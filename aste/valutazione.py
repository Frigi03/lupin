#!/usr/bin/env python3
"""
Lupin Aste — valutazione di un'asta immobiliare rispetto ai prezzi di zona.

Funziona con qualunque fonte delle aste (avvisi email del PVP, inserimento a mano…):
basta un dizionario con comune, metri quadri e prezzo base.

Confronta il prezzo dell'asta con due riferimenti:
  1. quotazioni OMI dell'Agenzia delle Entrate (omi/omi_sardegna.json) — riferimento principale;
  2. prezzi richiesti negli annunci di vendita raccolti da Lupin Case (annunci_memoria.json),
     solo come controllo: sono prezzi chiesti, di solito più alti di quelli reali.

Uso:
  from aste.valutazione import valuta, messaggio
  v = valuta({"comune": "Quartu Sant'Elena", "mq": 80, "prezzo_base": 65000})
  print(messaggio(v))

  python aste/valutazione.py   → prova con alcune aste di esempio (inventate)

Nessun dato personale: niente nomi di debitori, solo dati dell'immobile.
"""

import html
import json
import sys
from pathlib import Path
from statistics import median

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE))

from omi.omi import normalizza, riferimento  # noqa: E402

FILE_ANNUNCI = RADICE / "annunci_memoria.json"

# Nelle vendite giudiziarie si può offrire fino al 25% in meno del prezzo base (art. 571 c.p.c.)
QUOTA_OFFERTA_MINIMA = 0.75

# Senza zona OMI si usano solo centro, semicentro e periferia (no zone suburbane e rurali)
FASCE_URBANE = ("B", "C", "D")

# Annunci considerati per il prezzo richiesto di un comune
MQ_MIN, PREZZO_MQ_MIN, PREZZO_MQ_MAX, ANNUNCI_MIN = 30, 200, 6000, 5

# Correzione prudente se l'immobile è occupato (tempi e costi per liberarlo)
SCONTO_OCCUPATO = 0.15

GIUDIZI = [  # (sconto minimo sul valore OMI, punteggio, etichetta)
    (0.45, 9, "affare forte"),
    (0.35, 8, "molto interessante"),
    (0.25, 7, "interessante"),
    (0.15, 5, "da valutare"),
    (0.05, 3, "poco conveniente"),
    (-1.0, 1, "non conveniente"),
]

_annunci_cache: dict | None = None


def _prezzi_annunci() -> dict:
    """Mediana del prezzo richiesto al mq per comune, dagli annunci di Lupin Case."""
    global _annunci_cache
    if _annunci_cache is not None:
        return _annunci_cache
    per_comune: dict[str, list[float]] = {}
    if FILE_ANNUNCI.exists():
        for a in json.loads(FILE_ANNUNCI.read_text(encoding="utf-8")).values():
            mq, pmq = a.get("mq") or 0, a.get("prezzo_mq") or 0
            if mq >= MQ_MIN and PREZZO_MQ_MIN <= pmq <= PREZZO_MQ_MAX and a.get("città"):
                per_comune.setdefault(normalizza(a["città"]), []).append(pmq)
    _annunci_cache = {
        c: {"mediana": round(median(v)), "annunci": len(v)}
        for c, v in per_comune.items() if len(v) >= ANNUNCI_MIN
    }
    return _annunci_cache


def _giudizio(sconto: float) -> tuple[int, str]:
    for soglia, punti, etichetta in GIUDIZI:
        if sconto >= soglia:
            return punti, etichetta
    return 1, "non conveniente"


