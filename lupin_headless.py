#!/usr/bin/env python3
"""
Lupin – Scraper annunci Wikicasa → Telegram
Versione migliorata (2026-08)

Migliorie:
- Selettore corretto: article[data-cy='real-estate-insertion'] + /annuncio/ID
- Deduplicazione per ID numerico
- Estrazione prezzo e titolo affidabile
- Memoria basata solo su ID
- Logging chiaro
- Modalità DRY_RUN (env DRY_RUN=1)
- Playwright (più stabile su GitHub Actions)
- Pause casuali anti-bot
"""

import os
import re
import time
import random
import logging
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FILE_MEMORIA = Path("annunci_inviati.txt")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
MIN_PREZZO = 40_000
MAX_PREZZO = 2_500_000
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


def carica_memoria() -> set[str]:
    if not FILE_MEMORIA.exists():
        return set()
    ids: set[str] = set()
    with FILE_MEMORIA.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.search(r"/annuncio/(\d+)", line) or re.fullmatch(r"(\d+)", line)
            if m:
                ids.add(m.group(1))
    return ids


def salva_memoria(ids: set[str]) -> None:
    with FILE_MEMORIA.open("w", encoding="utf-8") as f:
        for i in sorted(ids, key=lambda x: int(x)):
            f.write(i + "\n")


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
                "titolo": titolo[:100],
            })
        except Exception:
            continue

    return risultati


def missione(citta: dict, memoria: set[str]) -> tuple[int, set[str]]:
    nome = citta["nome"]
    url = citta["url"]
    nuovi: set[str] = set()
    inviati = 0

    log.info("▶ %s", nome)

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
                    continue

                if a["prezzo"] is None:
                    nuovi.add(a["id"])
                    continue

                prezzo_fmt = f"€{a['prezzo']:,}".replace(",", ".")
                msg = (
                    f"🚨 *LUPIN – Nuovo a {nome.upper()}*\n"
                    f"💰 *{prezzo_fmt}*\n"
                    f"🏷 {a['titolo']}\n"
                    f"🔗 [Apri annuncio]({a['url']})"
                )

                if invia_telegram(msg):
                    inviati += 1
                    nuovi.add(a["id"])
                    log.info("  ✓ %s  %s", a["id"], prezzo_fmt)
                    time.sleep(1.3)
                else:
                    log.warning("  ✗ fallito invio %s", a["id"])

        except PlaywrightTimeout:
            log.error("  Timeout su %s", nome)
        except Exception as e:
            log.error("  Errore %s: %s", nome, e)
        finally:
            browser.close()

    return inviati, nuovi


def main():
    log.info("=== LUPIN %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))
    if DRY_RUN:
        log.info("Modalità DRY_RUN (nessun Telegram reale)")

    memoria = carica_memoria()
    log.info("Memoria iniziale: %d ID", len(memoria))

    totale = 0
    for citta in POSTI:
        inviati, nuovi = missione(citta, memoria)
        memoria.update(nuovi)
        totale += inviati
        time.sleep(random.uniform(4, 8))

    salva_memoria(memoria)
    log.info("=== Fine. Notifiche inviate: %d | Memoria: %d ===", totale, len(memoria))


if __name__ == "__main__":
    main()