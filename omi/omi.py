#!/usr/bin/env python3
"""
Quotazioni OMI (Agenzia delle Entrate) → prezzo al mq di riferimento per Lupin Aste.

Fonte: file CSV scaricati dall'area riservata dell'Agenzia delle Entrate
(QI_<semestre>_<prov>_VALORI.csv e _ZONE.csv), messi in omi/dati/.

Uso:
  python omi/omi.py            → ricostruisce omi/omi_sardegna.json dai CSV
  from omi.omi import riferimento
  riferimento("Quartu Sant'Elena")                 → valori di tutto il comune
  riferimento("Cagliari", zona="B1")               → valori di una zona OMI

I valori sono €/mq di superficie lorda, stato conservativo "normale" se presente.
"""

import csv
import json
import re
import sys
import unicodedata
from pathlib import Path
from statistics import median

CARTELLA = Path(__file__).resolve().parent
DATI = CARTELLA / "dati"
FILE_JSON = CARTELLA / "omi_sardegna.json"

# Tipologie residenziali usate per valutare le aste di case
TIPOLOGIE_CASA = ("Abitazioni civili", "Abitazioni di tipo economico", "Ville e Villini")


def normalizza(nome: str) -> str:
    """'QUARTU SANT`ELENA' e "Quartu Sant'Elena" diventano la stessa chiave."""
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    s = s.replace("`", "'").upper()
    s = re.sub(r"[^A-Z0-9' ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _numero(v: str) -> float | None:
    v = (v or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _leggi_csv(percorso: Path) -> list[dict]:
    # Prima riga: titolo del file; seconda: intestazioni separate da ';'
    righe = percorso.read_text(encoding="latin-1").splitlines()
    return list(csv.DictReader(righe[1:], delimiter=";"))


def costruisci() -> dict:
    comuni: dict = {}
    semestri = set()
    for f_zone in sorted(DATI.glob("*_ZONE.csv")):
        for r in _leggi_csv(f_zone):
            chiave = normalizza(r["Comune_descrizione"])
            c = comuni.setdefault(chiave, {
                "comune": r["Comune_descrizione"].replace("`", "'"),
                "prov": r["Prov"], "istat": r["Comune_ISTAT"], "zone": {},
            })
            c["zone"][r["Zona"]] = {
                "descrizione": r["Zona_Descr"].strip("'"),
                "fascia": r["Fascia"],
                "link": r["LinkZona"],
                "tipologie": {},
            }
        m = re.search(r"QI_(\d{5})", f_zone.name)
        if m:
            semestri.add(m.group(1))

    for f_val in sorted(DATI.glob("*_VALORI.csv")):
        for r in _leggi_csv(f_val):
            chiave = normalizza(r["Comune_descrizione"])
            c = comuni.get(chiave)
            if not c:
                continue
            zona = c["zone"].setdefault(r["Zona"], {"descrizione": "", "fascia": r["Fascia"],
                                                    "link": r["LinkZona"], "tipologie": {}})
            vmin, vmax = _numero(r["Compr_min"]), _numero(r["Compr_max"])
            if not vmin or not vmax:
                continue
            tip = r["Descr_Tipologia"]
            stato = r["Stato"].upper()
            esistente = zona["tipologie"].get(tip)
            # Se ci sono più stati (NORMALE/OTTIMO/SCADENTE) si tiene NORMALE
            if esistente and esistente["stato"] == "NORMALE" and stato != "NORMALE":
                continue
            zona["tipologie"][tip] = {"min": vmin, "max": vmax, "stato": stato}

    # Riepilogo per comune: mediana dei valori medi delle tipologie residenziali
    for c in comuni.values():
        medi = [
            (t["min"] + t["max"]) / 2
            for z in c["zone"].values()
            for nome, t in z["tipologie"].items() if nome in TIPOLOGIE_CASA
        ]
        c["casa_mq_mediana"] = round(median(medi)) if medi else None

    return {"fonte": "Agenzia delle Entrate - OMI", "semestri": sorted(semestri), "comuni": comuni}


_cache: dict | None = None


def _carica() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(FILE_JSON.read_text(encoding="utf-8")) if FILE_JSON.exists() else costruisci()
    return _cache


def riferimento(comune: str, zona: str | None = None,
                tipologia: str | None = None) -> dict | None:
    """Valori OMI per un comune (o una sua zona). None se il comune non è nei dati.

    Ritorna {"comune", "zona", "min", "max", "medio", "tipologie"} in €/mq."""
    c = _carica()["comuni"].get(normalizza(comune))
    if not c:
        return None
    zone = [c["zone"][zona]] if zona and zona in c["zone"] else list(c["zone"].values())
    tipi = (tipologia,) if tipologia else TIPOLOGIE_CASA
    valori = [t for z in zone for n, t in z["tipologie"].items() if n in tipi]
    if not valori:
        return None
    vmin = min(t["min"] for t in valori)
    vmax = max(t["max"] for t in valori)
    return {
        "comune": c["comune"],
        "zona": zona if zona and zona in c["zone"] else None,
        "min": vmin,
        "max": vmax,
        "medio": round(median((t["min"] + t["max"]) / 2 for t in valori)),
        "tipologie": sorted({n for z in zone for n in z["tipologie"] if n in tipi}),
    }


def main():
    dati = costruisci()
    FILE_JSON.write_text(json.dumps(dati, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    comuni = dati["comuni"]
    prov = sorted({c["prov"] for c in comuni.values()})
    print(f"Semestri {dati['semestri']} · province {prov} · {len(comuni)} comuni · "
          f"{sum(len(c['zone']) for c in comuni.values())} zone → {FILE_JSON.name}")
    for nome in sys.argv[1:]:
        print(nome, riferimento(nome))


if __name__ == "__main__":
    main()
