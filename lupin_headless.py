#!/usr/bin/env python3
"""
Lupin – Scraper annunci Wikicasa → Telegram
Versione con arricchimento dati (2026-08)

Novità rispetto alla versione base:
- Estrazione superficie (mq) dal testo dell'annuncio
- Calcolo prezzo/mq
- Giorni online: tracciati dalla prima volta che l'annuncio viene visto da Lupin
  (proxy affidabile, perché Wikicasa non espone sempre la data di pubblicazione reale)
- Confronto con la media prezzo/mq di zona (calcolata sugli annunci già in memoria
  per la stessa città) → flag "sotto media" / "sopra media"
- Memoria passata da lista di ID (.txt) a JSON strutturato, per conservare
  prezzo, mq, prezzo/mq, città e data di prima vista di ogni annuncio

Il resto (Playwright, GitHub Actions, Telegram, DRY_RUN) resta invariato.
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
FILE_MEMORIA = Path("annunci_memoria.json")  # sostituisce annunci_inviati.txt
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
MIN_PREZZO = 40_000
MAX_PREZZO = 2_500_000
SOGLIA_SOTTO_MEDIA = 0.10  # 10% sotto la media di zona → segnalato come "occasione"
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


# ---------------------------------------------------------------------------
# Memoria (ora JSON: id -> dati arricchiti)
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
# Estrazione e arricchimento
# ---------------------------------------------------------------------------

def estrai_mq(text: str) -> int | None:
    """Cerca pattern tipo '80 mq', '80 m²', '80mq' nel testo dell'annuncio."""
    m = re.search(r"(\d{2,4})\s*m(?:q|²|2)\b", text, re.IGNORECASE)
    if m:
        try:
            val = int(m.group(1))
            if 10 <= val <= 2000:  # range plausibile per un'abitazione
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
    return round(mean(valori)) if len(valori) >= 5 else None  # servono almeno 5 dati per una media sensata


def giorni_online(prima_vista_iso: str) -> int:
    prima_vista = datetime.fromisoformat(prima_vista_iso)
    return (datetime.now(timezone.utc) - prima_vista).days


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
            page.goto(url, wait_until="networkidle", timeout=90_000)
            page.wait_for_timeout(random.randint(2500, 4500))

            annunci = estrai_annunci(page)
            log.info("  %d annunci trovati", len(annunci))

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

                # Etichetta "sotto media" solo se abbiamo sia il dato che una media di zona
                sotto_media = False
                if a["prezzo_mq"] and media_zona:
                    sotto_media = a["prezzo_mq"] <= media_zona * (1 - SOGLIA_SOTTO_MEDIA)
                dati["sotto_media"] = sotto_media

                righe = [f"🚨 *LUPIN – Nuovo a {nome.upper()}*"]
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

    memoria = carica_memoria()
    log.info("Memoria iniziale: %d annunci", len(memoria))

    totale = 0
    for citta in POSTI:
        inviati, aggiornamenti = missione(citta, memoria)
        memoria.update(aggiornamenti)
        totale += inviati
        time.sleep(random.uniform(4, 8))

    salva_memoria(memoria)
    log.info("=== Fine. Notifiche inviate: %d | Memoria: %d ===", totale, len(memoria))


if __name__ == "__main__":
    main()
    
