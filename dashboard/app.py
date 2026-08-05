"""
app.py — Entry point della dashboard Streamlit.
Visualizza analisi statistiche dei tempi di attesa delle attrazioni di Disneyland Paris.
La dashboard è SOLO in lettura: non scrive mai sul database.

Sezioni:
- Attrazioni: tempi di attesa, heatmap, trend, confronto, single rider, premier access
- Show: orari delle performance e cambiamenti rilevati

Avvio: streamlit run dashboard/app.py
"""

import os
import streamlit as st
import psycopg2
import pandas as pd
from dotenv import load_dotenv
from datetime import date, timedelta

# Importa i moduli della dashboard
from queries import (
    get_overview_stats,
    get_attraction_list,
    get_sample_count,
    get_avg_by_hour,
    get_avg_by_day,
    get_heatmap_data,
    get_daily_trend,
    get_best_moments,
    get_comparison_data,
    get_single_rider_by_hour,
    get_premier_access_stats,
    get_premier_access_return_slots,
    get_single_rider_comparison,
    get_shows_list,
    get_show_times_by_name,
    get_show_changes,
    PA_TARGET_ATTRACTIONS,
    get_pa_slot_first_appearance,
    get_pa_availability_timeline,
    get_pa_slot_availability_pattern,
    get_pa_price_evolution,
    get_pa_daily_detail,
    get_current_avg_wait,
    get_crowd_history_range,
    calculate_crowd_percentage,
)
from charts import (
    bar_chart_by_hour,
    bar_chart_by_day,
    heatmap_chart,
    trend_line_chart,
    comparison_chart,
)
from planner import generate_daily_plan, get_plan_summary

# Nomi dei giorni della settimana in italiano
GIORNI_SETTIMANA = [
    "Lunedì", "Martedì", "Mercoledì", "Giovedì",
    "Venerdì", "Sabato", "Domenica"
]

# Carica le variabili d'ambiente dal file .env
load_dotenv()

# Configurazione della pagina Streamlit
st.set_page_config(
    page_title="🏰 Disneyland Paris — Wait Time Monitor",
    page_icon="🎢",
    layout="wide"
)


def _create_connection():
    """
    Crea una nuova connessione al database.
    Supporta sia .env locale che Streamlit Cloud secrets.
    """
    # Prima prova Streamlit secrets (per Streamlit Cloud)
    database_url = None
    try:
        database_url = st.secrets["DATABASE_URL"]
    except (KeyError, FileNotFoundError):
        pass
    
    # Fallback su variabile d'ambiente (per uso locale)
    if not database_url:
        database_url = os.environ.get("DATABASE_URL")
    
    if not database_url:
        st.error(
            "⚠️ Variabile DATABASE_URL non trovata. "
            "Configura i secrets su Streamlit Cloud o il file .env in locale."
        )
        st.stop()
    
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    
    try:
        conn = psycopg2.connect(database_url, connect_timeout=10)
        conn.autocommit = True
        # Mostra tutti gli orari in Europe/Paris
        with conn.cursor() as cur:
            cur.execute("SET timezone = 'Europe/Paris';")
        return conn
    except Exception as e:
        st.error(f"❌ Impossibile connettersi al database: {e}")
        st.stop()


@st.cache_resource
def get_db_connection():
    """Crea e cache-a la connessione, riconnettendo se chiusa."""
    return _create_connection()


def get_conn():
    """Restituisce una connessione valida, ricreandola se necessario."""
    conn = get_db_connection()
    try:
        # Test se la connessione è ancora viva
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn
    except Exception:
        # Connessione morta → svuota la cache e ricrea
        get_db_connection.clear()
        return get_db_connection()


