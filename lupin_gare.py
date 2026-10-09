#!/usr/bin/env python3
"""
Lupin Gare – Monitor gare d'appalto ANAC → Telegram
Versione sperimentale (2026-09)

Stesso schema di Lupin case, applicato agli appalti pubblici:
- Scarica i bandi pubblicati di recente sulla Piattaforma Pubblicità Legale
  di ANAC (Banca Dati Nazionale dei Contratti Pubblici). Se ANAC non risponde,
  usa TED (Gazzetta ufficiale UE), che però contiene solo le gare sopra soglia
  europea
- Tiene memoria delle gare già viste (gare_memoria.json)
- Filtra secondo il profilo dell'azienda (profilo_gare.json): parole chiave,
  codici CPV, luoghi, importo, scadenza
- Valutazione AI: punteggio 1-10 di quanto la gara è adatta all'azienda,
  con riassunto in parole semplici
- Notifica su Telegram

Il formato preciso dei dati ANAC non è ancora stato verificato su dati reali:
la lettura dei campi è volutamente "tollerante" (cerca i campi per nome ovunque
nel JSON). Al primo avvio usare ESPLORA=1 per stampare nei log la struttura
grezza e sistemare eventualmente i nomi dei campi.

Variabili d'ambiente (GitHub Secrets):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY
Opzionali:
  TELEGRAM_CHAT_ID_GARE → chat dedicata alle gare (default: TELEGRAM_CHAT_ID)
  PROFILI_GARE     → (GitHub Secret) profili delle imprese clienti, in JSON:
                     una lista di oggetti con gli stessi campi di profilo_gare.json
                     più "id" (breve e anonimo, es. "c1": finisce nella memoria
                     pubblica), "chat_id" (chat Telegram dell'impresa),
                     "attivo" (true/false) e "fine_prova" (AAAA-MM-GG, facoltativo).
                     Il profilo di profilo_gare.json resta e continua a scrivere
                     nella chat di TELEGRAM_CHAT_ID_GARE.
  DRY_RUN=1        → nessun invio Telegram reale e memoria non salvata
  AI_OFF=1         → disattiva la valutazione AI
  SOGLIA_SCORE=6   → notifica solo gare con punteggio >= 6 (default 0 = tutte)
  GIORNI_INDIETRO=2 → quanti giorni di pubblicazioni scaricare (default 2)
  RIPROVA=1        → (solo test) ignora memoria e segnalibro: rivaluta gli
                     ultimi GIORNI_INDIETRO giorni come se fosse il primo avvio
  ESPLORA=1        → stampa nei log esempi grezzi degli avvisi e
                     prova filtri e sorgenti (sonda)
  ANAC_SCHEDE=P    → tipi di scheda ANAC da tenere (prefissi, separati da virgola)
  ANAC_MAX_PAGINE=30 → massimo di pagine ANAC da 1000 avvisi per avvio
  SORGENTE=auto    → anac | ted | auto (ANAC e, se non risponde, TED)
  ANAC_API_URL     → endpoint degli avvisi (default: Pubblicità Legale ANAC)
"""

import os
import re
import json
import time
import html
import hashlib
import logging
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID_GARE") or os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FILE_MEMORIA = Path("gare_memoria.json")
FILE_PROFILO = Path("profilo_gare.json")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
AI_OFF = os.environ.get("AI_OFF", "0") == "1"
ESPLORA = os.environ.get("ESPLORA", "0") == "1"
RIPROVA = os.environ.get("RIPROVA", "0") == "1"
# RECUPERO=1: riscarica GIORNI_INDIETRO giorni ignorando il segnalibro (la memoria
# resta: niente doppioni) e rivaluta le gare scartate solo per punteggio sotto
# l'attuale SOGLIA_SCORE. Le gare scadute restano escluse dai filtri.
RECUPERO = os.environ.get("RECUPERO", "0") == "1"
SOGLIA_SCORE = int(os.environ.get("SOGLIA_SCORE", "0") or 0)
MAX_CHIAMATE_AI = int(os.environ.get("MAX_CHIAMATE_AI", "60"))
GIORNI_INDIETRO = int(os.environ.get("GIORNI_INDIETRO", "2") or 2)
MODELLO_AI = os.environ.get("MODELLO_AI", "claude-haiku-4-5-20251001")
ANAC_API_URL = os.environ.get(
    "ANAC_API_URL", "https://pubblicitalegale.anticorruzione.it/api/v0/avvisi"
)
URL_RICERCA_ANAC = "https://pubblicitalegale.anticorruzione.it/bandi"
TED_API_URL = "https://api.ted.europa.eu/v3/notices/search"
# anac = solo ANAC, ted = solo TED (UE), auto = ANAC e, se non risponde, TED
SORGENTE = os.environ.get("SORGENTE", "auto").lower()
HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Referer": "https://pubblicitalegale.anticorruzione.it/bandi",
    "Origin": "https://pubblicitalegale.anticorruzione.it",
}
DIMENSIONE_PAGINA = 100
MAX_PAGINE = 30
# ANAC pubblica migliaia di avvisi al giorno (soprattutto affidamenti diretti
# già conclusi): servono più pagine e si tengono solo i tipi di scheda utili.
ANAC_MAX_PAGINE = int(os.environ.get("ANAC_MAX_PAGINE", "30"))
ANAC_DIMENSIONE_PAGINA = 1000  # verificato: l'API accetta size=1000
# Prefissi di codiceScheda da tenere. "P" = bandi e avvisi di gara (P1_16,
# P2_16, P2_19...). AD = affidamenti diretti, A = esiti, M = modifiche.
ANAC_SCHEDE = [x.strip() for x in os.environ.get("ANAC_SCHEDE", "P").split(",") if x.strip()]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lupin-gare")

