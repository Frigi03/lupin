#!/usr/bin/env python3
"""
Lupin – Scraper annunci Wikicasa → Telegram
Versione con valutazione AI (2026-09)

Novità rispetto alla versione con arricchimento dati:
- Valutazione LLM di ogni nuovo annuncio: punteggio 1-10 + motivazione breve
  ("occasione", "prezzo giusto", "sopravvalutato", ecc.)
- Il punteggio viene salvato in memoria insieme agli altri dati
- Filtro opzionale: notifica solo annunci con punteggio >= SOGLIA_SCORE
- Tetto massimo di chiamate AI per run (controllo costi)
- Se manca la API key o l'AI fallisce, lo script continua a funzionare
  esattamente come prima (fallback silenzioso)

Variabili d'ambiente richieste (GitHub Secrets):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY
Opzionali:
  DRY_RUN=1        → nessun invio Telegram reale
  AI_OFF=1         → disattiva la valutazione AI
  SOGLIA_SCORE=7   → notifica solo annunci con punteggio >= 7 (default 0 = tutti)
  TEST_AI=3        → valuta 3 annunci già noti per città e stampa i punteggi nei log,
                     senza notificare né toccare la memoria (serve solo a verificare l'AI)
"""

import os
import re
import json
import time
import random
import logging
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FILE_MEMORIA = Path("annunci_memoria.json")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
AI_OFF = os.environ.get("AI_OFF", "0") == "1"
SOGLIA_SCORE = int(os.environ.get("SOGLIA_SCORE", "0"))  # 0 = notifica tutto
MAX_CHIAMATE_AI = int(os.environ.get("MAX_CHIAMATE_AI", "60"))  # tetto costi per run
TEST_AI = int(os.environ.get("TEST_AI", "0"))  # >0 = valuta N annunci già noti per città (solo test)
MODELLO_AI = os.environ.get("MODELLO_AI", "claude-haiku-4-5-20251001")

MIN_PREZZO = 40_000
MAX_PREZZO = 2_500_000
SOGLIA_SOTTO_MEDIA = 0.10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

