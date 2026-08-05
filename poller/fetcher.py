"""
fetcher.py — Client per l'API themeparks.wiki
Recupera i dati live delle attrazioni di Disneyland Paris.
Estrae anche dati su Single Rider e Premier Access.
"""

import requests
import logging

# Configurazione logger
logger = logging.getLogger(__name__)

# Timeout per le richieste HTTP (secondi)
REQUEST_TIMEOUT = 30


def get_live_data(park_id: str) -> dict | None:
    """
    Recupera i dati live per un parco specifico dall'API themeparks.wiki.
    
    Args:
        park_id: ID del parco (UUID dall'API themeparks.wiki)
    
    Returns:
        Dizionario con i dati live, oppure None se la richiesta fallisce.
    """
    url = f"https://api.themeparks.wiki/v1/entity/{park_id}/live"
    
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.Timeout:
        logger.error(f"Timeout nella richiesta per il parco {park_id}")
        return None
    except requests.exceptions.ConnectionError:
        logger.error(f"Errore di connessione per il parco {park_id}")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"Errore HTTP per il parco {park_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Errore imprevisto nel fetch per il parco {park_id}: {e}")
        return None


def parse_attraction(live_data: dict, attraction_id: str, park_name: str) -> dict | None:
    """
    Estrae le informazioni di attesa per un'attrazione specifica dai dati live.
    Cerca l'attrazione per ID nell'array liveData della risposta API.
    Estrae: wait_minutes, single_rider, premier_access_price.
    
    Args:
        live_data: Risposta JSON completa dall'API per un parco
        attraction_id: UUID dell'attrazione da cercare (themeparks.wiki ID)
        park_name: Nome del parco (per il record DB)
    
    Returns:
        Dizionario con i campi necessari per l'inserimento nel DB,
        oppure None se l'attrazione non è trovata nella risposta.
    """
    if not live_data or "liveData" not in live_data:
        return None
    
    for entity in live_data.get("liveData", []):
        if entity.get("id") == attraction_id:
            # Attrazione trovata — estraiamo tutti i dati utili
            status = entity.get("status", "UNKNOWN")
            entity_type = entity.get("entityType", "ATTRACTION")
            external_id = entity.get("externalId")
            name = entity.get("name", "Sconosciuto")
            
            # Tempo di attesa STANDBY
            wait_minutes = None
            if status == "OPERATING":
                queue = entity.get("queue", {})
                standby = queue.get("STANDBY", {})
                wait_minutes = standby.get("waitTime")
            
            # Tempo di attesa SINGLE RIDER (se disponibile)
            single_rider_minutes = None
            if status == "OPERATING":
                queue = entity.get("queue", {})
                single_rider = queue.get("SINGLE_RIDER", {})
                single_rider_minutes = single_rider.get("waitTime")
            
            # Prezzo Premier Access (PAID_RETURN_TIME)
            # Estratto SEMPRE (anche fuori orario parco — il PA può essere disponibile dopo mezzanotte)
            premier_access_price = None
            premier_access_currency = None
            premier_access_state = None
            premier_access_return_start = None
            premier_access_return_end = None
            queue = entity.get("queue", {})
            paid_return = queue.get("PAID_RETURN_TIME", {})
            if paid_return:
                premier_access_state = paid_return.get("state")  # AVAILABLE, FINISHED, etc.
                price_info = paid_return.get("price", {})
                if price_info:
                    premier_access_price = price_info.get("amount")
                    premier_access_currency = price_info.get("currency")
                # Orari del return time (fascia oraria prenotata)
                premier_access_return_start = paid_return.get("returnStart")
                premier_access_return_end = paid_return.get("returnEnd")
            
            return {
                "attraction_id": attraction_id,
                "attraction_name": name,
                "entity_type": entity_type,
                "park": park_name,
                "external_id": external_id,
                "wait_minutes": wait_minutes,
                "single_rider_minutes": single_rider_minutes,
                "premier_access_price": premier_access_price,
                "premier_access_currency": premier_access_currency,
                "premier_access_state": premier_access_state,
                "premier_access_return_start": premier_access_return_start,
                "premier_access_return_end": premier_access_return_end,
                "status": status
            }
    
    # Attrazione non trovata nella risposta API
    return None


def parse_show(live_data: dict, show_id: str, park_name: str) -> dict | None:
    """
    Estrae le informazioni di uno SHOW dai dati live.
    Ritorna gli showtimes (orari delle performance) per il salvataggio.
    
    Args:
        live_data: Risposta JSON completa dall'API per un parco
        show_id: UUID dello show da cercare
        park_name: Nome del parco
    
    Returns:
        Dizionario con show_id, show_name, park, external_id, status, showtimes
        oppure None se non trovato.
    """
    if not live_data or "liveData" not in live_data:
        return None
    
    for entity in live_data.get("liveData", []):
        if entity.get("id") == show_id:
            entity_type = entity.get("entityType", "")
            
            # Verifichiamo che sia effettivamente uno SHOW
            if entity_type != "SHOW":
                return None
            
            return {
                "show_id": show_id,
                "show_name": entity.get("name", "Sconosciuto"),
                "park": park_name,
                "external_id": entity.get("externalId"),
                "status": entity.get("status", "UNKNOWN"),
                "showtimes": entity.get("showtimes", [])
            }
    
    return None
