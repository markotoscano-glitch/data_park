"""
poller.py — Loop principale del sistema di monitoraggio tempi di attesa.
Due loop paralleli:
- Loop PA: ogni 5 minuti monitora le 4 attrazioni target per Premier Access
- Loop generale: ogni 30 minuti raccoglie tutti i dati (attese, show, alert)

Progettato per girare H24 su Railway senza mai crashare.
La configurazione dei parchi è nella cartella parks/: un file JSON per parco.
"""

import json
import time
import logging
import sys
import threading
from pathlib import Path
from dotenv import load_dotenv

# Carica il file .env dalla root del progetto (una cartella sopra poller/)
load_dotenv(Path(__file__).parent.parent / ".env")

# Importa i moduli del poller
from fetcher import get_live_data, parse_attraction, parse_show
from db import ensure_schema, insert_wait_time, insert_show_schedule, get_connection
from alerts import check_and_alert, send_cycle_summary, check_pa_alert
from telegram_bot import start_bot

# Configurazione logging — stdout per Railway
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("poller")

# Riduci il log verboso di httpx (polling Telegram)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Intervalli di polling
POLLING_INTERVAL = 30 * 60       # 1800 secondi — ciclo generale
PA_POLLING_INTERVAL = 5 * 60     # 300 secondi — ciclo PA (ogni 5 min)

# Le 4 attrazioni target per il monitoraggio PA ad alta frequenza
# Formato: {park_id: [attraction_id, ...]}
PA_TARGET_IDS = {
    # Disney Adventure World
    "ca888437-ebb4-4d50-aed2-d227f7096968": [
        "f0d4b531-e291-471b-9527-00410c2bbd65",   # Crush's Coaster
        "630e2675-f9fc-4984-ba83-03daf5fc89ed",   # Frozen Ever After
    ],
    # Disneyland Park
    "dae968d5-630d-4719-8b06-3d107e944401": [
        "852d957c-382e-4afb-bb74-70c9a79055df",   # Big Thunder Mountain
        "ddca340c-7ba1-4f23-89eb-0d3d52c84bda",   # Star Wars Hyperspace Mountain
    ],
}

# Nomi parchi per i log
PA_PARK_NAMES = {
    "ca888437-ebb4-4d50-aed2-d227f7096968": "Disney Adventure World",
    "dae968d5-630d-4719-8b06-3d107e944401": "Disneyland Park",
}


def load_parks() -> list:
    """
    Carica tutti i file JSON dalla cartella 'parks/'.
    Ogni file rappresenta un parco con le sue entity nel formato API themeparks.wiki.
    Separa le entity in ATTRACTION e SHOW per processarle diversamente.
    Esclude le entity elencate in disabled.json.
    
    Returns:
        Lista di dizionari con: park_id, park_name, attraction_ids, show_ids
    """
    parks_dir = Path(__file__).parent / "parks"
    
    if not parks_dir.exists():
        logger.error(f"Cartella parks/ non trovata in {parks_dir}")
        return []
    
    # Carica la lista degli ID disabilitati
    disabled_ids = set()
    disabled_file = parks_dir / "disabled.json"
    if disabled_file.exists():
        try:
            with open(disabled_file, "r", encoding="utf-8") as f:
                disabled_data = json.load(f)
            # Formato: entities con campo "enabled": true/false
            for entity in disabled_data.get("entities", []):
                if not entity.get("enabled", True):
                    disabled_ids.add(entity["id"])
            if disabled_ids:
                logger.info(f"Escluse {len(disabled_ids)} entity dal polling (enabled=false)")
        except Exception as e:
            logger.warning(f"Errore nel caricamento disabled.json: {e}")
    
    parks = []
    for json_file in sorted(parks_dir.glob("*.json")):
        # Salta il file disabled.json
        if json_file.name == "disabled.json":
            continue
        
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                park_data = json.load(f)
            
            park_id = park_data["id"]
            park_name = park_data["name"]
            
            # Separiamo ATTRACTION da SHOW, escludendo i disabled
            attraction_ids = []
            show_ids = []
            for entity in park_data.get("liveData", []):
                entity_id = entity["id"]
                if entity_id in disabled_ids:
                    continue
                entity_type = entity.get("entityType", "ATTRACTION")
                if entity_type == "SHOW":
                    show_ids.append(entity_id)
                else:
                    attraction_ids.append(entity_id)
            
            parks.append({
                "park_id": park_id,
                "park_name": park_name,
                "attraction_ids": attraction_ids,
                "show_ids": show_ids
            })
            
            logger.info(
                f"Caricato: {park_name} — "
                f"{len(attraction_ids)} attrazioni, {len(show_ids)} show "
                f"(da {json_file.name})"
            )
        except Exception as e:
            logger.error(f"Errore nel caricamento di {json_file.name}: {e}")
            continue
    
    total_attr = sum(len(p["attraction_ids"]) for p in parks)
    total_show = sum(len(p["show_ids"]) for p in parks)
    logger.info(f"Totale: {len(parks)} parchi, {total_attr} attrazioni, {total_show} show")
    return parks