POSTI = [
    {"nome": "Milano",  "url": "https://www.wikicasa.it/vendita-case/milano"},
    {"nome": "Roma",    "url": "https://www.wikicasa.it/vendita-case/roma"},
    {"nome": "Torino",  "url": "https://www.wikicasa.it/vendita-case/torino"},
    {"nome": "Napoli",  "url": "https://www.wikicasa.it/vendita-case/napoli"},
    {"nome": "Bologna", "url": "https://www.wikicasa.it/vendita-case/bologna"},
    {"nome": "Firenze", "url": "https://www.wikicasa.it/vendita-case/firenze"},
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lupin")

# Contatore globale delle chiamate AI fatte in questo run
_chiamate_ai = 0


# ---------------------------------------------------------------------------
# Memoria (JSON: id -> dati arricchiti)
# ---------------------------------------------------------------------------

def carica_memoria() -> dict:
    """Carica la memoria JSON. Se esiste ancora il vecchio annunci_inviati.txt
    (solo ID), lo migra automaticamente al nuovo formato."""
    if FILE_MEMORIA.exists():
        try:
            with FILE_MEMORIA.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            log.warning("Memoria JSON corrotta, riparto da vuota")
            return {}

    vecchio_file = Path("annunci_inviati.txt")
    if vecchio_file.exists():
        log.info("Migrazione da annunci_inviati.txt al nuovo formato JSON")
        memoria = {}
        with vecchio_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m = re.search(r"/annuncio/(\d+)", line) or re.fullmatch(r"(\d+)", line)
                if m:
                    memoria[m.group(1)] = {"prima_vista": datetime.now(timezone.utc).isoformat()}
        return memoria

    return {}


def salva_memoria(memoria: dict) -> None:
    with FILE_MEMORIA.open("w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def invia_telegram(testo: str) -> bool:
    if DRY_RUN or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("[DRY] %s", testo.replace("\n", " | ")[:140])
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": testo,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
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
# Valutazione AI
# ---------------------------------------------------------------------------

PROMPT_SISTEMA = (
    "Sei un analista immobiliare italiano. Ricevi i dati grezzi di un annuncio "
    "di vendita e lo valuti come farebbe un investitore esperto che cerca affari.\n"
    "Assegna un punteggio da 1 a 10:\n"
    "  9-10 = occasione rara, prezzo nettamente sotto mercato\n"
    "  7-8  = buon affare, merita una visita\n"
    "  5-6  = prezzo in linea col mercato, nulla di speciale\n"
    "  3-4  = sopravvalutato o dati poco chiari\n"
    "  1-2  = da evitare o annuncio sospetto\n"
    "Considera: prezzo/mq rispetto alla media di zona, dimensione, "
    "indizi nel titolo (da ristrutturare, asta, nuda proprietà, piano terra, "
    "seminterrato, zona). Se mancano dati chiave (prezzo o mq), abbassa il punteggio "
    "e segnalalo.\n"
    "Rispondi SOLO con JSON valido, nessun altro testo:\n"
    '{"score": <int 1-10>, "motivo": "<max 15 parole in italiano>"}'
)


def valuta_con_ai(annuncio: dict, citta: str, media_zona: float | None) -> dict | None:
    """Chiede all'LLM un punteggio 1-10 + motivazione. Ritorna None se non disponibile."""
    global _chiamate_ai

    if AI_OFF or not ANTHROPIC_API_KEY:
        return None
    if _chiamate_ai >= MAX_CHIAMATE_AI:
        return None

    dati = [
        f"Città: {citta}",
        f"Titolo: {annuncio.get('titolo') or 'n/d'}",
        f"Prezzo: {annuncio.get('prezzo') or 'n/d'} €",
        f"Superficie: {annuncio.get('mq') or 'n/d'} mq",
        f"Prezzo/mq: {annuncio.get('prezzo_mq') or 'n/d'} €/mq",
    ]
    if media_zona:
        dati.append(f"Media prezzo/mq in città (dati Lupin): {media_zona} €/mq")
    else:
        dati.append("Media di zona: non ancora disponibile")

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
                "max_tokens": 150,
                "system": PROMPT_SISTEMA,
                "messages": [{"role": "user", "content": "\n".join(dati)}],
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.warning("    AI HTTP %s → %s", r.status_code, r.text[:160])
            return None

        testo = r.json()["content"][0]["text"].strip()
        # Il modello a volte incapsula il JSON in un blocco markdown: lo ripuliamo
        m = re.search(r"\{.*\}", testo, re.DOTALL)
        if not m:
            log.warning("    AI: risposta non interpretabile")
            return None

        parsed = json.loads(m.group(0))
        score = int(parsed.get("score", 0))
        motivo = str(parsed.get("motivo", "")).strip()
        if not 1 <= score <= 10:
            return None
        return {"score": score, "motivo": motivo[:120]}

    except Exception as e:
        log.warning("    AI errore: %s", e)
        return None


def barra_score(score: int) -> str:
    """Rappresentazione visiva del punteggio per Telegram."""
    if score >= 9:
        return "🟢🟢🟢"
    if score >= 7:
        return "🟢🟢"
    if score >= 5:
        return "🟡"
    return "🔴"


# ---------------------------------------------------------------------------
# Estrazione e arricchimento
# ---------------------------------------------------------------------------

def estrai_mq(text: str) -> int | None:
    """Cerca pattern tipo '80 mq', '80 m²', '80mq' nel testo dell'annuncio."""
    m = re.search(r"(\d{2,4})\s*m(?:q|²|2)\b", text, re.IGNORECASE)
    if m:
        try:
            val = int(m.group(1))
            if 10 <= val <= 2000:
                return val
        except ValueError:
            pass
    return None


def estrai_annunci(page) -> list[dict]:
    articles = page.locator("article[data-cy='real-estate-insertion']").all()
    risultati = []

    for art in articles:
        try:
            aid = art.get_attribute("data-id") or art.get_attribute("id")
            if not aid:
                continue
            relative = art.get_attribute("data-url") or f"/annuncio/{aid}"
            full_url = f"https://www.wikicasa.it{relative}" if relative.startswith("/") else relative

            text = art.inner_text() or ""

            prezzo = None
            m = re.search(r"€\s*([\d]{1,3}(?:\.\d{3})+)", text)
            if m:
                try:
                    val = int(m.group(1).replace(".", ""))
                    if MIN_PREZZO <= val <= MAX_PREZZO:
                        prezzo = val
                except ValueError:
                    pass

            mq = estrai_mq(text)
            prezzo_mq = round(prezzo / mq) if (prezzo and mq) else None

            titolo = ""
            for lk in art.locator("a[href*='/annuncio/']").all():
                t = (lk.inner_text() or "").strip()
                if len(t) > 15:
                    titolo = t
                    break
            if not titolo:
                lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
                titolo = lines[1] if len(lines) > 1 else (lines[0] if lines else f"Annuncio #{aid}")

            risultati.append({
                "id": aid,
                "url": full_url,
                "prezzo": prezzo,
                "mq": mq,
                "prezzo_mq": prezzo_mq,
                "titolo": titolo[:100],
            })
        except Exception:
            continue

    return risultati


def media_prezzo_mq_zona(memoria: dict, citta: str) -> float | None:
    """Media prezzo/mq calcolata sugli annunci già noti per quella città."""
    valori = [
        d["prezzo_mq"] for d in memoria.values()
        if d.get("città") == citta and d.get("prezzo_mq")
    ]
    return round(mean(valori)) if len(valori) >= 5 else None


def giorni_online(prima_vista_iso: str) -> int:
    prima_vista = datetime.fromisoformat(prima_vista_iso)
    return (datetime.now(timezone.utc) - prima_vista).days


# ---------------------------------------------------------------------------
# Apertura pagina (robusta)
# ---------------------------------------------------------------------------

SELETTORE_ANNUNCI = "article[data-cy='real-estate-insertion']"

# Pulsanti di consenso cookie visti su Wikicasa / CMP comuni
SELETTORI_CONSENSO = [
    "#onetrust-accept-btn-handler",
    "button#didomi-notice-agree-button",
    "button[aria-label*='Accetta']",
    "button:has-text('Accetta tutto')",
    "button:has-text('Accetta tutti')",
    "button:has-text('Accetta')",
    "button:has-text('Ho capito')",
]


def chiudi_consenso(page) -> bool:
    """Prova a chiudere il banner cookie. True se ha cliccato qualcosa."""
    for sel in SELETTORI_CONSENSO:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=3000)
                log.info("  Banner consenso chiuso (%s)", sel)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def diagnostica(page, nome: str) -> None:
    """Quando gli annunci non compaiono, stampa cosa c'e' davvero nella pagina."""
    try:
        titolo = page.title()
    except Exception:
        titolo = "?"
    try:
        url_finale = page.url
    except Exception:
        url_finale = "?"
    try:
        testo = (page.locator("body").inner_text(timeout=5000) or "").strip()
        testo = " ".join(testo.split())[:300]
    except Exception:
        testo = "?"
    log.error("  [DIAG %s] titolo=%r url=%s", nome, titolo, url_finale)
    log.error("  [DIAG %s] body: %s", nome, testo)


def apri_lista(page, url: str, nome: str) -> bool:
    """Apre la pagina citta' e aspetta gli annunci. Due tentativi, poi diagnostica.

    networkidle su Wikicasa non arriva mai (script sempre attivi) -> usiamo
    domcontentloaded + attesa esplicita del selettore degli annunci.
    """
    for tentativo in (1, 2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeout:
            log.warning("  Tentativo %d: timeout nel caricamento di %s", tentativo, nome)
            continue

        chiudi_consenso(page)

        # Alcune liste montano gli annunci solo dopo uno scroll
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            pass

        try:
            page.wait_for_selector(SELETTORE_ANNUNCI, timeout=25_000)
            page.wait_for_timeout(random.randint(1200, 2500))
            return True
        except PlaywrightTimeout:
            log.warning("  Tentativo %d: annunci non comparsi su %s", tentativo, nome)
            if tentativo == 1:
                page.wait_for_timeout(random.randint(3000, 6000))

    diagnostica(page, nome)
    log.error("  Nessun annuncio caricato su %s", nome)
    return False


# ---------------------------------------------------------------------------
# Missione per città
# ---------------------------------------------------------------------------

def missione(citta: dict, memoria: dict) -> tuple[int, dict]:
    nome = citta["nome"]
    url = citta["url"]
    aggiornamenti: dict = {}
    inviati = 0

    log.info("▶ %s", nome)

    media_zona = media_prezzo_mq_zona(memoria, nome)
    if media_zona:
        log.info("  Media prezzo/mq %s: €%s", nome, media_zona)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="it-IT",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()

        try:
            if not apri_lista(page, url, nome):
                return inviati, aggiornamenti

            annunci = estrai_annunci(page)
            log.info("  %d annunci trovati", len(annunci))

            # Modalità test AI: valuta annunci già in memoria solo per verificare
            # che l'integrazione funzioni. Non notifica e non modifica la memoria.
            if TEST_AI:
                for a in annunci[:TEST_AI]:
                    v = valuta_con_ai(a, nome, media_zona)
                    if v:
                        log.info("  [TEST AI] %d/10 – %s | %s",
                                 v["score"], v["motivo"], a["titolo"][:50])
                    else:
                        log.warning("  [TEST AI] nessuna valutazione per %s", a["id"])

            for a in annunci:
                if a["id"] in memoria:
                    continue  # già visto: non rinotificare

                ora = datetime.now(timezone.utc).isoformat()
                dati = {
                    "città": nome,
                    "prima_vista": ora,
                    "prezzo": a["prezzo"],
                    "mq": a["mq"],
                    "prezzo_mq": a["prezzo_mq"],
                    "titolo": a["titolo"],
                }

                sotto_media = False
                if a["prezzo_mq"] and media_zona:
                    sotto_media = a["prezzo_mq"] <= media_zona * (1 - SOGLIA_SOTTO_MEDIA)
                dati["sotto_media"] = sotto_media

                # --- Valutazione AI ---
                valutazione = valuta_con_ai(a, nome, media_zona)
                if valutazione:
                    dati["score"] = valutazione["score"]
                    dati["motivo_ai"] = valutazione["motivo"]

                # L'annuncio resta in memoria comunque (non lo rivalutiamo domani),
                # ma sotto soglia non manda notifica.
                if valutazione and valutazione["score"] < SOGLIA_SCORE:
                    aggiornamenti[a["id"]] = dati
                    log.info("  – %s scartato (score %d)", a["id"], valutazione["score"])
                    continue

                righe = [f"🚨 *LUPIN – Nuovo a {nome.upper()}*"]
                if valutazione:
                    righe.append(
                        f"{barra_score(valutazione['score'])} *Punteggio {valutazione['score']}/10*"
                    )
                if a["prezzo"]:
                    righe.append(f"💰 *€{a['prezzo']:,}*".replace(",", "."))
                if a["mq"]:
                    righe.append(f"📐 {a['mq']} mq")
                if a["prezzo_mq"]:
                    extra = ""
                    if media_zona:
                        diff_pct = round((a["prezzo_mq"] / media_zona - 1) * 100)
                        extra = f" (media zona €{media_zona}/mq, {diff_pct:+d}%)"
                    righe.append(f"📊 €{a['prezzo_mq']}/mq{extra}")
                if sotto_media:
                    righe.append("✅ *Sotto media di zona*")
                if valutazione and valutazione["motivo"]:
                    righe.append(f"🤖 _{valutazione['motivo']}_")
                righe.append(f"🏷 {a['titolo']}")
                righe.append(f"🔗 [Apri annuncio]({a['url']})")
                msg = "\n".join(righe)

                if invia_telegram(msg):
                    inviati += 1
                    aggiornamenti[a["id"]] = dati
                    log.info("  ✓ %s", a["id"])
                    time.sleep(1.3)
                else:
                    log.warning("  ✗ fallito invio %s", a["id"])

        except PlaywrightTimeout:
            log.error("  Timeout su %s", nome)
        except Exception as e:
            log.error("  Errore %s: %s", nome, e)
        finally:
            browser.close()

    return inviati, aggiornamenti


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=== LUPIN %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))
    if DRY_RUN:
        log.info("Modalità DRY_RUN (nessun Telegram reale)")
    if AI_OFF or not ANTHROPIC_API_KEY:
        log.info("Valutazione AI disattivata")
    else:
        log.info("Valutazione AI attiva (modello %s, max %d chiamate, soglia score %d)",
                 MODELLO_AI, MAX_CHIAMATE_AI, SOGLIA_SCORE)

    memoria = carica_memoria()
    log.info("Memoria iniziale: %d annunci", len(memoria))

    totale = 0
    for citta in POSTI:
        inviati, aggiornamenti = missione(citta, memoria)
        memoria.update(aggiornamenti)
        totale += inviati
        time.sleep(random.uniform(4, 8))

    salva_memoria(memoria)
    log.info("=== Fine. Notifiche: %d | Memoria: %d | Chiamate AI: %d ===",
             totale, len(memoria), _chiamate_ai)


if __name__ == "__main__":
    main()
