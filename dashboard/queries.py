"""
queries.py — Query SQL per le aggregazioni statistiche della dashboard.
Tutte le funzioni restituiscono pandas DataFrame pronti per i grafici.
La dashboard è SOLO in lettura: nessuna operazione di scrittura.

Schema DB aggiornato:
- attraction_id, attraction_name, entity_type, park, external_id
- wait_minutes, single_rider_minutes, premier_access_price, premier_access_currency
- status, sampled_at, day_of_week, hour_of_day
"""

import pandas as pd


# === QUERY: Indicatore di affollamento ===

# Floor e cap di default per i primi giorni (quando lo storico è scarso)
CROWD_FLOOR_MINUTES = 5    # media attese minima "teorica"
CROWD_CAP_MINUTES = 75     # media attese massima "teorica" — verrà sostituita dal max reale
CROWD_MIN_SAMPLES_FOR_HISTORY = 50  # campionamenti minimi per fidarsi dello storico


def get_current_avg_wait(conn) -> dict:
    """
    Calcola la media attese di TUTTE le attrazioni OPERATIVE in questo momento
    (ultimo campionamento disponibile, entro gli ultimi 30 minuti).
    Restituisce: media attese attuale, numero attrazioni operative, timestamp.
    """
    query = """
        WITH latest AS (
            SELECT MAX(sampled_at) as last_ts
            FROM wait_times
            WHERE status = 'OPERATING' AND wait_minutes IS NOT NULL
        )
        SELECT 
            ROUND(AVG(wt.wait_minutes)) as avg_wait_now,
            COUNT(DISTINCT wt.attraction_id) as num_attractions,
            MAX(wt.sampled_at AT TIME ZONE 'Europe/Paris') as last_sample
        FROM wait_times wt, latest
        WHERE wt.sampled_at >= latest.last_ts - INTERVAL '30 minutes'
          AND wt.status = 'OPERATING'
          AND wt.wait_minutes IS NOT NULL
          AND wt.entity_type = 'ATTRACTION';
    """
    df = pd.read_sql(query, conn)
    if df.empty or df["avg_wait_now"].iloc[0] is None:
        return {"avg_wait_now": None, "num_attractions": 0, "last_sample": None}
    return {
        "avg_wait_now": float(df["avg_wait_now"].iloc[0]),
        "num_attractions": int(df["num_attractions"].iloc[0]),
        "last_sample": df["last_sample"].iloc[0],
    }


def get_crowd_history_range(conn, day_of_week: int, hour_of_day: int) -> dict:
    """
    Recupera min e max storici della media attese per la stessa fascia
    giorno-della-settimana + ora.
    Restituisce: min_storico, max_storico, media_storica, campionamenti.
    """
    query = """
        WITH hourly_avgs AS (
            SELECT 
                DATE(sampled_at) as giorno,
                ROUND(AVG(wait_minutes)) as avg_wait
            FROM wait_times
            WHERE day_of_week = %s
              AND hour_of_day = %s
              AND status = 'OPERATING'
              AND wait_minutes IS NOT NULL
              AND entity_type = 'ATTRACTION'
            GROUP BY DATE(sampled_at)
        )
        SELECT 
            MIN(avg_wait) as min_storico,
            MAX(avg_wait) as max_storico,
            ROUND(AVG(avg_wait)) as media_storica,
            COUNT(*) as giorni_campionati
        FROM hourly_avgs;
    """
    df = pd.read_sql(query, conn, params=(day_of_week, hour_of_day))
    if df.empty or df["giorni_campionati"].iloc[0] == 0:
        return {"min_storico": None, "max_storico": None, "media_storica": None, "giorni_campionati": 0}
    return {
        "min_storico": float(df["min_storico"].iloc[0]),
        "max_storico": float(df["max_storico"].iloc[0]),
        "media_storica": float(df["media_storica"].iloc[0]),
        "giorni_campionati": int(df["giorni_campionati"].iloc[0]),
    }


