#!/usr/bin/env python3
"""
Lupin Aste – aste immobiliari della Sardegna valutate e inviate su Telegram.

Stesso schema di Lupin gare: un profilo per cliente, memoria delle aste già
inviate, invio solo di quelle che passano i filtri del cliente.

Fonte delle aste: il file aste/coda.json (lista di aste). Per ora si riempie a
mano o da script; poi lo riempirà la lettura delle email di avviso del Portale
delle Vendite Pubbliche. Campi di ogni asta (vedi aste/valutazione.py):
  id (facoltativo), descrizione, comune, zona, mq, prezzo_base, offerta_minima,
  tipologia, occupato, data_asta, link

Variabili d'ambiente (GitHub Secrets):
  TELEGRAM_TOKEN_ASTE (o TELEGRAM_TOKEN) → bot che invia
  TELEGRAM_CHAT_ID_ASTE (o TELEGRAM_CHAT_ID) → chat del profilo principale (Frigi)
  PROFILI_ASTE → clienti, in JSON: lista di oggetti con
      id          breve e anonimo (finisce nella memoria pubblica), es. "a1"
      chat_id     chat Telegram del cliente
      attivo      true/false
      fine_prova  AAAA-MM-GG (facoltativo)
      comuni      lista di comuni (vuota = tutti)
      province    lista di sigle, es. ["CA", "SS"] (vuota = tutte)
      budget_max  euro sul prezzo base (0 = nessun limite)
      mq_min      superficie minima (0 = nessun limite)
      punteggio_min  da 1 a 10 (default 7)
      occupato_ok  true/false (default true)
Opzionali:
  DRY_RUN=1  → nessun invio reale e memoria non salvata
  CODA=aste/coda.json → file con le aste da valutare

Nessun dato personale: solo dati dell'immobile.
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from aste.valutazione import messaggio, valuta
from omi.omi import _carica as _omi, normalizza

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN_ASTE") or os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID_ASTE") or os.environ.get("TELEGRAM_CHAT_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1" or not TELEGRAM_TOKEN
FILE_CODA = Path(os.environ.get("CODA", "aste/coda.json"))
FILE_MEMORIA = Path("aste_memoria.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("lupin-aste")


def id_asta(a: dict) -> str:
    if a.get("id"):
        return str(a["id"])
    base = "|".join(str(a.get(k, "")) for k in ("comune", "mq", "prezzo_base", "data_asta", "link"))
    return hashlib.sha1(base.encode()).hexdigest()[:12]


def carica_profili() -> list[dict]:
    principale = {"_id": "", "_chat": TELEGRAM_CHAT_ID, "punteggio_min": 5, "occupato_ok": True}
    profili = [principale]
    grezzo = os.environ.get("PROFILI_ASTE", "").strip()
    if not grezzo:
        return profili
    try:
        lista = json.loads(grezzo)
    except json.JSONDecodeError as e:
        log.error("PROFILI_ASTE non è JSON valido (%s): uso solo il profilo principale", e)
        return profili
    oggi = datetime.now(timezone.utc).date().isoformat()
    for i, p in enumerate(lista if isinstance(lista, list) else [lista]):
        pid = str(p.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", pid) or not p.get("chat_id"):
            log.warning("PROFILI_ASTE[%d]: id o chat_id mancanti, ignorato", i)
            continue
        if p.get("attivo") is False or (p.get("fine_prova") and str(p["fine_prova"]) < oggi):
            log.info("Profilo %s: non attivo o prova finita, saltato", pid)
            continue
        profili.append({**p, "_id": pid, "_chat": str(p["chat_id"])})
    return profili


def provincia(comune: str) -> str | None:
    c = _omi()["comuni"].get(normalizza(comune))
    return c["prov"] if c else None


def adatta(v: dict, asta: dict, p: dict) -> bool:
    if v["punteggio"] < int(p.get("punteggio_min", 7)):
        return False
    comuni = {normalizza(c) for c in p.get("comuni") or []}
    if comuni and normalizza(asta["comune"]) not in comuni:
        return False
    province = {x.upper() for x in p.get("province") or []}
    if province and provincia(asta["comune"]) not in province:
        return False
    if p.get("budget_max") and v["prezzo_base"] > float(p["budget_max"]):
        return False
    if p.get("mq_min") and v["mq"] < float(p["mq_min"]):
        return False
    if asta.get("occupato") and p.get("occupato_ok") is False:
        return False
    return True


def invia_telegram(testo: str, chat_id: str) -> bool:
    if DRY_RUN or not chat_id:
        log.info("[DRY] → %s | %s", chat_id or "(nessuna chat)", testo.replace("\n", " | ")[:300])
        return True
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": testo, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        if r.status_code != 200:
            log.warning("Telegram HTTP %s → %s", r.status_code, r.text[:180])
        return r.status_code == 200
    except requests.RequestException as e:
        log.warning("Telegram errore: %s", e)
        return False


def main():
    log.info("=== LUPIN ASTE %s%s ===", datetime.now().strftime("%Y-%m-%d %H:%M"), " (prova)" if DRY_RUN else "")
    aste = json.loads(FILE_CODA.read_text(encoding="utf-8")) if FILE_CODA.exists() else []
    memoria = json.loads(FILE_MEMORIA.read_text(encoding="utf-8")) if FILE_MEMORIA.exists() else {}
    profili = carica_profili()
    inviate = 0
    for asta in aste:
        v = valuta(asta)
        if not v:
            log.info("Asta saltata (dati mancanti o comune non nei dati OMI): %s", asta.get("comune"))
            continue
        aid = id_asta(asta)
        for p in profili:
            chiave = f"{p['_id']}:{aid}" if p["_id"] else aid
            if chiave in memoria or not adatta(v, asta, p):
                continue
            if invia_telegram(messaggio(v), p["_chat"]):
                inviate += 1
                memoria[chiave] = {"inviata": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "punteggio": v["punteggio"]}
    log.info("Aste in coda: %d · messaggi inviati: %d · profili: %d", len(aste), inviate, len(profili))
    if not DRY_RUN:
        FILE_MEMORIA.write_text(json.dumps(memoria, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
