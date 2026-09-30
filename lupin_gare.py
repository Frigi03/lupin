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
  DRY_RUN=1        → nessun invio Telegram reale e memoria non salvata
  AI_OFF=1         → disattiva la valutazione AI
  SOGLIA_SCORE=6   → notifica solo gare con punteggio >= 6 (default 0 = tutte)
  GIORNI_INDIETRO=2 → quanti giorni di pubblicazioni scaricare (default 2)
  ESPLORA=1        → stampa nei log la struttura grezza dei primi avvisi e
                     controlla quali sorgenti rispondono (sonda)
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


def carica_profilo() -> dict:
    profilo = {
        "descrizione_azienda": "",
        "parole_chiave": [],
        "parole_escluse": [],
        "cpv_prefissi": [],
        "luoghi": [],
        "importo_min": 0,
        "importo_max": 0,
    }
    if FILE_PROFILO.exists():
        with FILE_PROFILO.open("r", encoding="utf-8") as f:
            profilo.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    return profilo


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


def normalizza(avviso: dict) -> dict:
    """Estrae dai dati grezzi i campi che servono a Lupin."""
    cig = cerca(avviso, "cig")
    id_avviso = cerca(avviso, "idAvviso", "id_avviso", "idScheda", "codiceScheda")
    oggetto = cerca(avviso, "oggetto_gara", "oggettoGara", "oggetto_lotto", "oggetto", "descrizione", "titolo")
    ente = cerca(avviso, "denominazione_amministrazione", "denominazioneAmministrazione",
                 "stazione_appaltante", "denominazione", "amministrazione")
    importo = a_numero(cerca(avviso, "importo_complessivo", "valore_complessivo",
                             "importo_totale", "importo_lotto", "importo", "valore"))
    scadenza = a_data(cerca(avviso, "termine_ricezione", "terminericezione", "scadenza",
                            "data_scadenza", "termine_presentazione"))
    pubblicazione = a_data(cerca(avviso, "data_pubblicazione", "dataPubblicazione"))
    cpv = list(dict.fromkeys(str(c) for c in cerca(avviso, "cpv", tutti=True)))[:5]
    luogo = cerca(avviso, "luogo_esecuzione", "luogo", "provincia", "comune", "regione", "nuts")
    tipo = cerca(avviso, "tipo_appalto", "tipologia", "oggetto_principale_contratto", "tipo")
    url = None
    for v in cerca(avviso, "url", "link", "indirizzo", tutti=True):
        if isinstance(v, str) and v.startswith("http"):
            url = v
            break

    if id_avviso:
        gid = f"A{id_avviso}"
    elif cig:
        gid = f"C{cig}"
    else:
        gid = "H" + hashlib.sha1(f"{ente}|{oggetto}|{importo}".encode()).hexdigest()[:16]

    return {
        "id": gid,
        "cig": str(cig) if cig else None,
        "oggetto": str(oggetto or "Oggetto non indicato").strip()[:400],
        "ente": str(ente or "Ente non indicato").strip()[:150],
        "importo": importo,
        "scadenza": scadenza.isoformat() if scadenza else None,
        "pubblicazione": pubblicazione.isoformat() if pubblicazione else None,
        "cpv": cpv,
        "luogo": str(luogo)[:80] if luogo else None,
        "tipo": str(tipo)[:60] if tipo else None,
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


def _esplora_pagina_anac(lista: list[dict], pagina: int) -> None:
    schede: dict[str, int] = {}
    date_pub = []
    for a in lista:
        k = str(a.get("codiceScheda"))
        schede[k] = schede.get(k, 0) + 1
        d = a_data(cerca(a, "data_pubblicazione", "dataPubblicazione"))
        if d:
            date_pub.append(d)
    log.info("[ESPLORA] ANAC pagina %d: tipi scheda %s | pubblicazione da %s a %s",
             pagina, schede, min(date_pub, default=None), max(date_pub, default=None))
    if pagina == 0:
        for i, a in enumerate(lista[:2]):
            log.info("[ESPLORA] ANAC avviso %d grezzo:\n%s", i,
                     json.dumps(a, ensure_ascii=False, indent=1)[:6000])
            log.info("[ESPLORA] ANAC avviso %d normalizzato: %s", i,
                     json.dumps(normalizza(a), ensure_ascii=False))


def scarica_avvisi_anac() -> list[dict] | None:
    """Ritorna la lista avvisi, oppure None se ANAC non risponde.

    Prima prova il filtro per data lato server. Se ANAC lo rifiuta (finora
    risponde HTTP 500), scarica le pagine senza filtro e scarta in locale gli
    avvisi più vecchi di GIORNI_INDIETRO, fermandosi alla prima pagina
    interamente vecchia."""
    oggi = datetime.now(timezone.utc).date()
    inizio = oggi - timedelta(days=GIORNI_INDIETRO)
    base = {"size": DIMENSIONE_PAGINA}
    filtro_date = {
        "dataPubblicazioneStart": inizio.isoformat(),
        "dataPubblicazioneEnd": oggi.isoformat(),
    }

    dati, errore = _get_anac({**base, **filtro_date, "page": 0})
    usa_filtro = dati is not None
    if not usa_filtro:
        log.warning("ANAC con filtro date: %s", errore)
        log.info("Riprovo ANAC senza filtro date (filtro in locale)")
        dati, errore = _get_anac({**base, "page": 0})
        if dati is None:
            log.error("ANAC %s", errore)
            return None

    avvisi: list[dict] = []
    for pagina in range(MAX_PAGINE):
        if pagina > 0:
            time.sleep(1)
            params = {**base, "page": pagina, **(filtro_date if usa_filtro else {})}
            dati, errore = _get_anac(params)
            if dati is None:
                log.error("ANAC pagina %d: %s", pagina, errore)
                break

        lista, altre = _estrai_lista(dati)
        if ESPLORA and pagina < 3:
            _esplora_pagina_anac(lista, pagina)

        recenti = lista
        if not usa_filtro:
            recenti = []
            for a in lista:
                d = a_data(cerca(a, "data_pubblicazione", "dataPubblicazione"))
                if d is None or d >= inizio:
                    recenti.append(a)
        avvisi.extend(recenti)
        log.info("  ANAC pagina %d: %d avvisi (%d recenti)", pagina, len(lista), len(recenti))

        if not altre or not lista:
            break
        if not usa_filtro and not recenti:
            break  # pagina interamente più vecchia del periodo richiesto
    return avvisi


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
        "luogo": _testo_ted(n.get("buyer-city")) or _testo_ted(n.get("place-of-performance")),
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


def scarica_gare() -> list[dict]:
    """Ritorna le gare già normalizzate dalla sorgente scelta."""
    if SORGENTE in ("anac", "auto"):
        grezzi = scarica_avvisi_anac()
        if grezzi is not None:
            log.info("Sorgente ANAC: %d avvisi", len(grezzi))
            return [normalizza(a) for a in grezzi]
        if SORGENTE == "anac":
            return []
        log.warning("ANAC non raggiungibile: passo a TED (solo gare sopra soglia UE)")
    grezzi = scarica_avvisi_ted()
    log.info("Sorgente TED: %d avvisi", len(grezzi))
    return [normalizza_ted(a) for a in grezzi]


def _stampa_parametri_api(r) -> None:
    """Dalla documentazione OpenAPI stampa i parametri accettati per gli avvisi."""
    try:
        doc = r.json()
    except ValueError:
        return
    for path, metodi in (doc.get("paths") or {}).items():
        if "avvis" not in path.lower():
            continue
        for metodo, op in metodi.items():
            nomi = [f"{x.get('name')}({(x.get('schema') or {}).get('type', '?')}"
                    f"{'/' + (x.get('schema') or {}).get('format') if (x.get('schema') or {}).get('format') else ''})"
                    for x in op.get("parameters", []) if isinstance(x, dict)]
            log.info("[SONDA] API %s %s → parametri: %s", metodo.upper(), path, ", ".join(nomi))


def sonda() -> None:
    """Diagnostica: quali sorgenti rispondono da qui (solo con ESPLORA=1)."""
    prove = [
        ("GET", "https://pubblicitalegale.anticorruzione.it/"),
        ("GET", "https://pubblicitalegale.anticorruzione.it/bandi"),
        ("GET", ANAC_API_URL + "?page=0&size=1"),
        ("GET", "https://pubblicitalegale.anticorruzione.it/api/v0/v3/api-docs"),
        ("GET", "https://pubblicitalegale.anticorruzione.it/v3/api-docs"),
        ("GET", "https://pubblicitalegale.anticorruzione.it/api/v3/api-docs"),
        ("GET", "https://dati.anticorruzione.it/opendata/api/3/action/package_list"),
        ("GET", "https://www.anticorruzione.it/"),
        ("POST", TED_API_URL),
    ]
    for metodo, url in prove:
        try:
            if metodo == "GET":
                r = requests.get(url, headers=HEADERS_BROWSER, timeout=20)
            else:
                r = requests.post(url, timeout=20, json={
                    "query": "buyer-country IN (ITA)", "fields": ["publication-number"], "limit": 1})
            corpo = " ".join(r.text.split())[:150]
            if "api-docs" in url and r.status_code == 200:
                _stampa_parametri_api(r)
            log.info("[SONDA] %s %s → HTTP %s | %s", metodo, url, r.status_code, corpo)
        except Exception as e:
            log.info("[SONDA] %s %s → errore %s", metodo, url, e)
    try:
        ip = requests.get("https://ipinfo.io/json", timeout=10).json()
        log.info("[SONDA] questo server: paese %s, rete %s", ip.get("country"), ip.get("org"))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Filtri profilo
# ---------------------------------------------------------------------------

def passa_filtri(g: dict, profilo: dict) -> tuple[bool, str]:
    testo = f"{g['oggetto']} {g['ente']} {g['luogo'] or ''}".lower()

    if g["scadenza"] and date.fromisoformat(g["scadenza"]) < datetime.now(timezone.utc).date():
        return False, "scaduta"

    for p in profilo["parole_escluse"]:
        if p.lower() in testo:
            return False, f"esclusa ({p})"

    parole = profilo["parole_chiave"]
    cpv_pref = profilo["cpv_prefissi"]
    if cpv_pref and g["cpv"]:
        # Il CPV è la classificazione ufficiale: se c'è, decide lui. Le parole
        # chiave da sole fanno passare troppo ("manutenzione" di barelle, software...)
        if not any(str(c).startswith(str(pref)) for c in g["cpv"] for pref in cpv_pref):
            return False, "fuori settore"
    elif parole and not any(p.lower() in testo for p in parole):
        return False, "fuori settore"

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


def valuta_con_ai(g: dict, profilo: dict) -> dict | None:
    global _chiamate_ai
    if AI_OFF or not ANTHROPIC_API_KEY or _chiamate_ai >= MAX_CHIAMATE_AI:
        return None

    dati = [
        f"PROFILO AZIENDA: {profilo['descrizione_azienda'] or 'non specificato'}",
        "",
        f"Ente: {g['ente']}",
        f"Oggetto: {g['oggetto']}",
        f"Tipo: {g['tipo'] or 'n/d'}",
        f"Importo: {formatta_euro(g['importo']) if g['importo'] else 'n/d'}",
        f"Scadenza offerte: {g['scadenza'] or 'n/d'}",
        f"CPV: {', '.join(g['cpv']) or 'n/d'}",
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
    if g["cig"]:
        righe.append(f"🔖 CIG {e(g['cig'])}")
    if valutazione and valutazione["riassunto"]:
        righe.append(f"🤖 <i>{e(valutazione['riassunto'])}</i>")
    if valutazione and valutazione["motivo"]:
        righe.append(f"💡 <i>{e(valutazione['motivo'])}</i>")
    righe.append(f'🔗 <a href="{e(g["url"], quote=True)}">Apri bando</a>')
    return "\n".join(righe)


def invia_telegram(testo: str) -> bool:
    if DRY_RUN or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("[DRY] %s", testo.replace("\n", " | ")[:300])
        return True
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
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

    profilo = carica_profilo()
    memoria = carica_memoria()
    log.info("Memoria iniziale: %d gare | ultimi %d giorni | sorgente %s",
             len(memoria), GIORNI_INDIETRO, SORGENTE)
    if ESPLORA:
        sonda()

    gare = scarica_gare()

    scarti: dict[str, int] = {}
    inviati = 0
    for g in gare:
        if g["id"] in memoria:
            continue

        ok, motivo_scarto = passa_filtri(g, profilo)
        dati = {k: g[k] for k in ("oggetto", "ente", "importo", "scadenza", "cig", "luogo")}
        dati["prima_vista"] = datetime.now(timezone.utc).isoformat()
        if not ok:
            scarti[motivo_scarto.split(" ")[0]] = scarti.get(motivo_scarto.split(" ")[0], 0) + 1
            dati["scartata"] = motivo_scarto
            memoria[g["id"]] = dati
            continue

        valutazione = valuta_con_ai(g, profilo)
        if valutazione:
            dati["score"] = valutazione["score"]
            dati["motivo_ai"] = valutazione["motivo"]
            if valutazione["score"] < SOGLIA_SCORE:
                dati["scartata"] = f"score {valutazione['score']}"
                memoria[g["id"]] = dati
                log.info("  – %s scartata (score %d)", g["id"], valutazione["score"])
                continue

        if invia_telegram(componi_messaggio(g, valutazione)):
            inviati += 1
            memoria[g["id"]] = dati
            log.info("  ✓ %s", g["id"])
            time.sleep(1.3)
        else:
            log.warning("  ✗ fallito invio %s", g["id"])

    if scarti:
        log.info("Scartate dai filtri: %s", scarti)
    if DRY_RUN:
        log.info("DRY_RUN: memoria non salvata")
    else:
        salva_memoria(memoria)
    log.info("=== Fine. Notifiche: %d | Memoria: %d | Chiamate AI: %d ===",
             inviati, len(memoria), _chiamate_ai)


if __name__ == "__main__":
    main()