def main():
    """Funzione principale della dashboard."""
    
    st.title("🏰 Disneyland Paris — Wait Time Monitor")
    st.markdown("Analisi statistica dei tempi di attesa basata su dati reali raccolti da themeparks.wiki")
    
    conn = get_conn()
    
    # --- BANNER AFFOLLAMENTO ---
    crowd_data = get_current_avg_wait(conn)
    if crowd_data["avg_wait_now"] is not None:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_paris = datetime.now(ZoneInfo("Europe/Paris"))
        history = get_crowd_history_range(conn, now_paris.weekday(), now_paris.hour)
        crowd = calculate_crowd_percentage(crowd_data["avg_wait_now"], history)
        
        # Banner colorato
        if crowd["crowd_pct"] <= 30:
            banner_color = "#d4edda"  # verde chiaro
            text_color = "#155724"
        elif crowd["crowd_pct"] <= 60:
            banner_color = "#fff3cd"  # giallo chiaro
            text_color = "#856404"
        elif crowd["crowd_pct"] <= 85:
            banner_color = "#ffe0cc"  # arancione chiaro
            text_color = "#cc5500"
        else:
            banner_color = "#f8d7da"  # rosso chiaro
            text_color = "#721c24"
        
        reliability_note = "" if crowd["reliable"] else " ⚠️ <i>(storico limitato — si auto-calibra col tempo)</i>"
        last_ts = crowd_data["last_sample"]
        last_str = last_ts.strftime("%H:%M") if hasattr(last_ts, 'strftime') else str(last_ts)[:5]
        
        st.markdown(
            f'<div style="background-color:{banner_color}; padding:12px 20px; border-radius:8px; '
            f'margin-bottom:16px; border-left:5px solid {text_color};">'
            f'<span style="font-size:1.3em;">{crowd["emoji"]} <b>Affollamento: {crowd["crowd_pct"]}%</b> '
            f'({crowd["livello"]})</span>'
            f'<span style="margin-left:20px; color:{text_color};">'
            f'Media attese: <b>{int(crowd_data["avg_wait_now"])} min</b> '
            f'su {crowd_data["num_attractions"]} attrazioni '
            f'| Aggiornato alle {last_str}'
            f'{reliability_note}</span></div>',
            unsafe_allow_html=True
        )
    else:
        st.info("📊 Affollamento: in attesa del primo campionamento dal poller...")
    
    # --- TABS PRINCIPALI ---
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "📊 Panoramica",
        "🎢 Analisi Attrazione",
        "📈 Trend Storico",
        "🔄 Confronto Attrazioni",
        "⚡ Single Rider & Premier Access",
        "🎯 PA Strategy",
        "🗺️ Planner Giornaliero",
        "🎭 Show & Spettacoli"
    ])
    
    # Recupera la lista attrazioni (solo ATTRACTION, no SHOW)
    attractions_df = get_attraction_list(conn)
    
    # --- SIDEBAR ---
    st.sidebar.header("🎯 Filtri Attrazioni")
    
    if not attractions_df.empty:
        # Filtro per parco
        parks_list = attractions_df["park"].unique().tolist()
        selected_park = st.sidebar.selectbox(
            "Seleziona parco",
            options=["Tutti"] + parks_list,
            index=0
        )
        
        # Filtra attrazioni per parco
        filtered_attractions = attractions_df.copy()
        if selected_park != "Tutti":
            filtered_attractions = filtered_attractions[filtered_attractions["park"] == selected_park]
        
        attraction_names = filtered_attractions["attraction_name"].tolist()
        
        if attraction_names:
            selected_name = st.sidebar.selectbox(
                "Seleziona attrazione",
                options=attraction_names,
                index=0
            )
            selected_row = filtered_attractions[filtered_attractions["attraction_name"] == selected_name].iloc[0]
            selected_id = selected_row["attraction_id"]
        else:
            selected_name = None
            selected_id = None
        
        # Filtro giorno della settimana
        day_filter_option = st.sidebar.selectbox(
            "Filtra per giorno (opzionale)",
            options=["Tutti"] + GIORNI_SETTIMANA,
            index=0
        )
        day_filter = None
        if day_filter_option != "Tutti":
            day_filter = GIORNI_SETTIMANA.index(day_filter_option)
        
        # Filtro range di date
        st.sidebar.subheader("📅 Range di date")
        use_date_filter = st.sidebar.checkbox("Filtra per date")
        date_range = None
        if use_date_filter:
            col1, col2 = st.sidebar.columns(2)
            with col1:
                start_date = st.date_input("Da", value=date.today() - timedelta(days=30))
            with col2:
                end_date = st.date_input("A", value=date.today())
            date_range = (start_date, end_date)
    else:
        selected_name = None
        selected_id = None
        attraction_names = []
        day_filter = None
        date_range = None
    
    # ===== TAB 1: PANORAMICA =====
    with tab1:
        st.header("Panoramica generale")
        
        if attractions_df.empty:
            st.warning(
                "📭 Nessun dato nel database. "
                "Il poller deve raccogliere almeno un ciclo di dati."
            )
        else:
            stats = get_overview_stats(conn)
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Campionamenti totali", f"{stats['total_samples']:,}")
            with col2:
                first = stats["first_sample"]
                st.metric("Primo campionamento", 
                         first.strftime("%d/%m/%Y %H:%M") if first else "N/A")
            with col3:
                last = stats["last_sample"]
                st.metric("Ultimo campionamento", 
                         last.strftime("%d/%m/%Y %H:%M") if last else "N/A")
            
            st.subheader("Riepilogo per attrazione")
            if not stats["summary_df"].empty:
                summary = stats["summary_df"].copy()
                summary.columns = [
                    "Attrazione", "Parco", "Tipo", "Campionamenti", 
                    "Attesa media (min)", "Single Rider media (min)", "Ultimo dato"
                ]
                st.dataframe(summary, use_container_width=True, hide_index=True)
    
    # ===== TAB 2: ANALISI ATTRAZIONE =====
    with tab2:
        if selected_name is None:
            st.info("Nessuna attrazione disponibile.")
        else:
            st.header(f"Analisi: {selected_name}")
            
            sample_count = get_sample_count(conn, selected_id)
            if sample_count < 100:
                st.warning(
                    f"⚠️ Dati insufficienti ({sample_count} campionamenti). "
                    f"Minimo consigliato: 100."
                )
            
            df_hour = get_avg_by_hour(conn, selected_id, day_filter=day_filter, date_range=date_range)
            fig_hour = bar_chart_by_hour(df_hour, f"Attesa media per ora — {selected_name}")
            st.plotly_chart(fig_hour, use_container_width=True, key="chart_hour")
            
            df_day = get_avg_by_day(conn, selected_id, date_range=date_range)
            fig_day = bar_chart_by_day(df_day, f"Attesa media per giorno — {selected_name}")
            st.plotly_chart(fig_day, use_container_width=True, key="chart_day")
            
            df_heatmap = get_heatmap_data(conn, selected_id, date_range=date_range)
            fig_heatmap = heatmap_chart(df_heatmap, f"Heatmap attesa — {selected_name}")
            st.plotly_chart(fig_heatmap, use_container_width=True, key="chart_heatmap")
            
            st.subheader("🌟 Migliori momenti per visitare")
            df_best = get_best_moments(conn, selected_id, date_range=date_range)
            if not df_best.empty:
                for _, row in df_best.iterrows():
                    hour = int(row["hour_of_day"])
                    wait = int(row["avg_wait"])
                    st.success(f"🕐 Ore {hour}:00 — attesa media: **{wait} minuti**")
            else:
                st.info("Dati insufficienti per calcolare i momenti migliori.")
    
    # ===== TAB 3: TREND STORICO =====
    with tab3:
        if selected_name is None:
            st.info("Nessuna attrazione disponibile.")
        else:
            st.header(f"Trend storico: {selected_name}")
            
            df_trend = get_daily_trend(conn, selected_id, date_range=date_range)
            fig_trend = trend_line_chart(df_trend, f"Andamento giornaliero — {selected_name}")
            st.plotly_chart(fig_trend, use_container_width=True, key="chart_trend")
            
            if not df_trend.empty:
                st.caption(
                    f"Periodo: {df_trend['day'].min()} → {df_trend['day'].max()} | "
                    f"Giorni con dati: {len(df_trend)}"
                )
    
    # ===== TAB 4: CONFRONTO ATTRAZIONI =====
    with tab4:
        st.header("Confronto attrazioni")
        
        if not attraction_names:
            st.info("Nessuna attrazione disponibile.")
        else:
            selected_for_comparison = st.multiselect(
                "Seleziona attrazioni da confrontare",
                options=attraction_names,
                default=attraction_names[:3] if len(attraction_names) >= 3 else attraction_names
            )
            
            if len(selected_for_comparison) < 2:
                st.info("👆 Seleziona almeno 2 attrazioni per il confronto.")
            else:
                comparison_ids = attractions_df[
                    attractions_df["attraction_name"].isin(selected_for_comparison)
                ]["attraction_id"].tolist()
                
                df_comparison = get_comparison_data(conn, comparison_ids, date_range=date_range)
                fig_comp = comparison_chart(df_comparison, "Confronto attesa media per ora del giorno")
                st.plotly_chart(fig_comp, use_container_width=True, key="chart_comparison")
    
    # ===== TAB 5: SINGLE RIDER & PREMIER ACCESS =====
    with tab5:
        st.header("⚡ Single Rider & Premier Access")
        
        if selected_name is None:
            st.info("Nessuna attrazione disponibile.")
        else:
            # --- Single Rider confronto globale ---
            st.subheader("🚶 Single Rider — Risparmio tempo")
            df_sr_comparison = get_single_rider_comparison(conn, date_range=date_range)
            if not df_sr_comparison.empty:
                st.dataframe(
                    df_sr_comparison.rename(columns={
                        "attraction_name": "Attrazione",
                        "avg_standby": "Standby (min)",
                        "avg_single_rider": "Single Rider (min)",
                        "risparmio_medio": "Risparmio (min)"
                    }),
                    use_container_width=True, hide_index=True
                )
            else:
                st.info("Nessun dato Single Rider disponibile ancora.")
            
            st.divider()
            
            # --- Single Rider per attrazione ---
            st.subheader(f"🚶 Single Rider per ora — {selected_name}")
            df_sr_hour = get_single_rider_by_hour(conn, selected_id, date_range=date_range)
            if not df_sr_hour.empty:
                import plotly.graph_objects as go
                fig_sr = go.Figure()
                fig_sr.add_trace(go.Bar(
                    x=df_sr_hour["hour_of_day"], y=df_sr_hour["avg_standby"],
                    name="Standby", marker_color="#FF6B6B"
                ))
                fig_sr.add_trace(go.Bar(
                    x=df_sr_hour["hour_of_day"], y=df_sr_hour["avg_single_rider"],
                    name="Single Rider", marker_color="#4ECDC4"
                ))
                fig_sr.update_layout(
                    title=f"Standby vs Single Rider — {selected_name}",
                    xaxis_title="Ora", yaxis_title="Attesa media (min)", barmode="group"
                )
                st.plotly_chart(fig_sr, use_container_width=True, key="chart_single_rider")
            else:
                st.info(f"'{selected_name}' non offre Single Rider o dati non ancora disponibili.")
            
            st.divider()
            
            # --- Premier Access ---
            st.subheader(f"💎 Premier Access — {selected_name}")
            df_pa = get_premier_access_stats(conn, selected_id, date_range=date_range)
            if not df_pa.empty and df_pa["avg_price"].notna().any():
                import plotly.express as px
                df_pa_display = df_pa.copy()
                df_pa_display["prezzo_euro"] = df_pa_display["avg_price"] / 100
                df_pa_display["disponibilità_%"] = (
                    df_pa_display["times_available"] / df_pa_display["total_samples"] * 100
                ).round(1)
                
                fig_pa = px.bar(
                    df_pa_display, x="hour_of_day", y="prezzo_euro",
                    title=f"Prezzo medio Premier Access — {selected_name}",
                    labels={"hour_of_day": "Ora", "prezzo_euro": "Prezzo medio (€)"},
                    color="prezzo_euro", color_continuous_scale="Blues"
                )
                st.plotly_chart(fig_pa, use_container_width=True, key="chart_premier_access")
                
                st.dataframe(
                    df_pa_display[["hour_of_day", "prezzo_euro", "disponibilità_%"]].rename(columns={
                        "hour_of_day": "Ora",
                        "prezzo_euro": "Prezzo medio (€)",
                        "disponibilità_%": "Disponibilità (%)"
                    }),
                    use_container_width=True, hide_index=True
                )
            else:
                st.info(f"'{selected_name}' non offre Premier Access o dati non disponibili.")
            
            st.divider()
            
            # --- Premier Access: Analisi Return Slot ---
            st.subheader(f"🎯 Premier Access Return Slot — {selected_name}")
            st.markdown(
                "Mostra **quale fascia oraria ti viene assegnata** in base a quando acquisti il Premier Access. "
                "Es: se compri alle 9:00, a che ora potrai entrare?"
            )
            
            df_slots = get_premier_access_return_slots(conn, selected_id, date_range=date_range)
            if not df_slots.empty:
                import plotly.graph_objects as go
                
                # Tabella dettagliata — più chiara di un grafico per orari
                st.markdown("**Se compri il Premier Access a quest'ora, ti assegnano questo slot:**")
                
                df_slots_display = df_slots.copy()
                df_slots_display["prezzo_euro"] = (df_slots_display["prezzo_medio"] / 100).round(2)
                df_slots_display["slot_assegnato"] = (
                    df_slots_display["return_start_medio"] + " – " + df_slots_display["return_end_medio"]
                )
                
                st.dataframe(
                    df_slots_display[["ora_acquisto", "slot_assegnato", "return_start_min", "return_start_max", "prezzo_euro", "campionamenti"]].rename(columns={
                        "ora_acquisto": "Ora acquisto",
                        "slot_assegnato": "Slot assegnato (media)",
                        "return_start_min": "Slot più presto",
                        "return_start_max": "Slot più tardi",
                        "prezzo_euro": "Prezzo (€)",
                        "campionamenti": "Campionamenti"
                    }),
                    use_container_width=True, hide_index=True
                )
                
                st.caption(
                    "💡 Più presto compri, prima è lo slot assegnato. "
                    "Se lo slot mostra ore tardi (es. 21:00), i PA delle ore precedenti sono già esauriti."
                )
            else:
                st.info("Dati return slot non ancora disponibili. Attendi più campionamenti.")
    
    # ===== TAB 6: PA STRATEGY =====
    with tab6:
        st.header("🎯 Premier Access Strategy")
        st.markdown(
            "Analisi dettagliata per capire **a che ora** diventano disponibili gli slot Premier Access "
            "delle attrazioni più ambite. Usa questi dati per sapere quando connetterti e comprare il PA."
        )
        st.markdown(f"**Attrazioni monitorate:** {', '.join(PA_TARGET_ATTRACTIONS)}")
        
        # --- FILTRO GIORNO DELLA SETTIMANA (specifico per questa tab) ---
        pa_day_option = st.selectbox(
            "📅 Filtra per giorno della settimana",
            options=["Tutti i giorni"] + GIORNI_SETTIMANA,
            index=0,
            key="pa_strategy_day_filter"
        )
        pa_day_filter = None
        if pa_day_option != "Tutti i giorni":
            pa_day_filter = GIORNI_SETTIMANA.index(pa_day_option)
        
        st.divider()
        
        # --- SEZIONE 1: Pattern medio — A che ora appare ogni slot ---
        st.subheader("📊 Pattern medio: quando appare ogni slot")
        st.markdown(
            "Per ogni attrazione e ogni fascia oraria di return, mostra l'ora media "
            "(e il range min-max) in cui quel slot è apparso per la prima volta. "
            "La colonna **Affollamento** indica la media attese del parco in quel momento."
        )
        
        df_pattern = get_pa_slot_availability_pattern(conn, date_range=date_range, day_filter=pa_day_filter)
        if not df_pattern.empty:
            for attr_name in PA_TARGET_ATTRACTIONS:
                df_attr = df_pattern[df_pattern["attraction_name"] == attr_name]
                if not df_attr.empty:
                    st.markdown(f"#### {attr_name}")
                    display_df = df_attr[["slot_ora", "ora_media_disponibilita", "ora_min_disponibilita", "ora_max_disponibilita", "affollamento_medio", "giorni_osservati"]].copy()
                    display_df.columns = ["Slot (ora return)", "Disponibile alle (media)", "Prima volta (min)", "Più tardi (max)", "Affollamento (min attesa)", "Giorni osservati"]
                    display_df["Slot (ora return)"] = display_df["Slot (ora return)"].apply(lambda h: f"{int(h)}:00 - {int(h)+1}:00")
                    st.dataframe(display_df, use_container_width=True, hide_index=True)
                    st.markdown("")
        else:
            st.info("📭 Nessun dato Premier Access disponibile ancora. Il poller deve raccogliere più campionamenti.")
        
        st.divider()
        
        # --- SEZIONE 2: Dettaglio per giorno — Prima apparizione di ogni slot ---
        st.subheader("📅 Dettaglio giornaliero: prima apparizione di ogni slot")
        st.markdown(
            "Mostra esattamente a che ora è apparso per la prima volta ogni slot in ogni giornata. "
            "La colonna **Affollamento** mostra la media attese del parco in quel momento."
        )
        
        df_first = get_pa_slot_first_appearance(conn, date_range=date_range, day_filter=pa_day_filter)
        if not df_first.empty:
            pa_attr_select = st.selectbox(
                "Seleziona attrazione",
                options=PA_TARGET_ATTRACTIONS,
                index=0,
                key="pa_strategy_attr"
            )
            
            df_attr_first = df_first[df_first["attraction_name"] == pa_attr_select].copy()
            if not df_attr_first.empty:
                # Mostra gli ultimi 7 giorni
                giorni_disponibili = sorted(df_attr_first["giorno"].unique(), reverse=True)
                
                for giorno in giorni_disponibili[:7]:
                    df_day = df_attr_first[df_attr_first["giorno"] == giorno].sort_values("return_start")
                    giorno_str = giorno.strftime("%A %d/%m/%Y") if hasattr(giorno, 'strftime') else str(giorno)
                    
                    # Indicatore affollamento del giorno
                    avg_crowd = df_day["affollamento"].mean()
                    crowd_emoji = "🟢" if avg_crowd and avg_crowd <= 20 else "🟡" if avg_crowd and avg_crowd <= 40 else "🟠" if avg_crowd and avg_crowd <= 60 else "🔴"
                    crowd_str = f" — {crowd_emoji} Affollamento medio: {int(avg_crowd)} min" if avg_crowd and not pd.isna(avg_crowd) else ""
                    
                    st.markdown(f"**{giorno_str}**{crowd_str}")
                    
                    display_day = df_day[["return_slot", "ora_apparizione", "prezzo", "affollamento"]].copy()
                    display_day.columns = ["Slot Return", "Apparso alle", "Prezzo (cent)", "Affollamento (min)"]
                    display_day["Prezzo (€)"] = (display_day["Prezzo (cent)"] / 100).round(2)
                    display_day = display_day[["Slot Return", "Apparso alle", "Prezzo (€)", "Affollamento (min)"]]
                    st.dataframe(display_day, use_container_width=True, hide_index=True)
                    st.markdown("")
            else:
                st.info(f"Nessun dato PA disponibile per {pa_attr_select}.")
        else:
            st.info("📭 Dati insufficienti. Attendi più campionamenti dal poller.")
        
        st.divider()
        
        # --- SEZIONE 3: Evoluzione prezzo durante la giornata ---
        st.subheader("💰 Evoluzione prezzo durante la giornata")
        st.markdown("Come varia il prezzo del Premier Access ora per ora. Include correlazione con affollamento.")
        
        pa_price_attr = st.selectbox(
            "Seleziona attrazione",
            options=PA_TARGET_ATTRACTIONS,
            index=0,
            key="pa_price_attr"
        )
        
        df_price = get_pa_price_evolution(conn, pa_price_attr, date_range=date_range, day_filter=pa_day_filter)
        if not df_price.empty:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
            
            df_price_display = df_price.copy()
            df_price_display["prezzo_medio_eur"] = df_price_display["prezzo_medio"] / 100
            df_price_display["prezzo_min_eur"] = df_price_display["prezzo_min"] / 100
            df_price_display["prezzo_max_eur"] = df_price_display["prezzo_max"] / 100
            
            # Grafico doppio asse: prezzo + affollamento
            fig_price = make_subplots(specs=[[{"secondary_y": True}]])
            
            fig_price.add_trace(go.Scatter(
                x=df_price_display["hour_of_day"],
                y=df_price_display["prezzo_max_eur"],
                mode="lines",
                name="Max",
                line=dict(width=0),
                showlegend=False
            ), secondary_y=False)
            fig_price.add_trace(go.Scatter(
                x=df_price_display["hour_of_day"],
                y=df_price_display["prezzo_min_eur"],
                mode="lines",
                name="Range prezzo",
                fill="tonexty",
                fillcolor="rgba(255, 107, 107, 0.2)",
                line=dict(width=0)
            ), secondary_y=False)
            fig_price.add_trace(go.Scatter(
                x=df_price_display["hour_of_day"],
                y=df_price_display["prezzo_medio_eur"],
                mode="lines+markers",
                name="Prezzo medio (€)",
                line=dict(color="#FF6B6B", width=3)
            ), secondary_y=False)
            
            # Affollamento sull'asse secondario
            if "affollamento_medio" in df_price_display.columns and df_price_display["affollamento_medio"].notna().any():
                fig_price.add_trace(go.Bar(
                    x=df_price_display["hour_of_day"],
                    y=df_price_display["affollamento_medio"],
                    name="Affollamento (min attesa)",
                    marker_color="rgba(100, 149, 237, 0.3)",
                    width=0.6
                ), secondary_y=True)
            
            fig_price.update_layout(
                title=f"Prezzo PA + Affollamento — {pa_price_attr}",
                xaxis_title="Ora del giorno",
                xaxis=dict(dtick=1),
                legend=dict(orientation="h", yanchor="bottom", y=1.02)
            )
            fig_price.update_yaxes(title_text="Prezzo (€)", secondary_y=False)
            fig_price.update_yaxes(title_text="Affollamento (min attesa media)", secondary_y=True)
            
            st.plotly_chart(fig_price, use_container_width=True, key="chart_pa_price_evo")
        else:
            st.info(f"Nessun dato prezzo per {pa_price_attr}.")
        
        st.divider()
        
        # --- SEZIONE 4: Timeline completa di un giorno specifico ---
        st.subheader("🔬 Analisi dettagliata di un giorno specifico")
        st.markdown("Seleziona un'attrazione e una data per vedere TUTTI i campionamenti PA con affollamento.")
        
        col1, col2 = st.columns(2)
        with col1:
            pa_detail_attr = st.selectbox(
                "Attrazione",
                options=PA_TARGET_ATTRACTIONS,
                index=0,
                key="pa_detail_attr"
            )
        with col2:
            pa_detail_date = st.date_input(
                "Data",
                value=date.today() - timedelta(days=1),
                key="pa_detail_date"
            )
        
        df_detail = get_pa_daily_detail(conn, pa_detail_attr, str(pa_detail_date))
        if not df_detail.empty:
            df_detail_display = df_detail.copy()
            df_detail_display["prezzo_eur"] = (df_detail_display["prezzo"] / 100).round(2)
            df_detail_display["slot"] = df_detail_display["slot_inizio"] + " - " + df_detail_display["slot_fine"]
            
            final_display = df_detail_display[["ora", "stato", "slot", "prezzo_eur", "affollamento"]].copy()
            final_display.columns = ["Ora", "Stato", "Slot Return", "Prezzo (€)", "Affollamento (min)"]
            st.dataframe(final_display, use_container_width=True, hide_index=True)
            
            # Evidenzia info chiave
            available_rows = df_detail_display[df_detail_display["stato"] == "AVAILABLE"]
            if not available_rows.empty:
                first_available = available_rows.iloc[0]["ora"]
                last_available = available_rows.iloc[-1]["ora"]
                avg_crowd_day = available_rows["affollamento"].mean()
                crowd_note = f" | Affollamento medio: **{int(avg_crowd_day)} min**" if avg_crowd_day and not pd.isna(avg_crowd_day) else ""
                st.success(
                    f"✅ PA disponibile dalle **{first_available}** alle **{last_available}** | "
                    f"Slot iniziale: **{available_rows.iloc[0]['slot']}** → "
                    f"Slot finale: **{available_rows.iloc[-1]['slot']}**"
                    f"{crowd_note}"
                )
            
            finished_rows = df_detail_display[df_detail_display["stato"] == "FINISHED"]
            if not finished_rows.empty:
                first_finished = finished_rows.iloc[0]["ora"]
                st.error(f"❌ PA esaurito alle **{first_finished}**")
        else:
            st.info(f"Nessun dato per {pa_detail_attr} il {pa_detail_date}. Potrebbe non esserci stato campionamento.")

    # ===== TAB 7: PLANNER GIORNALIERO =====
    with tab7:
        st.header("🗺️ Planner Giornaliero")
        st.markdown(
            "Seleziona le attrazioni che vuoi fare e il giorno della settimana. "
            "Il planner calcolerà l'ordine ottimale per minimizzare il tempo in coda."
        )
        
        if not attraction_names:
            st.info("Nessuna attrazione disponibile.")
        else:
            # Selezione attrazioni per il piano
            plan_attractions = st.multiselect(
                "Attrazioni da includere nel piano",
                options=attraction_names,
                default=attraction_names[:8] if len(attraction_names) >= 8 else attraction_names,
                key="planner_multiselect"
            )
            
            # Selezione giorno
            plan_day_option = st.selectbox(
                "Giorno della visita",
                options=["Media generale"] + GIORNI_SETTIMANA,
                index=0,
                key="planner_day"
            )
            plan_day = None
            if plan_day_option != "Media generale":
                plan_day = GIORNI_SETTIMANA.index(plan_day_option)
            
            if len(plan_attractions) < 2:
                st.info("👆 Seleziona almeno 2 attrazioni per generare il piano.")
            else:
                # Recupera gli ID
                plan_ids = attractions_df[
                    attractions_df["attraction_name"].isin(plan_attractions)
                ]["attraction_id"].tolist()
                
                # Genera il piano
                plan_df = generate_daily_plan(
                    conn, plan_ids, 
                    park_hours=(9, 22),
                    day_filter=plan_day, 
                    date_range=date_range
                )
                
                if not plan_df.empty:
                    summary = get_plan_summary(plan_df)
                    
                    # Metriche riepilogative
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Attrazioni pianificate", summary["num_attractions"])
                    with col2:
                        st.metric("Tempo totale stimato in coda", f"{summary['total_wait']} min")
                    with col3:
                        st.metric("Tempo risparmiato vs media", f"{summary['total_saving']} min")
                    
                    st.divider()
                    
                    # Piano dettagliato
                    st.subheader("📋 Il tuo piano ottimale")
                    for _, row in plan_df.iterrows():
                        hour = int(row["hour"])
                        saving = int(row["saving"])
                        emoji = "🟢" if saving > 10 else "🟡" if saving > 0 else "🔴"
                        st.markdown(
                            f"{emoji} **{hour}:00** — {row['attraction_name']} "
                            f"| Attesa stimata: **{int(row['avg_wait'])} min** "
                            f"(media generale: {int(row['avg_general'])} min, "
                            f"risparmio: {saving} min)"
                        )
                    
                    st.divider()
                    st.caption(
                        "💡 Il piano assegna ogni attrazione alla fascia oraria dove storicamente "
                        "la coda è più bassa. Più dati accumuli, più il piano sarà accurato."
                    )
                else:
                    st.warning(
                        "⚠️ Dati insufficienti per generare un piano. "
                        "Il poller deve raccogliere più campionamenti."
                    )
    
    # ===== TAB 8: SHOW & SPETTACOLI =====
    with tab8:
        st.header("🎭 Show & Spettacoli")
        st.markdown("Orari delle performance e cambiamenti rilevati nel tempo.")
        
        # --- Cambiamenti recenti ---
        st.subheader("🔔 Cambiamenti orari recenti")
        df_changes = get_show_changes(conn, limit=20)
        if not df_changes.empty:
            for _, row in df_changes.iterrows():
                detected = row["detected_at"]
                detected_str = detected.strftime("%d/%m/%Y %H:%M") if detected else ""
                st.warning(
                    f"**{row['show_name']}** ({row['park']}) — {detected_str}\n\n"
                    f"Vecchi orari: `{row['old_times']}`\n\n"
                    f"Nuovi orari: `{row['new_times']}`"
                )
        else:
            st.success("✅ Nessun cambiamento rilevato finora. Gli orari sono stabili.")
        
        st.divider()
        
        # --- Lista show e orari ---
        st.subheader("📋 Orari degli spettacoli")
        
        shows_df = get_shows_list(conn)
        if not shows_df.empty:
            # Filtro per parco nella sezione show
            show_parks = shows_df["park"].unique().tolist()
            show_park_filter = st.selectbox(
                "Filtra per parco (show)",
                options=["Tutti"] + show_parks,
                index=0,
                key="show_park_filter"
            )
            
            filtered_shows = shows_df
            if show_park_filter != "Tutti":
                filtered_shows = shows_df[shows_df["park"] == show_park_filter]
            
            show_names = filtered_shows["show_name"].tolist()
            selected_show = st.selectbox(
                "Seleziona show",
                options=show_names,
                index=0,
                key="show_selector"
            )
            
            if selected_show:
                df_show_times = get_show_times_by_name(conn, selected_show)
                if not df_show_times.empty:
                    # Raggruppiamo per data
                    dates = df_show_times["performance_date"].unique()
                    for perf_date in sorted(dates, reverse=True)[:7]:  # Ultimi 7 giorni
                        day_data = df_show_times[df_show_times["performance_date"] == perf_date]
                        times_list = [t.strftime("%H:%M") for t in day_data["performance_time"]]
                        
                        date_str = perf_date.strftime("%A %d/%m/%Y") if hasattr(perf_date, 'strftime') else str(perf_date)
                        st.markdown(f"**{date_str}**")
                        st.markdown(" · ".join([f"`{t}`" for t in times_list]))
                        st.markdown("---")
                else:
                    st.info("Nessun orario disponibile per questo show.")
        else:
            st.info("📭 Nessuno show registrato nel database. Attendi il prossimo ciclo del poller.")


if __name__ == "__main__":
    main()
