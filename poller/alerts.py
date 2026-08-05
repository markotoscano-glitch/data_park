"""
alerts.py — Sistema di notifiche Telegram.
Invia alert quando un'attrazione ha una coda insolitamente bassa
rispetto alla media storica per quella fascia oraria.

Configurazione:
- TELEGRAM_BOT_TOKEN: token del bot Telegram (da @BotFather)
- TELEGRAM_CHAT_ID: ID della chat dove inviare i messaggi

Per creare un bot Telegram:
1. Apri @BotFather su Telegram
2. Invia /newbot e segui le istruzioni
3. Copia il token nel .env
4. Avvia una chat col bot e invia un messaggio
5. Visita https://api.telegram.org/bot<TOKEN>/getUpdates per trovare il chat_id
"""

import os
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")

# Soglia: se il wait time è inferiore al X% della media, invia alert
LOW_QUEUE_THRESHOLD_PERCENT = 50  # Coda al 50% o meno della media → alert


def send_telegram_message(message: str) -> bool:
    """
    Invia un messaggio tramite il bot Telegram.
    
    Returns:
        True se inviato con successo, False altrimenti.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.debug("Messaggio Telegram inviato con successo")
            return True
        else:
            logger.warning(f"Telegram API errore: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Errore invio Telegram: {e}")
        return False


def check_and_alert(conn, record: dict):
    """
    Controlla se il wait time corrente è insolitamente basso rispetto alla media
    storica per questa attrazione in questa fascia oraria.
    Se sì, invia un alert Telegram.
    
    Args:
        conn: Connessione psycopg2
        record: Record appena inserito con attraction_name, wait_minutes, etc.
    """
    # Se Telegram non è configurato, skip
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        return
    
    wait_minutes = record.get("wait_minutes")
    if wait_minutes is None or wait_minutes == 0:
        return
    
    attraction_id = record["attraction_id"]
    attraction_name = record["attraction_name"]
    
    now_paris = datetime.now(PARIS_TZ)
    hour_of_day = now_paris.hour
    
    # Recupera la media storica per questa attrazione a quest'ora
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ROUND(AVG(wait_minutes)) as avg_wait, COUNT(*) as samples
                FROM wait_times
                WHERE attraction_id = %s 
                  AND hour_of_day = %s
                  AND status = 'OPERATING'
                  AND wait_minutes IS NOT NULL;
            """, (attraction_id, hour_of_day))
            
            row = cur.fetchone()
            if row is None or row[0] is None or row[1] < 10:
                # Non abbastanza dati storici per confrontare
                return
            
            avg_wait = float(row[0])
            samples = int(row[1])
        
        # Se la coda attuale è significativamente più bassa della media
        if avg_wait > 0:
            percentage = (wait_minutes / avg_wait) * 100
            
            if percentage <= LOW_QUEUE_THRESHOLD_PERCENT:
                saving = int(avg_wait - wait_minutes)
                message = (
                    f"🎢 <b>CODA BASSA!</b>\n\n"
                    f"<b>{attraction_name}</b>\n"
                    f"⏱ Attesa attuale: <b>{wait_minutes} min</b>\n"
                    f"📊 Media storica ({hour_of_day}:00): {int(avg_wait)} min\n"
                    f"💰 Risparmio: ~{saving} min\n"
                    f"📍 {record.get('park', '')}\n\n"
                    f"🕐 {now_paris.strftime('%H:%M')} — basato su {samples} campionamenti"
                )
                send_telegram_message(message)
                logger.info(f"Alert Telegram inviato: {attraction_name} = {wait_minutes} min (media {int(avg_wait)})")
    
    except Exception as e:
        logger.error(f"Errore nel check alert per '{attraction_name}': {e}")


# === PREMIER ACCESS ALERT ===
# Configurazione fasce orarie desiderate per il PA
# Formato: nome_attrazione → (ora_inizio, ora_fine) dello slot di RETURN desiderato
PA_ALERT_CONFIG = {
    "Crush's Coaster": (16, 20),
    "Frozen Ever After": (16, 20),
    "Big Thunder Mountain": (9, 13),
    "Star Wars Hyperspace Mountain": (9, 13),
}