def calculate_crowd_percentage(avg_wait_now: float, history: dict) -> dict:
    """
    Calcola la percentuale di affollamento normalizzata.
    Usa floor/cap di default se lo storico è insufficiente.
    
    Returns: dict con crowd_pct (0-100+), livello (basso/medio/alto/estremo),
             emoji, affidabilità dello storico.
    """
    giorni = history.get("giorni_campionati", 0)
    
    # Determina min e max da usare
    if giorni >= CROWD_MIN_SAMPLES_FOR_HISTORY and history["min_storico"] is not None:
        min_val = history["min_storico"]
        max_val = history["max_storico"]
        reliable = True
    else:
        min_val = CROWD_FLOOR_MINUTES
        max_val = CROWD_CAP_MINUTES
        reliable = False
    
    # Evita divisione per zero
    range_val = max_val - min_val
    if range_val <= 0:
        range_val = 1
    
    # Calcolo percentuale
    crowd_pct = ((avg_wait_now - min_val) / range_val) * 100
    crowd_pct = max(0, crowd_pct)  # non scende sotto 0
    # Può superare 100 se oggi è un nuovo record
    
    # Livello
    if crowd_pct <= 30:
        livello = "Basso"
        emoji = "🟢"
    elif crowd_pct <= 60:
        livello = "Medio"
        emoji = "🟡"
    elif crowd_pct <= 85:
        livello = "Alto"
        emoji = "🟠"
    else:
        livello = "Estremo"
        emoji = "🔴"
    
    return {
        "crowd_pct": round(crowd_pct, 1),
        "livello": livello,
        "emoji": emoji,
        "reliable": reliable,
        "giorni_campionati": giorni,
        "min_used": min_val,
        "max_used": max_val,
    }


def get_overview_stats(conn) -> dict:
    """
    Recupera le statistiche generali di panoramica:
    - Totale campionamenti
    - Data primo e ultimo campionamento
    - Tabella riepilogativa per attrazione
    """
    # Totale campionamenti e date estreme (convertite a Europe/Paris)
    query_totals = """
        SELECT 
            COUNT(*) as total_samples,
            MIN(sampled_at AT TIME ZONE 'Europe/Paris') as first_sample,
            MAX(sampled_at AT TIME ZONE 'Europe/Paris') as last_sample
        FROM wait_times;
    """
    df_totals = pd.read_sql(query_totals, conn)
    
    # Tabella riepilogativa per attrazione (solo ATTRACTION, escluse SHOW)
    query_summary = """
        SELECT 
            attraction_name,
            park,
            entity_type,
            COUNT(*) as num_samples,
            ROUND(AVG(wait_minutes)) as avg_wait,
            ROUND(AVG(single_rider_minutes)) as avg_single_rider,
            MAX(sampled_at AT TIME ZONE 'Europe/Paris') as last_sample
        FROM wait_times
        WHERE status = 'OPERATING'
        GROUP BY attraction_name, park, entity_type
        ORDER BY park, attraction_name;
    """
    df_summary = pd.read_sql(query_summary, conn)
    
    return {
        "total_samples": int(df_totals["total_samples"].iloc[0]) if not df_totals.empty else 0,
        "first_sample": df_totals["first_sample"].iloc[0] if not df_totals.empty else None,
        "last_sample": df_totals["last_sample"].iloc[0] if not df_totals.empty else None,
        "summary_df": df_summary
    }


def get_attraction_list(conn) -> pd.DataFrame:
    """
    Recupera la lista di tutte le ATTRACTION monitorate nel database.
    Esclude gli SHOW (gestiti separatamente).
    """
    query = """
        SELECT DISTINCT attraction_id, attraction_name, park, entity_type
        FROM wait_times
        WHERE entity_type = 'ATTRACTION'
        ORDER BY park, attraction_name;
    """
    return pd.read_sql(query, conn)


def get_sample_count(conn, attraction_id: str) -> int:
    """
    Conta i campionamenti per una specifica attrazione.
    Usato per il controllo 'dati insufficienti'.
    """
    query = """
        SELECT COUNT(*) as cnt
        FROM wait_times
        WHERE attraction_id = %s AND status = 'OPERATING';
    """
    df = pd.read_sql(query, conn, params=(attraction_id,))
    return int(df["cnt"].iloc[0]) if not df.empty else 0