def valuta(asta: dict) -> dict | None:
    """Valuta un'asta. Campi di `asta`:
      comune (obbligatorio), mq (obbligatorio), prezzo_base (obbligatorio),
      zona OMI (es. "B1"), offerta_minima, tipologia OMI, occupato (bool),
      data_asta, link, descrizione.
    Ritorna None se mancano dati o il comune non è nei dati OMI."""
    try:
        mq = float(asta["mq"])
        base = float(asta["prezzo_base"])
    except (KeyError, TypeError, ValueError):
        return None
    if mq <= 0 or base <= 0:
        return None

    omi = riferimento(asta["comune"], zona=asta.get("zona"), tipologia=asta.get("tipologia"),
                      fasce=FASCE_URBANE)
    if not omi:
        return None

    offerta_min = float(asta.get("offerta_minima") or base * QUOTA_OFFERTA_MINIMA)
    valore = omi["medio"] * mq
    valore_min, valore_max = omi["min"] * mq, omi["max"] * mq

    # Se occupato, il valore utile per chi compra è più basso
    valore_utile = valore * (1 - SCONTO_OCCUPATO) if asta.get("occupato") else valore

    sconto_base = 1 - base / valore_utile
    sconto_offerta = 1 - offerta_min / valore_utile
    punti, etichetta = _giudizio(sconto_base)

    mercato = _prezzi_annunci().get(normalizza(asta["comune"]))
    controllo = None
    if mercato:
        controllo = {
            "prezzo_mq_richiesto": mercato["mediana"],
            "annunci": mercato["annunci"],
            "valore_richiesto": round(mercato["mediana"] * mq),
        }

    avvisi = []
    if asta.get("occupato"):
        avvisi.append(f"occupato: valore ridotto del {SCONTO_OCCUPATO:.0%} per tempi e costi di liberazione")
    if not asta.get("zona"):
        avvisi.append("zona OMI non indicata: usata la media delle zone urbane del comune")
    if base / mq < omi["min"] * 0.4:
        avvisi.append("prezzo al mq molto basso: controllare la perizia (stato, abusi, quota di proprietà)")

    return {
        "comune": omi["comune"].title(),
        "zona": omi["zona"],
        "mq": mq,
        "prezzo_base": round(base),
        "offerta_minima": round(offerta_min),
        "prezzo_mq_base": round(base / mq),
        "omi_mq": {"min": omi["min"], "medio": omi["medio"], "max": omi["max"]},
        "valore_omi": {"min": round(valore_min), "medio": round(valore), "max": round(valore_max)},
        "sconto_base": round(sconto_base, 3),
        "sconto_offerta_minima": round(sconto_offerta, 3),
        "punteggio": punti,
        "giudizio": etichetta,
        "controllo_annunci": controllo,
        "avvisi": avvisi,
        "data_asta": asta.get("data_asta"),
        "link": asta.get("link"),
        "descrizione": asta.get("descrizione"),
        "fonte": "Agenzia Entrate - OMI",
    }


def _euro(n: float) -> str:
    return f"{n:,.0f} €".replace(",", ".")


def messaggio(v: dict) -> str:
    """Testo pronto per Telegram (parse_mode HTML, come Lupin gare)."""
    def e(x, quote=False):
        return html.escape(x, quote=quote)
    titolo = e(v.get("descrizione") or "Immobile")
    zona = f" (zona {e(v['zona'])})" if v["zona"] else ""
    righe = [
        f"🏠 <b>{titolo} – {e(v['comune'])}</b>{zona}",
        f"{v['mq']:.0f} mq · prezzo base {_euro(v['prezzo_base'])} ({_euro(v['prezzo_mq_base'])}/mq)",
        f"Offerta minima: {_euro(v['offerta_minima'])}",
        f"Valore di zona: {_euro(v['valore_omi']['medio'])} "
        f"({_euro(v['omi_mq']['min'])}–{_euro(v['omi_mq']['max'])}/mq, fonte {e(v['fonte'])})",
        f"Sconto sul valore di zona: <b>{v['sconto_base']:.0%}</b> "
        f"(con l'offerta minima {v['sconto_offerta_minima']:.0%})",
        f"Giudizio: <b>{e(v['giudizio'])}</b> · {v['punteggio']}/10",
    ]
    if v["controllo_annunci"]:
        c = v["controllo_annunci"]
        righe.append(f"Annunci in vendita nel comune: {_euro(c['prezzo_mq_richiesto'])}/mq richiesti "
                     f"({c['annunci']} annunci)")
    for a in v["avvisi"]:
        righe.append(f"⚠️ {e(a)}")
    if v.get("data_asta"):
        righe.append(f"📅 Asta: {e(str(v['data_asta']))}")
    if v.get("link"):
        righe.append(f'🔗 <a href="{e(v["link"], quote=True)}">Apri l\'avviso di vendita</a>')
    return "\n".join(righe)


ESEMPI = [  # aste inventate, solo per provare il calcolo
    {"descrizione": "Appartamento 3 vani", "comune": "Quartu Sant'Elena", "mq": 80,
     "prezzo_base": 65000, "data_asta": "12/11/2026"},
    {"descrizione": "Casa indipendente", "comune": "Sinnai", "mq": 140,
     "prezzo_base": 150000, "occupato": True},
    {"descrizione": "Bilocale", "comune": "Cagliari", "mq": 55, "prezzo_base": 120000},
    {"descrizione": "Villetta", "comune": "Comune Inesistente", "mq": 100, "prezzo_base": 90000},
]


def main():
    for asta in ESEMPI:
        v = valuta(asta)
        print(messaggio(v) if v else f"✗ {asta['comune']}: comune non trovato nei dati OMI o dati mancanti")
        print("-" * 40)


if __name__ == "__main__":
    main()