def check_pa_alert(record: dict):
    """
    Controlla se il Premier Access di un'attrazione target offre uno slot
    nella fascia oraria desiderata. Se sì, invia un alert Telegram.
    
    L'alert viene inviato AD OGNI CICLO finché lo slot è nel range desiderato,
    così l'utente sa in tempo reale che è ancora disponibile.
    
    NON richiede status OPERATING — il PA potrebbe apparire anche prima
    dell'apertura del parco (es. dopo mezzanotte).
    
    Args:
        record: Record appena processato dal poller con premier_access_* fields.
    """
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        return
    
    attraction_name = record.get("attraction_name", "")
    pa_state = record.get("premier_access_state")
    pa_return_start = record.get("premier_access_return_start")
    pa_price = record.get("premier_access_price")
    
    # Verifica che sia un'attrazione target con PA disponibile
    if attraction_name not in PA_ALERT_CONFIG:
        return
    if pa_state != "AVAILABLE" or not pa_return_start:
        return
    
    # Parsing dell'ora del return slot
    try:
        if isinstance(pa_return_start, str):
            return_dt = datetime.fromisoformat(pa_return_start)
        else:
            return_dt = pa_return_start
        
        # Converti a Europe/Paris
        if return_dt.tzinfo is not None:
            return_paris = return_dt.astimezone(PARIS_TZ)
        else:
            return_paris = return_dt
        return_hour = return_paris.hour
    except (ValueError, TypeError):
        return
    
    # Verifica se lo slot rientra nella fascia desiderata
    desired_start, desired_end = PA_ALERT_CONFIG[attraction_name]
    if not (desired_start <= return_hour < desired_end):
        return
    
    # Invia l'alert (ogni ciclo, senza de-duplicazione)
    now_paris = datetime.now(PARIS_TZ)
    price_str = f"{pa_price / 100:.2f}€" if pa_price else "N/A"
    return_time_str = return_paris.strftime("%H:%M")
    
    message = (
        f"🎯 <b>PA DISPONIBILE!</b>\n\n"
        f"<b>{attraction_name}</b>\n"
        f"🎟 Slot return: <b>{return_time_str}</b>\n"
        f"💰 Prezzo: <b>{price_str}</b>\n"
        f"🕐 Rilevato alle: {now_paris.strftime('%H:%M')}\n\n"
        f"⚡ Fascia desiderata: {desired_start}:00 - {desired_end}:00\n"
        f"👉 <b>Compra ora prima che lo slot avanzi!</b>"
    )
    
    if send_telegram_message(message):
        logger.info(f"PA Alert inviato: {attraction_name} slot {return_time_str} ({price_str})")


def send_cycle_summary(conn, parks: list):
    """
    Invia un riepilogo del ciclo con le code attuali più basse (top 5).
    Chiamato opzionalmente alla fine di ogni ciclo.
    """
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        return
    
    # Solo se configurato per sommari (opzionale)
    if not os.environ.get("TELEGRAM_SEND_SUMMARY"):
        return
    
    now_paris = datetime.now(PARIS_TZ)
    
    try:
        with conn.cursor() as cur:
            # Ultime 5 attrazioni con coda più bassa di questo ciclo
            cur.execute("""
                SELECT attraction_name, wait_minutes, park
                FROM wait_times
                WHERE sampled_at > NOW() - INTERVAL '5 minutes'
                  AND wait_minutes IS NOT NULL
                  AND wait_minutes > 0
                  AND entity_type = 'ATTRACTION'
                ORDER BY wait_minutes ASC
                LIMIT 5;
            """)
            
            rows = cur.fetchall()
            if not rows:
                return
            
            lines = [f"📊 <b>Top 5 code basse — {now_paris.strftime('%H:%M')}</b>\n"]
            for name, wait, park in rows:
                lines.append(f"• {name}: <b>{wait} min</b> ({park})")
            
            message = "\n".join(lines)
            send_telegram_message(message)
    
    except Exception as e:
        logger.error(f"Errore nel sommario Telegram: {e}")
