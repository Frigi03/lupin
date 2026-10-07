#!/usr/bin/env python3
"""
Lupin Report – riepilogo settimanale del mercato → Telegram

Legge la memoria di Lupin Case (annunci_memoria.json) e manda un messaggio con:
- annunci nuovi della settimana nella zona scelta (default Cagliari e provincia)
- prezzo/mq tipico (mediana) per comune, con la variazione sulla settimana prima
- le 3 migliori occasioni della settimana (punteggio AI, poi sconto sulla mediana)
- una riga di confronto con le grandi città

Non scarica nulla: usa solo i dati già raccolti da lupin_headless.py.

Variabili d'ambiente:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
Opzionali:
  DRY_RUN=1          → stampa il messaggio nei log invece di inviarlo
  REPORT_ZONE=...    → zone da riepilogare, separate da virgola
                       (default "Cagliari,Provincia di Cagliari")
  REPORT_GIORNI=7    → ampiezza del periodo in giorni
"""

import html
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true")
ZONE = [z.strip() for z in os.environ.get("REPORT_ZONE", "Cagliari,Provincia di Cagliari").split(",") if z.strip()]
GIORNI = int(os.environ.get("REPORT_GIORNI", "7") or 7)
FILE_MEMORIA = Path("annunci_memoria.json")
GRANDI_CITTA = ["Milano", "Roma", "Torino", "Napoli", "Bologna", "Firenze"]

# Sotto questa superficie il "mq" letto è quasi sempre un errore (box, terrazzo...)
MQ_MIN = 25
# Prezzo/mq plausibile: fuori da questo intervallo il dato è sbagliato
PREZZO_MQ_MIN, PREZZO_MQ_MAX = 300, 20000
# Servono almeno tanti valori per dare una mediana sensata
MIN_CAMPIONE = 5
# Uno sconto oltre questa soglia è quasi sempre un rudere, un terreno o un dato
# sbagliato, non un'occasione: queste case restano fuori dalla classifica
SCONTO_MAX = 50
# Oltre questa superficie di solito il "mq" comprende terreno o giardino
MQ_MAX_OCCASIONE = 400

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("lupin-report")


def carica_memoria() -> dict:
    if not FILE_MEMORIA.exists():
        return {}
    with FILE_MEMORIA.open(encoding="utf-8") as f:
        return json.load(f)