_chiamate_ai = 0


# ---------------------------------------------------------------------------
# Memoria e profilo
# ---------------------------------------------------------------------------

def carica_memoria() -> dict:
    if FILE_MEMORIA.exists():
        try:
            with FILE_MEMORIA.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            log.warning("Memoria JSON corrotta, riparto da vuota")
    return {}


def salva_memoria(memoria: dict) -> None:
    with FILE_MEMORIA.open("w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2, sort_keys=True)


def _profilo_base() -> dict:
    return {
        "descrizione_azienda": "",
        "parole_chiave": [],
        "parole_escluse": [],
        "cpv_prefissi": [],
        "nature": [],
        "categorie_soa": [],
        "luoghi": [],
        "regioni": [],
        "importo_min": 0,
        "importo_max": 0,
    }


def carica_profilo() -> dict:
    profilo = _profilo_base()
    if FILE_PROFILO.exists():
        with FILE_PROFILO.open("r", encoding="utf-8") as f:
            profilo.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    return profilo


def carica_profili() -> list[dict]:
    """Ritorna i profili da servire in questo avvio: prima quello di
    profilo_gare.json (id "", chat TELEGRAM_CHAT_ID_GARE), poi le imprese
    clienti del secret PROFILI_GARE. Ogni profilo ha anche "_id" e "_chat"."""
    principale = carica_profilo()
    principale["_id"] = ""
    principale["_chat"] = TELEGRAM_CHAT_ID
    principale["_nome"] = "principale"
    profili = [principale]

    grezzo = os.environ.get("PROFILI_GARE", "").strip()
    if not grezzo:
        return profili
    try:
        lista = json.loads(grezzo)
    except json.JSONDecodeError as e:
        log.error("PROFILI_GARE non è JSON valido (%s): uso solo il profilo principale", e)
        return profili
    if isinstance(lista, dict):
        lista = [lista]

    oggi = datetime.now(timezone.utc).date()
    visti: set[str] = set()
    for i, dati in enumerate(lista):
        if not isinstance(dati, dict):
            log.warning("PROFILI_GARE[%d]: non è un oggetto, ignorato", i)
            continue
        pid = str(dati.get("id") or "").strip()
        chat = str(dati.get("chat_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", pid) or pid in visti:
            log.warning("PROFILI_GARE[%d]: \"id\" mancante, non valido o doppio: ignorato", i)
            continue
        visti.add(pid)
        if not chat:
            log.warning("Profilo %s: manca chat_id, ignorato", pid)
            continue
        if dati.get("attivo") is False:
            log.info("Profilo %s: non attivo, saltato", pid)
            continue
        fine = a_data(dati.get("fine_prova"))
        if fine and fine < oggi:
            log.info("Profilo %s: prova finita il %s, saltato", pid, fine.isoformat())
            continue
        profilo = _profilo_base()
        profilo.update({k: v for k, v in dati.items()
                        if k in profilo and not k.startswith("_")})
        profilo["_id"] = pid
        profilo["_chat"] = chat
        profilo["_nome"] = pid
        profili.append(profilo)
    return profili


def chiave_memoria(profilo: dict, gid: str) -> str:
    """Il profilo principale usa l'id della gara (come prima), gli altri
    "<id profilo>:<id gara>", così ogni impresa ha la sua memoria."""
    return f"{profilo['_id']}:{gid}" if profilo["_id"] else gid


# ---------------------------------------------------------------------------
# Lettura tollerante del JSON ANAC
# ---------------------------------------------------------------------------

def _norm(chiave: str) -> str:
    return re.sub(r"[^a-z0-9]", "", chiave.lower())


def cerca(obj, *nomi, tutti: bool = False):
    """Cerca ricorsivamente il valore dei campi il cui nome contiene uno dei
    `nomi` (confronto senza maiuscole, underscore, trattini). I nomi sono in
    ordine di preferenza. Ritorna il primo valore scalare non vuoto, oppure
    (tutti=True) la lista di tutti i valori trovati."""
    trovati: dict[str, list] = {n: [] for n in nomi}
    chiavi = [(n, _norm(n)) for n in nomi]

    def visita(o):
        if isinstance(o, dict):
            for k, v in o.items():
                nk = _norm(str(k))
                for n, nn in chiavi:
                    if nn in nk and v not in (None, "", [], {}) and not isinstance(v, (dict, list)):
                        trovati[n].append(v)
                        break
                visita(v)
        elif isinstance(o, list):
            for x in o:
                visita(x)

    visita(obj)
    if tutti:
        out = []
        for n in nomi:
            for v in trovati[n]:
                if v not in out:
                    out.append(v)
        return out
    for n in nomi:
        if trovati[n]:
            return trovati[n][0]
    return None


def a_numero(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r"[^\d,.\-]", "", str(val))
    if not s:
        return None
    # "1.234.567,89" → 1234567.89 ; "1234567.89" resta così
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def a_data(val) -> date | None:
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:19] if "T" in fmt else s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def a_datetime(val) -> datetime | None:
    if not val:
        return None
    try:
        d = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _template_anac(avviso: dict) -> dict:
    for t in avviso.get("templates") or avviso.get("template") or []:
        if isinstance(t, dict) and isinstance(t.get("template"), dict):
            return t["template"]
    return {}


def _items_anac(tpl: dict) -> list[dict]:
    items = []
    for sez in tpl.get("sections") or []:
        for it in sez.get("items") or []:
            if isinstance(it, dict):
                items.append(it)
    return items


def normalizza(avviso: dict) -> dict:
    """Estrae dai dati grezzi ANAC (Pubblicità Legale) i campi che servono.

    Struttura reale vista a settembre 2026: campi di testata (idAvviso,
    codiceScheda, tipologia, dataScadenza, dataPubblicazione) e
    templates[0].template con metadata.descrizione e sections[] (SEZ. A
    committente, SEZ. B dati generali, SEZ. C oggetto con items[] per lotto)."""
    tpl = _template_anac(avviso)
    meta = tpl.get("metadata") or {}
    items = _items_anac(tpl)

    cig = cerca(items, "cig") or cerca(avviso, "cig")
    oggetto = (meta.get("descrizione") or meta.get("titolo")
               or next((it.get("descrizione") for it in items if it.get("descrizione")), None)
               or cerca(avviso, "oggetto_gara", "oggetto_lotto", "descrizione", "titolo"))
    ente = cerca(avviso, "denominazione_amministrazione", "denominazioneAmministrazione",
                 "stazione_appaltante", "amministrazione")
    importo = a_numero(cerca(items or avviso, "importo_complessivo", "valore_complessivo",
                             "importo_totale", "valore_stimato", "importo_base", "importo_lotto",
                             "valore_affidamento", "importo", "valore"))
    scadenza = a_data(avviso.get("dataScadenza") or cerca(
        avviso, "termine_ricezione", "terminericezione", "data_scadenza", "scadenza",
        "termine_presentazione"))
    pubblicazione = a_datetime(avviso.get("dataPubblicazione")) or a_datetime(
        cerca(avviso, "data_pubblicazione", "dataPubblicazione"))
    cpv = list(dict.fromkeys(str(c) for c in cerca(avviso, "cpv", tutti=True)))[:5]
    categorie = []
    for it in items:
        for c in it.get("categorie") or []:
            if isinstance(c, dict) and c.get("codice"):
                desc = (c.get("descrizione") or "").strip()
                voce = desc if desc.startswith(c["codice"]) else f"{c['codice']} {desc}".strip()
                if voce not in categorie:
                    categorie.append(voce)
    # luogo_istat a volte è il codice numerico del comune: si preferisce un nome
    luogo = next((v for v in cerca(items or avviso, "luogo_istat", "luogo_nuts",
                                   "luogo_esecuzione", "luogo", "comune", "provincia",
                                   "regione", tutti=True)
                  if not str(v).strip().isdigit()), None)
    # Codici di luogo per il filtro per regione (ISTAT del comune, NUTS)
    istat = next((str(v).strip() for v in cerca(items or avviso, "luogo_istat", "codice_istat", tutti=True)
                  if str(v).strip().isdigit()), None)
    nuts = next((str(v).strip().upper() for v in cerca(items or avviso, "luogo_nuts", "nuts", tutti=True)
                 if re.match(r"^IT[A-Z0-9]", str(v).strip().upper())), None)
    natura = cerca(items, "natura_principale")
    tipo = avviso.get("tipologia") or cerca(items, "natura_principale")
    url = None
    for v in cerca(avviso, "documenti_di_gara_link", "link", "url", tutti=True):
        if isinstance(v, str) and v.startswith("http"):
            url = v
            break

    id_avviso = avviso.get("idAvviso") or cerca(avviso, "idAvviso", "id_avviso")
    if id_avviso:
        gid = f"A{id_avviso}"
    elif cig:
        gid = f"C{cig}"
    else:
        gid = "H" + hashlib.sha1(f"{ente}|{oggetto}|{importo}".encode()).hexdigest()[:16]

    return {
        "id": gid,
        "cig": str(cig) if cig else None,
        "oggetto": " ".join(str(oggetto or "Oggetto non indicato").split())[:400],
        "ente": str(ente or "Ente non indicato").strip()[:150],
        "importo": importo,
        "scadenza": scadenza.isoformat() if scadenza else None,
        "pubblicazione": pubblicazione.date().isoformat() if pubblicazione else None,
        "cpv": cpv,
        "categorie": categorie[:4],
        "luogo": str(luogo)[:80] if luogo else None,
        "istat": istat,
        "nuts": nuts,
        "tipo": str(tipo)[:60] if tipo else None,
        "scheda": avviso.get("codiceScheda"),
        "natura": str(natura) if natura else None,
        "url": url or URL_RICERCA_ANAC,
    }


# ---------------------------------------------------------------------------
# Download da ANAC
# ---------------------------------------------------------------------------

def _estrai_lista(dati) -> tuple[list, bool]:
    """Ritorna (lista avvisi, ci_sono_altre_pagine)."""
    if isinstance(dati, list):
        return dati, len(dati) >= DIMENSIONE_PAGINA
    if isinstance(dati, dict):
        for chiave in ("content", "results", "risultati", "data", "items", "avvisi"):
            if isinstance(dati.get(chiave), list):
                lista = dati[chiave]
                if "last" in dati:
                    return lista, not dati["last"]
                return lista, len(lista) >= DIMENSIONE_PAGINA
    return [], False


def _get_anac(params: dict):
    """Una richiesta all'API ANAC. Ritorna (json, None) oppure (None, errore)."""
    try:
        r = requests.get(ANAC_API_URL, params=params, timeout=45, headers=HEADERS_BROWSER)
    except Exception as e:
        return None, f"errore di rete: {e}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} su {r.url} → {' '.join(r.text.split())[:200]}"
    try:
        return r.json(), None
    except ValueError:
        return None, f"risposta non JSON → {' '.join(r.text.split())[:200]}"


def _pubblicazione(a: dict) -> datetime | None:
    return a_datetime(a.get("dataPubblicazione"))


def _conta_schede(lista: list[dict]) -> dict[str, int]:
    schede: dict[str, int] = {}
    for a in lista:
        k = str(a.get("codiceScheda"))
        schede[k] = schede.get(k, 0) + 1
    return dict(sorted(schede.items(), key=lambda x: -x[1]))


def _esplora_pagina_anac(lista: list[dict], pagina: int) -> None:
    date_pub = [d for d in (_pubblicazione(a) for a in lista) if d]
    log.info("[ESPLORA] ANAC pagina %d: tipi scheda %s | pubblicazione da %s a %s",
             pagina, _conta_schede(lista),
             min(date_pub, default=None), max(date_pub, default=None))


_esempi_stampati: set[str] = set()


def _esplora_esempio(a: dict) -> None:
    """Stampa un esempio grezzo per ciascun tipo di scheda tenuto."""
    k = str(a.get("codiceScheda"))
    if k in _esempi_stampati or len(_esempi_stampati) >= 4:
        return
    _esempi_stampati.add(k)
    log.info("[ESPLORA] ANAC esempio scheda %s grezzo:\n%s", k,
             json.dumps(a, ensure_ascii=False, indent=1)[:7000])
    log.info("[ESPLORA] ANAC esempio scheda %s normalizzato: %s", k,
             json.dumps(normalizza(a), ensure_ascii=False))


def scheda_utile(a: dict) -> bool:
    k = str(a.get("codiceScheda") or "")
    return not ANAC_SCHEDE or any(k.startswith(p) for p in ANAC_SCHEDE)


def scarica_avvisi_anac(dal: datetime | None = None) -> tuple[list[dict] | None, datetime | None]:
    """Ritorna (avvisi utili, data di pubblicazione più recente vista).
    avvisi è None se ANAC non risponde.

    Il filtro per data lato server fa rispondere ANAC con HTTP 500, quindi si
    scaricano le pagine più recenti (in ordine di pubblicazione decrescente) e
    ci si ferma alla prima pagina interamente più vecchia di `dal` (ultimo
    avvio) o, al primo avvio, di GIORNI_INDIETRO giorni."""
    limite = dal or (datetime.now(timezone.utc) - timedelta(days=GIORNI_INDIETRO))
    base = {"size": ANAC_DIMENSIONE_PAGINA}

    avvisi: list[dict] = []
    piu_recente: datetime | None = None
    totale_schede: dict[str, int] = {}
    arrivato_al_limite = False

    for pagina in range(ANAC_MAX_PAGINE):
        if pagina > 0:
            time.sleep(0.5)
        dati, errore = _get_anac({**base, "page": pagina})
        if dati is None:
            log.error("ANAC pagina %d: %s", pagina, errore)
            if pagina == 0:
                return None, None
            break

        lista, altre = _estrai_lista(dati)
        if ESPLORA and pagina < 3:
            _esplora_pagina_anac(lista, pagina)
        for k, v in _conta_schede(lista).items():
            totale_schede[k] = totale_schede.get(k, 0) + v

        nuovi = 0
        for a in lista:
            d = _pubblicazione(a)
            if d and (piu_recente is None or d > piu_recente):
                piu_recente = d
            if d and d < limite:
                continue
            nuovi += 1
            if scheda_utile(a):
                avvisi.append(a)
                if ESPLORA:
                    _esplora_esempio(a)
        if pagina % 3 == 0 or not nuovi:
            log.info("  ANAC pagina %d: %d avvisi, %d nel periodo, %d utili finora",
                     pagina, len(lista), nuovi, len(avvisi))

        if not altre or not lista:
            arrivato_al_limite = True
            break
        if not nuovi:
            arrivato_al_limite = True
            break

    if not arrivato_al_limite:
        log.warning("ANAC: raggiunto il massimo di %d pagine prima di arrivare al %s; "
                    "alcuni avvisi più vecchi non sono stati letti",
                    ANAC_MAX_PAGINE, limite.isoformat(timespec="minutes"))
    log.info("ANAC tipi scheda letti: %s | tenuti (prefissi %s): %d",
             totale_schede, ",".join(ANAC_SCHEDE) or "tutti", len(avvisi))
    return avvisi, piu_recente


# ---------------------------------------------------------------------------
# Sorgente di riserva: TED (Gazzetta UE, solo gare sopra soglia europea)
# ---------------------------------------------------------------------------

CAMPI_TED = [
    "publication-number", "publication-date", "notice-title", "buyer-name",
    "buyer-city", "classification-cpv", "deadline-receipt-tender-date-lot",
    "place-of-performance", "notice-type",
]


def _testo_ted(v, lingue=("ita", "eng")) -> str | None:
    """I campi TED sono spesso {"ita": "..."} o {"ita": ["..."]} o liste."""
    if v is None:
        return None
    if isinstance(v, dict):
        for l in lingue:
            if v.get(l):
                return _testo_ted(v[l])
        for x in v.values():
            t = _testo_ted(x)
            if t:
                return t
        return None
    if isinstance(v, list):
        for x in v:
            t = _testo_ted(x)
            if t:
                return t
        return None
    return str(v)


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def normalizza_ted(n: dict) -> dict:
    pub = n.get("publication-number") or ""
    cpv = n.get("classification-cpv") or []
    if not isinstance(cpv, list):
        cpv = [cpv]
    return {
        "id": f"T{pub}",
        "cig": None,
        "oggetto": re.sub(r"^Italia\s*[–-]\s*", "",
                          (_testo_ted(n.get("notice-title")) or "Oggetto non indicato").strip())[:400],
        "ente": (_testo_ted(n.get("buyer-name")) or "Ente non indicato").strip()[:150],
        "importo": None,
        "scadenza": _iso(a_data(_testo_ted(n.get("deadline-receipt-tender-date-lot")))),
        "pubblicazione": _iso(a_data(_testo_ted(n.get("publication-date")))),
        "cpv": list(dict.fromkeys(str(c) for c in cpv))[:5],
        "categorie": [],
        "scheda": None,
        "natura": None,
        "luogo": _testo_ted(n.get("buyer-city")) or _testo_ted(n.get("place-of-performance")),
        "istat": None,
        "nuts": next((c for c in re.findall(r"\bIT[A-Z0-9]{1,3}\b", json.dumps(n.get("place-of-performance") or ""))), None),
        "tipo": _testo_ted(n.get("notice-type")),
        "url": f"https://ted.europa.eu/it/notice/-/detail/{pub}" if pub else "https://ted.europa.eu",
    }


def scarica_avvisi_ted() -> list[dict]:
    inizio = datetime.now(timezone.utc).date() - timedelta(days=GIORNI_INDIETRO)
    query = f"buyer-country IN (ITA) AND publication-date>={inizio.strftime('%Y%m%d')}"
    avvisi: list[dict] = []
    for pagina in range(1, MAX_PAGINE + 1):
        try:
            r = requests.post(TED_API_URL, timeout=45, json={
                "query": query, "fields": CAMPI_TED,
                "page": pagina, "limit": DIMENSIONE_PAGINA,
            })
        except Exception as e:
            log.error("TED errore di rete: %s", e)
            break
        if r.status_code != 200:
            log.error("TED HTTP %s → %s", r.status_code, " ".join(r.text.split())[:400])
            break
        dati = r.json()
        lista = dati.get("notices") or []
        if ESPLORA and pagina == 1:
            log.info("[ESPLORA] TED chiavi: %s | totale: %s", list(dati.keys()),
                     dati.get("totalNoticeCount"))
            for i, a in enumerate(lista[:2]):
                log.info("[ESPLORA] TED avviso %d grezzo:\n%s", i,
                         json.dumps(a, ensure_ascii=False, indent=1)[:3000])
                log.info("[ESPLORA] TED avviso %d normalizzato: %s", i,
                         json.dumps(normalizza_ted(a), ensure_ascii=False))
        avvisi.extend(lista)
        log.info("  TED pagina %d: %d avvisi", pagina, len(lista))
        if len(lista) < DIMENSIONE_PAGINA:
            break
        time.sleep(1)
    return avvisi


def scarica_gare(stato: dict) -> list[dict]:
    """Ritorna le gare già normalizzate dalla sorgente scelta. Aggiorna `stato`
    con la data dell'avviso ANAC più recente, da cui ripartire la volta dopo."""
    if SORGENTE in ("anac", "auto"):
        dal = a_datetime(stato.get("anac_ultima_pubblicazione"))
        grezzi, piu_recente = scarica_avvisi_anac(dal)
        if grezzi is not None:
            if piu_recente:
                stato["anac_ultima_pubblicazione"] = piu_recente.isoformat()
            log.info("Sorgente ANAC: %d avvisi utili", len(grezzi))
            return [normalizza(a) for a in grezzi]
        if SORGENTE == "anac":
            return []
        log.warning("ANAC non raggiungibile: passo a TED (solo gare sopra soglia UE)")
    grezzi = scarica_avvisi_ted()
    log.info("Sorgente TED: %d avvisi", len(grezzi))
    return [normalizza_ted(a) for a in grezzi]


def _prova_parametri_anac() -> None:
    """Prova filtri candidati sull'API avvisi e riassume cosa cambia nei risultati.
    Serve a scoprire come filtrare lato server (tipo di scheda, date, dimensione
    pagina), così da non dover scaricare migliaia di affidamenti diretti."""
    ieri = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    candidati = [
        {},
        {"size": 1000},
        {"codiceScheda": "P2_16"},
        {"codiceScheda": "P"},
        {"tipo": "bando"},
        {"tipologia": "BANDO"},
        {"sort": "dataPubblicazione,asc"},
        {"dataPubblicazioneStart": f"{ieri}T00:00:00"},
        {"dataPubblicazioneStart": f"{ieri}T00:00:00.000Z"},
        {"dataPubblicazioneDa": ieri.isoformat()},
        {"dataDa": ieri.isoformat()},
        {"dataPubblicazione": ieri.isoformat()},
        {"q": "manutenzione"},
    ]
    for extra in candidati:
        params = {"page": 0, "size": 20, **extra}
        dati, errore = _get_anac(params)
        if dati is None:
            log.info("[SONDA] ANAC %s → %s", extra, errore[:80])
            continue
        lista, _ = _estrai_lista(dati)
        date_pub = [d for d in (_pubblicazione(a) for a in lista) if d]
        tot = {k: dati.get(k) for k in ("totalElements", "totalPages") if isinstance(dati, dict)}
        log.info("[SONDA] ANAC %s → %d avvisi %s | schede %s | date %s → %s",
                 extra, len(lista), tot, _conta_schede(lista),
                 min(date_pub, default=None), max(date_pub, default=None))


def _cerca_api_nel_sito() -> None:
    """Legge il codice JavaScript del sito Pubblicità Legale per trovare gli
    indirizzi e i parametri che usa la pagina di ricerca bandi."""
    radice = "https://pubblicitalegale.anticorruzione.it/"
    try:
        pagina = requests.get(radice + "bandi", headers=HEADERS_BROWSER, timeout=20).text
    except Exception as e:
        log.info("[SONDA] sito: errore %s", e)
        return
    script = re.findall(r'src="([^"]+\.js)"', pagina)
    log.info("[SONDA] sito: script %s", script)
    trovati: list[str] = []
    for src in script:
        url = src if src.startswith("http") else radice + src.lstrip("/")
        try:
            js = requests.get(url, headers=HEADERS_BROWSER, timeout=30).text
        except Exception:
            continue
        for m in re.finditer(r"api/v\d[^\"'`\s]{0,120}", js):
            if m.group(0) not in trovati:
                trovati.append(m.group(0))
        for m in re.finditer(r"avvisi", js):
            frammento = " ".join(js[max(0, m.start() - 250): m.end() + 350].split())
            if len(trovati) < 60:
                trovati.append("…" + frammento + "…")
    for t in trovati[:60]:
        log.info("[SONDA] sito: %s", t)


def sonda() -> None:
    """Diagnostica: quali sorgenti rispondono da qui (solo con ESPLORA=1)."""
    prove = [
        ("GET", ANAC_API_URL + "?page=0&size=1"),
        ("GET", "https://dati.anticorruzione.it/opendata/api/3/action/package_list"),
        ("POST", TED_API_URL),
    ]
    for metodo, url in prove:
        try:
            if metodo == "GET":
                r = requests.get(url, headers=HEADERS_BROWSER, timeout=20)
            else:
                r = requests.post(url, timeout=20, json={
                    "query": "buyer-country IN (ITA)", "fields": ["publication-number"], "limit": 1})
            log.info("[SONDA] %s %s → HTTP %s", metodo, url, r.status_code)
        except Exception as e:
            log.info("[SONDA] %s %s → errore %s", metodo, url, e)
    _prova_parametri_anac()
    _cerca_api_nel_sito()


# ---------------------------------------------------------------------------
# Filtri profilo
# ---------------------------------------------------------------------------

# Regioni riconosciute dal campo "regioni" del profilo: prefissi ISTAT delle
# province (anche quelle soppresse, ancora presenti in alcuni avvisi), prefisso
# NUTS e parole che indicano la regione nel nome dell'ente o del luogo (per TED
# e per gli avvisi senza codici).
REGIONI = {
    "sardegna": {
        "istat": ("090", "091", "092", "095", "104", "105", "106", "107", "111"),
        "nuts": "ITG2",
        "parole": ("sardegna", "cagliari", "sassari", "nuoro", "oristano", "olbia", "gallura",
                   "ogliastra", "sulcis", "iglesias", "carbonia", "campidano", "quartu",
                   "alghero", "tempio pausania", "lanusei", "tortoli", "sanluri", "villacidro",
                   "abbanoa", "anas sardegna", "forestas", "arst"),
    },
}


def in_regione(g: dict, regione: str) -> bool:
    r = REGIONI.get(regione.strip().lower())
    if not r:
        return True  # regione non in elenco: nessun filtro, meglio una gara in più che una persa
    if g.get("istat") and g["istat"].zfill(6)[:3] in r["istat"]:
        return True
    if g.get("nuts") and g["nuts"].startswith(r["nuts"]):
        return True
    testo = f"{g.get('ente') or ''} {g.get('luogo') or ''}".lower()
    return any(re.search(rf"\b{re.escape(p)}\b", testo) for p in r["parole"])


def _codice_soa(voce: str) -> str | None:
    """'OG 3 - STRADE...' → 'OG3'. None se non è una categoria SOA (OG/OS)."""
    m = re.match(r"\s*(O[GS])\s*(\d+)", voce.upper())
    return f"{m.group(1)}{int(m.group(2))}" if m else None


def passa_filtri(g: dict, profilo: dict) -> tuple[bool, str]:
    testo = f"{g['oggetto']} {g['ente']} {g['luogo'] or ''} {' '.join(g['categorie'])} " \
            f"{' '.join(g['cpv'])}".lower()

    if not g["scadenza"] and g["scheda"]:
        # Sugli avvisi ANAC la scadenza c'è sempre per i bandi aperti; quelli
        # senza sono per lo più vecchie procedure ripubblicate
        return False, "senza scadenza"
    if g["scadenza"] and date.fromisoformat(g["scadenza"]) < datetime.now(timezone.utc).date():
        return False, "scaduta"

    for p in profilo["parole_escluse"]:
        if p.lower() in testo:
            return False, f"esclusa ({p})"

    nature = [n.lower() for n in profilo["nature"]]
    if nature and g["natura"] and g["natura"].lower() not in nature:
        return False, "fuori settore (natura)"

    # Settore, dal criterio più affidabile disponibile per questa gara:
    # 1) categorie SOA (OG/OS, solo ANAC), 2) CPV numerico (TED), 3) parole chiave
    soa_profilo = {c for c in (_codice_soa(x) for x in profilo["categorie_soa"]) if c}
    soa_gara = {c for c in (_codice_soa(x) for x in g["categorie"]) if c}
    cpv_numerici = [c for c in g["cpv"] if str(c)[:2].isdigit()]
    parole = profilo["parole_chiave"]
    if soa_profilo and soa_gara:
        if not soa_profilo & soa_gara:
            return False, "fuori settore (SOA)"
    elif profilo["cpv_prefissi"] and cpv_numerici:
        if not any(str(c).startswith(str(pref)) for c in cpv_numerici
                   for pref in profilo["cpv_prefissi"]):
            return False, "fuori settore (CPV)"
    elif parole and not any(p.lower() in testo for p in parole):
        return False, "fuori settore (parole)"

    if profilo["regioni"] and not any(in_regione(g, r) for r in profilo["regioni"]):
        return False, "fuori regione"
    if profilo["luoghi"] and not any(l.lower() in testo for l in profilo["luoghi"]):
        return False, "fuori zona"

    if g["importo"] is not None:
        if profilo["importo_min"] and g["importo"] < profilo["importo_min"]:
            return False, "importo basso"
        if profilo["importo_max"] and g["importo"] > profilo["importo_max"]:
            return False, "importo alto"

    return True, ""


# ---------------------------------------------------------------------------
# Valutazione AI
# ---------------------------------------------------------------------------

PROMPT_SISTEMA = (
    "Sei un consulente esperto di appalti pubblici italiani. Ricevi il profilo di "
    "un'azienda e i dati di una gara d'appalto. Valuti quanto la gara è adatta e "
    "interessante per quell'azienda.\n"
    "Punteggio da 1 a 10:\n"
    "  9-10 = perfettamente in linea, da non perdere\n"
    "  7-8  = adatta, vale la pena studiare il bando\n"
    "  5-6  = parzialmente adatta o dati incompleti\n"
    "  1-4  = poco o per niente adatta\n"
    "Considera settore, dimensione dell'importo rispetto all'azienda, luogo, "
    "tempo rimasto alla scadenza. Se l'oggetto è vago, abbassa il punteggio.\n"
    "Rispondi SOLO con JSON valido, nessun altro testo:\n"
    '{"score": <int 1-10>, "riassunto": "<cosa chiede l\'ente, in parole semplici, '
    'max 20 parole>", "motivo": "<perché è o non è adatta, max 15 parole>"}'
)


_limite_ai = MAX_CHIAMATE_AI


def valuta_con_ai(g: dict, profilo: dict) -> dict | None:
    global _chiamate_ai
    if AI_OFF or not ANTHROPIC_API_KEY or _chiamate_ai >= _limite_ai:
        return None

    dati = [
        f"PROFILO AZIENDA: {profilo['descrizione_azienda'] or 'non specificato'}",
        "",
        f"Ente: {g['ente']}",
        f"Oggetto: {g['oggetto']}",
        f"Tipo: {g['tipo'] or 'n/d'} {('- ' + g['natura']) if g['natura'] else ''}",
        f"Importo: {formatta_euro(g['importo']) if g['importo'] else 'n/d'}",
        f"Scadenza offerte: {g['scadenza'] or 'n/d'}",
        f"CPV: {', '.join(g['cpv']) or 'n/d'}",
        f"Categorie: {', '.join(g['categorie']) or 'n/d'}",
        f"Luogo: {g['luogo'] or 'n/d'}",
    ]

    try:
        _chiamate_ai += 1
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODELLO_AI,
                "max_tokens": 250,
                "system": PROMPT_SISTEMA,
                "messages": [{"role": "user", "content": "\n".join(dati)}],
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.warning("    AI HTTP %s → %s", r.status_code, r.text[:160])
            return None
        testo = r.json()["content"][0]["text"].strip()
        m = re.search(r"\{.*\}", testo, re.DOTALL)
        if not m:
            log.warning("    AI: risposta non interpretabile")
            return None
        parsed = json.loads(m.group(0))
        score = int(parsed.get("score", 0))
        if not 1 <= score <= 10:
            return None
        return {
            "score": score,
            "riassunto": str(parsed.get("riassunto", "")).strip()[:200],
            "motivo": str(parsed.get("motivo", "")).strip()[:150],
        }
    except Exception as e:
        log.warning("    AI errore: %s", e)
        return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def formatta_euro(v: float) -> str:
    return f"€{v:,.0f}".replace(",", ".")


def barra_score(score: int) -> str:
    if score >= 9:
        return "🟢🟢🟢"
    if score >= 7:
        return "🟢🟢"
    if score >= 5:
        return "🟡"
    return "🔴"


def componi_messaggio(g: dict, valutazione: dict | None) -> str:
    e = html.escape
    righe = ["📑 <b>LUPIN GARE – Nuovo bando</b>"]
    if valutazione:
        righe.append(f"{barra_score(valutazione['score'])} <b>Adatta {valutazione['score']}/10</b>")
    righe.append(f"🏛 {e(g['ente'])}")
    righe.append(f"📝 <b>{e(g['oggetto'][:250])}</b>")
    if g["importo"]:
        righe.append(f"💰 <b>{formatta_euro(g['importo'])}</b>")
    if g["scadenza"]:
        sc = date.fromisoformat(g["scadenza"])
        giorni = (sc - datetime.now(timezone.utc).date()).days
        righe.append(f"⏰ Scadenza {sc.strftime('%d/%m/%Y')} (tra {giorni} giorni)")
    if g["luogo"]:
        righe.append(f"📍 {e(g['luogo'])}")
    if g["cpv"]:
        righe.append(f"🏷 CPV {e(', '.join(g['cpv'][:3]))}")
    if g["categorie"]:
        righe.append(f"🧱 {e(', '.join(g['categorie'][:3]))}")
    if g["cig"]:
        righe.append(f"🔖 CIG {e(g['cig'])}")
    if valutazione and valutazione["riassunto"]:
        righe.append(f"🤖 <i>{e(valutazione['riassunto'])}</i>")
    if valutazione and valutazione["motivo"]:
        righe.append(f"💡 <i>{e(valutazione['motivo'])}</i>")
    righe.append(f'🔗 <a href="{e(g["url"], quote=True)}">Apri bando</a>')
    return "\n".join(righe)


def invia_telegram(testo: str, chat_id: str = "") -> bool:
    chat_id = chat_id or TELEGRAM_CHAT_ID
    if DRY_RUN or not TELEGRAM_TOKEN or not chat_id:
        log.info("[DRY] %s", testo.replace("\n", " | ")[:300])
        return True
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": testo,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("Telegram HTTP %s → %s", r.status_code, r.text[:180])
            return False
        return True
    except Exception as e:
        log.warning("Telegram errore: %s", e)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=== LUPIN GARE %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))
    if DRY_RUN:
        log.info("Modalità DRY_RUN (nessun Telegram reale, memoria non salvata)")
    if AI_OFF or not ANTHROPIC_API_KEY:
        log.info("Valutazione AI disattivata")

    global _limite_ai
    profili = carica_profili()
    _limite_ai = MAX_CHIAMATE_AI * len(profili)
    log.info("Profili attivi: %s", ", ".join(p["_nome"] for p in profili))
    memoria = carica_memoria()
    if RIPROVA:
        log.info("RIPROVA: memoria e segnalibro ignorati per questo avvio")
        memoria = {"_stato": {}}
    log.info("Memoria iniziale: %d gare | sorgente %s | ultimo avviso ANAC visto: %s",
             len([k for k in memoria if not k.startswith("_")]), SORGENTE,
             (memoria.get("_stato") or {}).get("anac_ultima_pubblicazione", "nessuno"))
    if ESPLORA:
        sonda()

    stato = memoria.setdefault("_stato", {})
    if RECUPERO:
        segnalibro = stato.pop("anac_ultima_pubblicazione", None)
        riaperte = 0
        for k in [k for k in memoria if not k.startswith("_")]:
            m = re.fullmatch(r"score (\d+)", str(memoria[k].get("scartata") or ""))
            if m and SOGLIA_SCORE and int(m.group(1)) >= SOGLIA_SCORE:
                del memoria[k]
                riaperte += 1
        log.info("RECUPERO: ultimi %d giorni, segnalibro ignorato (%s), %d gare riaperte per la nuova soglia",
                 GIORNI_INDIETRO, segnalibro or "nessuno", riaperte)
    stato_prima = dict(stato)
    gare = scarica_gare(stato)

    scarti: dict[str, int] = {}
    inviati = 0
    notificate: list[dict] = []
    senza_voto = 0
    per_profilo: dict[str, int] = {p["_nome"]: 0 for p in profili}
    for profilo in profili:
        for g in gare:
            chiave = chiave_memoria(profilo, g["id"])
            if chiave in memoria:
                continue

            ok, motivo_scarto = passa_filtri(g, profilo)
            dati = {k: g[k] for k in ("oggetto", "ente", "importo", "scadenza", "cig", "luogo")}
            dati["prima_vista"] = datetime.now(timezone.utc).isoformat()
            if not ok:
                if not profilo["_id"]:
                    chiave_s = motivo_scarto.split(" (")[0] if motivo_scarto.startswith("esclusa") else motivo_scarto
                    scarti[chiave_s] = scarti.get(chiave_s, 0) + 1
                    dati["scartata"] = motivo_scarto
                    memoria[chiave] = dati
                else:
                    # Per le imprese clienti si tiene in memoria solo l'esito,
                    # senza ripetere i dati della gara
                    memoria[chiave] = {"scartata": motivo_scarto}
                continue

            valutazione = valuta_con_ai(g, profilo)
            if SOGLIA_SCORE and not valutazione:
                # Con la soglia attiva non si invia una gara senza voto (limite di
                # chiamate AI raggiunto o errore): non va in memoria, ci si riprova
                # al prossimo avvio
                senza_voto += 1
                continue
            if not profilo["_id"]:
                registro = dati
            else:
                registro = {"prima_vista": dati["prima_vista"]}
            if valutazione:
                registro["score"] = valutazione["score"]
                registro["motivo_ai"] = valutazione["motivo"]
                if valutazione["score"] < SOGLIA_SCORE:
                    registro["scartata"] = f"score {valutazione['score']}"
                    memoria[chiave] = registro
                    log.info("  – [%s] %s scartata (score %d)", profilo["_nome"], g["id"], valutazione["score"])
                    continue

            if invia_telegram(componi_messaggio(g, valutazione), profilo["_chat"]):
                inviati += 1
                per_profilo[profilo["_nome"]] += 1
                if not profilo["_id"]:
                    notificate.append(g | {"score": (valutazione or {}).get("score")})
                memoria[chiave] = registro
                log.info("  ✓ [%s] %s", profilo["_nome"], g["id"])
                time.sleep(1.3)
            else:
                log.warning("  ✗ [%s] fallito invio %s", profilo["_nome"], g["id"])

    if scarti:
        log.info("Scartate dai filtri: %s", scarti)
    if senza_voto:
        log.warning("%d gare non valutate dall'AI rimandate al prossimo avvio", senza_voto)
        # Il segnalibro resta dov'era, così il prossimo avvio le riscarica
        stato.clear()
        stato.update(stato_prima)
    if DRY_RUN:
        log.info("DRY_RUN: memoria non salvata")
    else:
        salva_memoria(memoria)
    log.info("=== Fine. Notifiche: %d | Memoria: %d | Chiamate AI: %d ===",
             inviati, len(memoria) - 1, _chiamate_ai)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Riepilogo come annotazioni: si leggono dalla pagina dell'avvio senza aprire i log
        def ann(titolo, testo):
            print(f"::notice title={titolo}::" + " ".join(str(testo).split()).replace("%", "%25"))
        if len(profili) > 1:
            ann("Profili", " · ".join(f"{k}: {v} gare" for k, v in per_profilo.items()))
        ann("Lupin Gare", f"{len(gare)} avvisi scaricati, {inviati} gare notificate"
                          f"{' (DRY_RUN)' if DRY_RUN else ''}. Scarti: {scarti or 'nessuno'}")
        for g in notificate[:8]:
            imp = f"{g['importo']:,.0f} €".replace(",", ".") if g.get("importo") else "importo n/d"
            ann(f"Gara {g.get('score') or '-'}/10", f"{g['oggetto'][:150]} | {g['ente']} | {g['luogo'] or ''} | "
                f"{imp} | scade {g['scadenza'] or 'n/d'} | CIG {g['cig'] or 'n/d'}")


if __name__ == "__main__":
    main()