def get_avg_by_hour(conn, attraction_id: str, day_filter=None, date_range=None) -> pd.DataFrame:
    """
    Media tempo di attesa per ora del giorno.
    Esclude status REFURBISHMENT e CLOSED.
    """
    query = """
        SELECT hour_of_day, ROUND(AVG(wait_minutes)) as avg_wait
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND wait_minutes IS NOT NULL
    """
    params = [attraction_id]
    
    if day_filter is not None:
        query += " AND day_of_week = %s"
        params.append(day_filter)
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY hour_of_day ORDER BY hour_of_day;"
    
    return pd.read_sql(query, conn, params=params)


def get_avg_by_day(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Media tempo di attesa per giorno della settimana.
    """
    query = """
        SELECT day_of_week, ROUND(AVG(wait_minutes)) as avg_wait
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND wait_minutes IS NOT NULL
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY day_of_week ORDER BY day_of_week;"
    
    return pd.read_sql(query, conn, params=params)


def get_heatmap_data(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Dati per la heatmap: giorno della settimana × ora del giorno.
    """
    query = """
        SELECT day_of_week, hour_of_day, ROUND(AVG(wait_minutes)) as avg_wait
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND wait_minutes IS NOT NULL
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY day_of_week, hour_of_day ORDER BY day_of_week, hour_of_day;"
    
    return pd.read_sql(query, conn, params=params)


def get_daily_trend(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Trend storico: media giornaliera nel tempo.
    """
    query = """
        SELECT DATE(sampled_at) as day, ROUND(AVG(wait_minutes)) as avg_wait
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND wait_minutes IS NOT NULL
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY DATE(sampled_at) ORDER BY day;"
    
    return pd.read_sql(query, conn, params=params)


def get_best_moments(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Top 3 fasce orarie con la coda media più bassa.
    """
    query = """
        SELECT hour_of_day, ROUND(AVG(wait_minutes)) as avg_wait
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND wait_minutes IS NOT NULL
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY hour_of_day ORDER BY avg_wait ASC LIMIT 3;"
    
    return pd.read_sql(query, conn, params=params)


def get_comparison_data(conn, attraction_ids: list, date_range=None) -> pd.DataFrame:
    """
    Dati per il confronto tra attrazioni: media per ora del giorno.
    """
    if not attraction_ids:
        return pd.DataFrame()
    
    placeholders = ",".join(["%s"] * len(attraction_ids))
    
    query = f"""
        SELECT attraction_name, hour_of_day, ROUND(AVG(wait_minutes)) as avg_wait
        FROM wait_times
        WHERE attraction_id IN ({placeholders})
          AND status = 'OPERATING'
          AND wait_minutes IS NOT NULL
    """
    params = list(attraction_ids)
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY attraction_name, hour_of_day ORDER BY attraction_name, hour_of_day;"
    
    return pd.read_sql(query, conn, params=params)


# === NUOVE QUERY: Single Rider e Premier Access ===


def get_single_rider_by_hour(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Media tempo Single Rider per ora del giorno.
    Solo per attrazioni che offrono Single Rider.
    """
    query = """
        SELECT hour_of_day, 
               ROUND(AVG(wait_minutes)) as avg_standby,
               ROUND(AVG(single_rider_minutes)) as avg_single_rider
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND single_rider_minutes IS NOT NULL
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY hour_of_day ORDER BY hour_of_day;"
    
    return pd.read_sql(query, conn, params=params)


def get_premier_access_stats(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Statistiche Premier Access: prezzo medio e disponibilità per ora.
    Il prezzo è in centesimi (es. 1300 = 13.00€).
    """
    query = """
        SELECT hour_of_day,
               ROUND(AVG(premier_access_price)) as avg_price,
               COUNT(premier_access_price) as times_available,
               COUNT(*) as total_samples
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY hour_of_day ORDER BY hour_of_day;"
    
    return pd.read_sql(query, conn, params=params)


def get_premier_access_availability(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Analisi disponibilità Premier Access nel tempo:
    - A che ora del giorno sono ancora AVAILABLE
    - A che ora diventano FINISHED (esauriti)
    - Fascia oraria media del return time offerto per ogni ora di campionamento
    
    Utile per capire: "se compro il PA alle 9, che fascia oraria mi danno?"
    """
    query = """
        SELECT 
            hour_of_day,
            premier_access_state,
            COUNT(*) as occurrences,
            ROUND(AVG(EXTRACT(HOUR FROM premier_access_return_start))) as avg_return_hour
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND premier_access_state IS NOT NULL
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY hour_of_day, premier_access_state ORDER BY hour_of_day;"
    
    return pd.read_sql(query, conn, params=params)


def get_premier_access_return_slots(conn, attraction_id: str, date_range=None) -> pd.DataFrame:
    """
    Mostra la fascia oraria del return time offerta per ogni ora di acquisto.
    Es: "Se compro alle 9:00, mi danno slot 14:30-15:30"
        "Se compro alle 12:00, mi danno slot 19:10-20:10"
    
    Fondamentale per pianificare quando prenotare.
    Mostra ora:minuti completa, non solo l'ora.
    """
    query = """
        SELECT 
            hour_of_day as ora_acquisto,
            TO_CHAR(AVG(premier_access_return_start::time), 'HH24:MI') as return_start_medio,
            TO_CHAR(MIN(premier_access_return_start::time), 'HH24:MI') as return_start_min,
            TO_CHAR(MAX(premier_access_return_start::time), 'HH24:MI') as return_start_max,
            TO_CHAR(AVG(premier_access_return_end::time), 'HH24:MI') as return_end_medio,
            ROUND(AVG(premier_access_price)) as prezzo_medio,
            COUNT(*) as campionamenti
        FROM wait_times
        WHERE attraction_id = %s 
          AND status = 'OPERATING'
          AND premier_access_state = 'AVAILABLE'
          AND premier_access_return_start IS NOT NULL
    """
    params = [attraction_id]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY hour_of_day ORDER BY hour_of_day;"
    
    return pd.read_sql(query, conn, params=params)


def get_single_rider_comparison(conn, date_range=None) -> pd.DataFrame:
    """
    Confronto risparmio Single Rider vs Standby per tutte le attrazioni
    che offrono Single Rider.
    """
    query = """
        SELECT attraction_name,
               ROUND(AVG(wait_minutes)) as avg_standby,
               ROUND(AVG(single_rider_minutes)) as avg_single_rider,
               ROUND(AVG(wait_minutes) - AVG(single_rider_minutes)) as risparmio_medio
        FROM wait_times
        WHERE status = 'OPERATING'
          AND single_rider_minutes IS NOT NULL
          AND wait_minutes IS NOT NULL
    """
    params = []
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " GROUP BY attraction_name ORDER BY risparmio_medio DESC;"
    
    return pd.read_sql(query, conn, params=params)


# === QUERY: Analisi dettagliata Premier Access per attrazioni target ===

# Le 4 attrazioni target per l'analisi PA dettagliata
PA_TARGET_ATTRACTIONS = [
    "Crush's Coaster",
    "Big Thunder Mountain",
    "Frozen Ever After",
    "Star Wars Hyperspace Mountain",
]


def get_pa_slot_first_appearance(conn, date_range=None, day_filter=None) -> pd.DataFrame:
    """
    Per ogni giornata e ogni attrazione target, trova la PRIMA volta (sampled_at)
    in cui ogni fascia oraria di return (es. 17:00-18:00) è apparsa come AVAILABLE.
    Include l'indice di affollamento (media attese di tutte le attrazioni nello stesso ciclo).
    
    Restituisce: attraction_name, giorno, day_of_week, return_slot, 
                 ora_apparizione, prezzo, affollamento.
    """
    placeholders = ",".join(["%s"] * len(PA_TARGET_ATTRACTIONS))
    
    query = f"""
        WITH cycle_crowd AS (
            SELECT 
                DATE(sampled_at) as giorno,
                hour_of_day,
                ROUND(AVG(wait_minutes)) as crowd_avg
            FROM wait_times
            WHERE status = 'OPERATING'
              AND wait_minutes IS NOT NULL
              AND entity_type = 'ATTRACTION'
            GROUP BY DATE(sampled_at), hour_of_day
        ),
        slot_appearances AS (
            SELECT 
                wt.attraction_name,
                DATE(wt.sampled_at) as giorno,
                wt.day_of_week,
                TO_CHAR(wt.premier_access_return_start AT TIME ZONE 'Europe/Paris', 'HH24:MI') as return_start,
                TO_CHAR(wt.premier_access_return_end AT TIME ZONE 'Europe/Paris', 'HH24:MI') as return_end,
                wt.sampled_at,
                wt.premier_access_price,
                wt.hour_of_day,
                ROW_NUMBER() OVER (
                    PARTITION BY wt.attraction_name, DATE(wt.sampled_at), 
                    TO_CHAR(wt.premier_access_return_start AT TIME ZONE 'Europe/Paris', 'HH24:MI')
                    ORDER BY wt.sampled_at ASC
                ) as rn
            FROM wait_times wt
            WHERE wt.attraction_name IN ({placeholders})
              AND wt.premier_access_state = 'AVAILABLE'
              AND wt.premier_access_return_start IS NOT NULL
              AND wt.status = 'OPERATING'
    """
    params = list(PA_TARGET_ATTRACTIONS)
    
    if date_range is not None:
        query += " AND wt.sampled_at >= %s AND wt.sampled_at <= %s"
        params.extend(date_range)
    
    if day_filter is not None:
        query += " AND wt.day_of_week = %s"
        params.append(day_filter)
    
    query += """
        )
        SELECT 
            sa.attraction_name,
            sa.giorno,
            sa.day_of_week,
            sa.return_start || ' - ' || sa.return_end as return_slot,
            sa.return_start,
            TO_CHAR(sa.sampled_at AT TIME ZONE 'Europe/Paris', 'HH24:MI') as ora_apparizione,
            sa.premier_access_price as prezzo,
            cc.crowd_avg as affollamento
        FROM slot_appearances sa
        LEFT JOIN cycle_crowd cc ON cc.giorno = sa.giorno AND cc.hour_of_day = sa.hour_of_day
        WHERE sa.rn = 1
        ORDER BY sa.attraction_name, sa.giorno DESC, sa.return_start;
    """
    
    return pd.read_sql(query, conn, params=params)


def get_pa_availability_timeline(conn, attraction_name: str, date_range=None) -> pd.DataFrame:
    """
    Per una singola attrazione target, mostra l'evoluzione del PA durante la giornata:
    - A ogni campionamento, quale slot viene offerto, a che prezzo, e lo stato.
    
    Utile per vedere la timeline completa: come gli slot si muovono durante il giorno.
    """
    query = """
        SELECT 
            DATE(sampled_at) as giorno,
            TO_CHAR(sampled_at AT TIME ZONE 'Europe/Paris', 'HH24:MI') as ora_campionamento,
            sampled_at,
            premier_access_state as stato,
            TO_CHAR(premier_access_return_start AT TIME ZONE 'Europe/Paris', 'HH24:MI') as slot_inizio,
            TO_CHAR(premier_access_return_end AT TIME ZONE 'Europe/Paris', 'HH24:MI') as slot_fine,
            premier_access_price as prezzo
        FROM wait_times
        WHERE attraction_name = %s
          AND status = 'OPERATING'
          AND premier_access_state IS NOT NULL
    """
    params = [attraction_name]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    query += " ORDER BY sampled_at;"
    
    return pd.read_sql(query, conn, params=params)


def get_pa_slot_availability_pattern(conn, date_range=None, day_filter=None) -> pd.DataFrame:
    """
    Pattern medio: per ogni attrazione target e ogni fascia return slot,
    calcola l'ORA MEDIA in cui quel slot diventa disponibile per la prima volta.
    Include l'affollamento medio nel momento in cui lo slot appare.
    
    Es: "Il PA per le 17:00 di Crush's Coaster in media appare alle 9:23"
    """
    placeholders = ",".join(["%s"] * len(PA_TARGET_ATTRACTIONS))
    
    query = f"""
        WITH cycle_crowd AS (
            SELECT 
                DATE(sampled_at) as giorno,
                hour_of_day,
                ROUND(AVG(wait_minutes)) as crowd_avg
            FROM wait_times
            WHERE status = 'OPERATING'
              AND wait_minutes IS NOT NULL
              AND entity_type = 'ATTRACTION'
            GROUP BY DATE(sampled_at), hour_of_day
        ),
        first_appearances AS (
            SELECT 
                wt.attraction_name,
                DATE(wt.sampled_at) as giorno,
                wt.day_of_week,
                EXTRACT(HOUR FROM wt.premier_access_return_start AT TIME ZONE 'Europe/Paris') as return_hour,
                MIN(wt.sampled_at) as first_seen,
                (EXTRACT(HOUR FROM MIN(wt.sampled_at) AT TIME ZONE 'Europe/Paris'))::int as first_seen_hour
            FROM wait_times wt
            WHERE wt.attraction_name IN ({placeholders})
              AND wt.premier_access_state = 'AVAILABLE'
              AND wt.premier_access_return_start IS NOT NULL
              AND wt.status = 'OPERATING'
    """
    params = list(PA_TARGET_ATTRACTIONS)
    
    if date_range is not None:
        query += " AND wt.sampled_at >= %s AND wt.sampled_at <= %s"
        params.extend(date_range)
    
    if day_filter is not None:
        query += " AND wt.day_of_week = %s"
        params.append(day_filter)
    
    query += f"""
            GROUP BY wt.attraction_name, DATE(wt.sampled_at), wt.day_of_week,
                     EXTRACT(HOUR FROM wt.premier_access_return_start AT TIME ZONE 'Europe/Paris')
        )
        SELECT 
            fa.attraction_name,
            fa.return_hour::int as slot_ora,
            TO_CHAR(
                MAKE_INTERVAL(secs => AVG(EXTRACT(HOUR FROM fa.first_seen AT TIME ZONE 'Europe/Paris') * 3600 
                    + EXTRACT(MINUTE FROM fa.first_seen AT TIME ZONE 'Europe/Paris') * 60)),
                'HH24:MI'
            ) as ora_media_disponibilita,
            TO_CHAR(
                MAKE_INTERVAL(secs => MIN(EXTRACT(HOUR FROM fa.first_seen AT TIME ZONE 'Europe/Paris') * 3600 
                    + EXTRACT(MINUTE FROM fa.first_seen AT TIME ZONE 'Europe/Paris') * 60)),
                'HH24:MI'
            ) as ora_min_disponibilita,
            TO_CHAR(
                MAKE_INTERVAL(secs => MAX(EXTRACT(HOUR FROM fa.first_seen AT TIME ZONE 'Europe/Paris') * 3600 
                    + EXTRACT(MINUTE FROM fa.first_seen AT TIME ZONE 'Europe/Paris') * 60)),
                'HH24:MI'
            ) as ora_max_disponibilita,
            ROUND(AVG(cc.crowd_avg)) as affollamento_medio,
            COUNT(*) as giorni_osservati
        FROM first_appearances fa
        LEFT JOIN cycle_crowd cc ON cc.giorno = fa.giorno AND cc.hour_of_day = fa.first_seen_hour
        GROUP BY fa.attraction_name, fa.return_hour
        ORDER BY fa.attraction_name, fa.return_hour;
    """
    
    return pd.read_sql(query, conn, params=params)


def get_pa_price_evolution(conn, attraction_name: str, date_range=None, day_filter=None) -> pd.DataFrame:
    """
    Evoluzione del prezzo PA durante la giornata per una attrazione target.
    Mostra come il prezzo cambia ora per ora (media su tutti i giorni).
    Include affollamento medio per ogni ora.
    """
    query = """
        WITH pa_data AS (
            SELECT hour_of_day, premier_access_price, DATE(sampled_at) as giorno
            FROM wait_times
            WHERE attraction_name = %s
              AND status = 'OPERATING'
              AND premier_access_state = 'AVAILABLE'
              AND premier_access_price IS NOT NULL
    """
    params = [attraction_name]
    
    if date_range is not None:
        query += " AND sampled_at >= %s AND sampled_at <= %s"
        params.extend(date_range)
    
    if day_filter is not None:
        query += " AND day_of_week = %s"
        params.append(day_filter)
    
    query += """
        ),
        crowd AS (
            SELECT DATE(sampled_at) as giorno, hour_of_day,
                   ROUND(AVG(wait_minutes)) as crowd_avg
            FROM wait_times
            WHERE status = 'OPERATING' AND wait_minutes IS NOT NULL AND entity_type = 'ATTRACTION'
            GROUP BY DATE(sampled_at), hour_of_day
        )
        SELECT 
            pa.hour_of_day,
            ROUND(AVG(pa.premier_access_price)) as prezzo_medio,
            MIN(pa.premier_access_price) as prezzo_min,
            MAX(pa.premier_access_price) as prezzo_max,
            ROUND(AVG(c.crowd_avg)) as affollamento_medio,
            COUNT(*) as campionamenti
        FROM pa_data pa
        LEFT JOIN crowd c ON c.giorno = pa.giorno AND c.hour_of_day = pa.hour_of_day
        GROUP BY pa.hour_of_day 
        ORDER BY pa.hour_of_day;
    """
    
    return pd.read_sql(query, conn, params=params)


def get_pa_daily_detail(conn, attraction_name: str, target_date: str) -> pd.DataFrame:
    """
    Dettaglio completo di un singolo giorno per una attrazione:
    tutti i campionamenti PA con slot e prezzo, ordinati cronologicamente.
    Include l'affollamento (media attese di tutte le attrazioni) per ogni campionamento.
    """
    query = """
        WITH crowd AS (
            SELECT DATE(sampled_at) as giorno, hour_of_day,
                   ROUND(AVG(wait_minutes)) as crowd_avg
            FROM wait_times
            WHERE status = 'OPERATING' AND wait_minutes IS NOT NULL 
              AND entity_type = 'ATTRACTION' AND DATE(sampled_at) = %s
            GROUP BY DATE(sampled_at), hour_of_day
        )
        SELECT 
            TO_CHAR(wt.sampled_at AT TIME ZONE 'Europe/Paris', 'HH24:MI') as ora,
            wt.premier_access_state as stato,
            TO_CHAR(wt.premier_access_return_start AT TIME ZONE 'Europe/Paris', 'HH24:MI') as slot_inizio,
            TO_CHAR(wt.premier_access_return_end AT TIME ZONE 'Europe/Paris', 'HH24:MI') as slot_fine,
            wt.premier_access_price as prezzo,
            c.crowd_avg as affollamento
        FROM wait_times wt
        LEFT JOIN crowd c ON c.giorno = DATE(wt.sampled_at) AND c.hour_of_day = wt.hour_of_day
        WHERE wt.attraction_name = %s
          AND DATE(wt.sampled_at) = %s
          AND wt.status = 'OPERATING'
          AND wt.premier_access_state IS NOT NULL
        ORDER BY wt.sampled_at;
    """
    return pd.read_sql(query, conn, params=(target_date, attraction_name, target_date))


def get_shows_schedule(conn) -> pd.DataFrame:
    """
    Recupera tutti gli orari degli show salvati, raggruppati per show e data.
    """
    query = """
        SELECT show_name, park, performance_date, performance_time, status
        FROM show_schedules
        ORDER BY show_name, performance_date, performance_time;
    """
    return pd.read_sql(query, conn)


def get_show_changes(conn, limit: int = 50) -> pd.DataFrame:
    """
    Recupera gli ultimi cambiamenti rilevati negli orari degli show.
    """
    query = """
        SELECT show_name, park, change_type, old_times, new_times, detected_at
        FROM show_changes
        ORDER BY detected_at DESC
        LIMIT %s;
    """
    return pd.read_sql(query, conn, params=(limit,))


def get_shows_list(conn) -> pd.DataFrame:
    """
    Lista di tutti gli show monitorati con l'ultimo stato.
    """
    query = """
        SELECT DISTINCT show_id, show_name, park
        FROM show_schedules
        ORDER BY park, show_name;
    """
    return pd.read_sql(query, conn)


def get_show_times_by_name(conn, show_name: str) -> pd.DataFrame:
    """
    Orari di uno specifico show raggruppati per data.
    """
    query = """
        SELECT performance_date, performance_time, status
        FROM show_schedules
        WHERE show_name = %s
        ORDER BY performance_date DESC, performance_time;
    """
    return pd.read_sql(query, conn, params=(show_name,))