def data(d: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(d["prima_vista"])
    except (KeyError, ValueError):
        return None


def valido(d: dict) -> bool:
    pm, mq = d.get("prezzo_mq"), d.get("mq") or 0
    return bool(pm) and PREZZO_MQ_MIN <= pm <= PREZZO_MQ_MAX and mq >= MQ_MIN


def mediana(valori: list) -> int | None:
    return round(median(valori)) if len(valori) >= MIN_CAMPIONE else None


def euro(n: float | int) -> str:
    return f"€{round(n):,}".replace(",", ".")


def link(aid: str, d: dict) -> str:
    return d.get("url") or f"https://www.wikicasa.it/annuncio/{aid}"


def costruisci_report(memoria: dict, adesso: datetime | None = None) -> str | None:
    adesso = adesso or datetime.now(timezone.utc)
    inizio = adesso - timedelta(days=GIORNI)
    inizio_prec = inizio - timedelta(days=GIORNI)

    nella_zona = {
        aid: d for aid, d in memoria.items()
        if (d.get("zona") or d.get("città")) in ZONE and d.get("città")
    }
    if not nella_zona:
        log.warning("Nessun annuncio in memoria per %s", ", ".join(ZONE))
        return None

    nuovi = {aid: d for aid, d in nella_zona.items() if (data(d) or inizio) >= inizio and data(d)}

    # --- prezzo/mq per comune ---
    comuni: dict[str, list] = {}
    for d in nella_zona.values():
        comuni.setdefault(d["città"], []).append(d)

    righe_comuni = []
    for comune, lista in comuni.items():
        tutti = [d["prezzo_mq"] for d in lista if valido(d)]
        med = mediana(tutti)
        if not med:
            continue
        sett = [d["prezzo_mq"] for d in lista if valido(d) and data(d) and data(d) >= inizio]
        prec = [d["prezzo_mq"] for d in lista if valido(d) and data(d) and inizio_prec <= data(d) < inizio]
        m_sett, m_prec = mediana(sett), mediana(prec)
        trend = ""
        if m_sett and m_prec:
            var = (m_sett - m_prec) / m_prec * 100
            trend = f" ({'+' if var >= 0 else ''}{var:.0f}%)"
        n_nuovi = sum(1 for d in lista if data(d) and data(d) >= inizio)
        righe_comuni.append((med, comune, trend, n_nuovi))
    righe_comuni.sort(reverse=True)

    # --- occasioni della settimana ---
    mediane_comune = {c: mediana([d["prezzo_mq"] for d in l if valido(d)]) for c, l in comuni.items()}
    mediane_zona = {}
    for d in nella_zona.values():
        mediane_zona.setdefault(d.get("zona") or d["città"], []).append(d)
    mediane_zona = {z: mediana([d["prezzo_mq"] for d in l if valido(d)]) for z, l in mediane_zona.items()}

    candidati = []
    for aid, d in nuovi.items():
        if not valido(d):
            continue
        rif = mediane_comune.get(d["città"]) or mediane_zona.get(d.get("zona") or d["città"])
        if not rif:
            continue
        sconto = (rif - d["prezzo_mq"]) / rif * 100
        if sconto <= 0 and not d.get("score"):
            continue
        if sconto > SCONTO_MAX or (d.get("mq") or 0) > MQ_MAX_OCCASIONE:
            continue
        candidati.append((d.get("score") or 0, sconto, aid, d, rif))
    candidati.sort(key=lambda x: (x[0], x[1]), reverse=True)
    occasioni = candidati[:3]

    # --- grandi città ---
    righe_italia = []
    for c in GRANDI_CITTA:
        vals = [d["prezzo_mq"] for d in memoria.values() if d.get("città") == c and valido(d)]
        med = mediana(vals)
        if med:
            righe_italia.append(f"{c} {euro(med)}")

    # --- messaggio (HTML di Telegram) ---
    e = lambda t: html.escape(str(t), quote=False)
    giorno_da = (inizio + timedelta(days=1)).strftime("%d/%m")
    giorno_a = adesso.strftime("%d/%m")
    out = [
        f"🏠 <b>Lupin – Report settimanale</b>",
        f"<i>{e(' e '.join(ZONE))} · {giorno_da}–{giorno_a}</i>",
        "",
        f"📥 <b>{len(nuovi)}</b> annunci nuovi in settimana · <b>{len(nella_zona)}</b> monitorati in totale",
    ]
    prima_data = min((data(d) for d in nella_zona.values() if data(d)), default=None)
    if prima_data and prima_data >= inizio:
        out.append("<i>Prima settimana di monitoraggio: gli annunci già online sono contati come nuovi.</i>")

    if righe_comuni:
        out += ["", "📊 <b>Prezzo tipico al mq per comune</b>"]
        for med, comune, trend, n in righe_comuni:
            extra = f" · {n} nuovi" if n else ""
            out.append(f"• {e(comune)}: {euro(med)}{e(trend)}{extra}")

    out += ["", "💎 <b>Occasioni della settimana</b>"]
    if occasioni:
        for i, (score, sconto, aid, d, rif) in enumerate(occasioni, 1):
            titolo = e((d.get("titolo") or f"Annuncio {aid}")[:80])
            dettagli = [euro(d["prezzo"]) if d.get("prezzo") else None,
                        f"{d['mq']} mq" if d.get("mq") else None,
                        f"{euro(d['prezzo_mq'])}/mq"]
            riga2 = " · ".join(x for x in dettagli if x)
            sotto = f"{sconto:.0f}% sotto la mediana di {e(d['città'])} ({euro(rif)}/mq)" if sconto > 0 \
                else f"in linea con la mediana di {e(d['città'])}"
            out.append(f"{i}. <a href=\"{html.escape(link(aid, d))}\">{titolo}</a>")
            out.append(f"   {riga2}")
            out.append(f"   {sotto}" + (f" · AI {score}/10" if score else ""))
            if d.get("motivo_ai"):
                out.append(f"   <i>{e(d['motivo_ai'])}</i>")
    else:
        out.append("Nessun annuncio sotto la media questa settimana.")

    if righe_italia:
        out += ["", "🇮🇹 <b>Confronto grandi città</b> (prezzo tipico al mq)", e(" · ".join(righe_italia))]

    return "\n".join(out)


def invia_telegram(testo: str) -> bool:
    if DRY_RUN or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("[DRY] Messaggio che verrebbe inviato:\n%s", testo)
        return True
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": testo, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=15,
    )
    if r.status_code != 200:
        log.error("Telegram HTTP %s → %s", r.status_code, r.text[:300])
        return False
    return True


def main():
    memoria = carica_memoria()
    log.info("Memoria: %d annunci · zone: %s · periodo: %d giorni", len(memoria), ", ".join(ZONE), GIORNI)
    testo = costruisci_report(memoria)
    if not testo:
        log.warning("Report non generato: niente da riepilogare")
        return
    if not invia_telegram(testo):
        raise SystemExit(1)
    log.info("Report inviato")


if __name__ == "__main__":
    main()