def run_cycle(parks: list):
    """
    Esegue un singolo ciclo di polling:
    - Per ogni parco, recupera i dati live dall'API
    - Per le ATTRACTION: salva wait_minutes nella tabella wait_times
    - Per gli SHOW: salva/aggiorna gli showtimes nella tabella show_schedules
    
    Args:
        parks: Lista di configurazioni parco [{park_id, park_name, attraction_ids, show_ids}]
    """
    for park in parks:
        park_id = park["park_id"]
        park_name = park["park_name"]
        attraction_ids = park["attraction_ids"]
        show_ids = park["show_ids"]
        
        try:
            logger.info(f"Recupero dati live per: {park_name}")
            live_data = get_live_data(park_id)
            
            if live_data is None:
                logger.warning(f"Nessun dato ricevuto per {park_name}, salto al prossimo parco")
                continue
            
            # --- Processiamo le ATTRACTION ---
            inserted = 0
            skipped = 0
            conn = get_connection()
            for attr_id in attraction_ids:
                try:
                    record = parse_attraction(live_data, attr_id, park_name)
                    if record is not None:
                        insert_wait_time(record)
                        # Controlla se mandare alert Telegram (coda bassa)
                        check_and_alert(conn, record)
                        # Controlla se il PA offre uno slot nella fascia desiderata
                        check_pa_alert(record)
                        inserted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    logger.error(f"Errore attraction {attr_id}: {e}", exc_info=True)
                    continue
            
            logger.info(f"{park_name} ATTRAZIONI: {inserted} inserite, {skipped} non trovate")
            
            # --- Processiamo gli SHOW ---
            shows_ok = 0
            shows_skip = 0
            for show_id in show_ids:
                try:
                    show_record = parse_show(live_data, show_id, park_name)
                    if show_record is not None and show_record.get("showtimes"):
                        insert_show_schedule(show_record)
                        shows_ok += 1
                    else:
                        shows_skip += 1
                except Exception as e:
                    logger.error(f"Errore show {show_id}: {e}", exc_info=True)
                    continue
            
            logger.info(f"{park_name} SHOW: {shows_ok} processati, {shows_skip} senza orari")
                    
        except Exception as e:
            logger.error(
                f"Errore nel recupero dati per {park_name}: {e}",
                exc_info=True
            )
            continue


def run_pa_cycle():
    """
    Ciclo rapido: monitora SOLO le 4 attrazioni target per il Premier Access.
    Salva i dati nel DB (per la dashboard PA Strategy) e invia alert Telegram.
    Gira ogni 5 minuti, 24/7.
    """
    for park_id, attr_ids in PA_TARGET_IDS.items():
        park_name = PA_PARK_NAMES.get(park_id, park_id)
        
        try:
            live_data = get_live_data(park_id)
            if live_data is None:
                logger.warning(f"[PA] Nessun dato per {park_name}")
                continue
            
            for attr_id in attr_ids:
                try:
                    record = parse_attraction(live_data, attr_id, park_name)
                    if record is not None:
                        insert_wait_time(record)
                        check_pa_alert(record)
                except Exception as e:
                    logger.error(f"[PA] Errore {attr_id}: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"[PA] Errore ciclo per {park_name}: {e}")
            continue


def pa_loop():
    """
    Thread separato: loop infinito per il monitoraggio PA ogni 5 minuti.
    """
    logger.info("[PA] Avvio loop Premier Access — ogni 5 minuti, 24/7")
    pa_cycle_count = 0
    
    while True:
        pa_cycle_count += 1
        try:
            run_pa_cycle()
            logger.info(f"[PA] Ciclo #{pa_cycle_count} completato. Prossimo fra 5 min.")
        except Exception as e:
            logger.error(f"[PA] Errore inatteso ciclo #{pa_cycle_count}: {e}")
        
        time.sleep(PA_POLLING_INTERVAL)


def main():
    """
    Entry point del poller. Avvia due loop:
    1. Loop PA (thread separato): ogni 5 minuti, solo 4 attrazioni target
    2. Loop generale (main thread): ogni 30 minuti, tutte le attrazioni e show
    """
    logger.info("=" * 60)
    logger.info("Avvio Disneyland Paris Wait Time Monitor — Poller")
    logger.info("=" * 60)
    
    # Avvia il bot Telegram in background (se configurato)
    start_bot()
    
    # Carica la configurazione di tutti i parchi dalla cartella parks/
    parks = load_parks()
    
    if not parks:
        logger.error("Nessun parco caricato. Verifica la cartella poller/parks/")
        logger.info("Il poller continuerà a tentare nei prossimi cicli...")
    
    # Verifica/crea lo schema del database
    try:
        ensure_schema()
        logger.info("Schema database pronto")
    except Exception as e:
        logger.error(f"Errore nella creazione dello schema DB: {e}", exc_info=True)
        logger.info("Il poller continuerà a tentare nei prossimi cicli...")
    
    # Avvia il loop PA in un thread separato (daemon = muore col main)
    pa_thread = threading.Thread(target=pa_loop, daemon=True)
    pa_thread.start()
    logger.info("[PA] Thread Premier Access avviato")
    
    # Loop generale (main thread) — ogni 30 minuti
    cycle_count = 0
    while True:
        cycle_count += 1
        logger.info(f"--- Inizio ciclo generale #{cycle_count} ---")
        
        try:
            run_cycle(parks)
            # Invia sommario Telegram (se configurato)
            try:
                conn = get_connection()
                send_cycle_summary(conn, parks)
            except Exception:
                pass
            logger.info(
                f"Ciclo #{cycle_count} completato con successo. "
                f"Prossimo ciclo fra {POLLING_INTERVAL // 60} minuti."
            )
        except Exception as e:
            # Errore inatteso a livello di ciclo — non deve mai crashare
            logger.error(
                f"Errore inatteso nel ciclo #{cycle_count}: {e}",
                exc_info=True
            )
            logger.info("Il poller continuerà al prossimo ciclo...")
        
        # Attesa fino al prossimo ciclo
        time.sleep(POLLING_INTERVAL)


if __name__ == "__main__":
    main()
