#!/usr/bin/env python3
"""
Lupin Case – raccolta degli annunci di vendita case in tutta la Sardegna (Wikicasa)

Fa una cosa sola: scorre le pagine provinciali di Wikicasa per tutte le province
sarde e salva in annunci_memoria.json gli annunci nuovi (prezzo, mq, prezzo/mq,
comune, provincia, titolo, link, data di prima vista).

Niente Telegram e niente AI: i dati servono come base dei prezzi di zona
(per Lupin Aste e per eventuali analisi). Gli annunci già in memoria, comprese
le città fuori Sardegna raccolte in passato, non vengono toccati.

Variabili d'ambiente opzionali:
  DRY_RUN=1          → non salva la memoria (solo log)
  PROVINCE=ca,ss     → solo queste province (default: tutte)
  MAX_PAGINE=1       → pagine per provincia (Wikicasa blocca con Cloudflare le pagine oltre la prima)
"""

import os
import re
import json
import time
import random
import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

FILE_MEMORIA = Path("annunci_memoria.json")
DRY_RUN = os.environ.get("DRY_RUN", "0").strip().lower() in ("1", "true", "yes")
MAX_PAGINE = int(os.environ.get("MAX_PAGINE", "1"))

MIN_PREZZO = 10_000
MAX_PREZZO = 5_000_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
BASE = "https://www.wikicasa.it"

# Le province come le divide Wikicasa (sigle storiche): coprono tutta l'isola.
PROVINCE = {
    "ca": "Provincia di Cagliari",
    "ss": "Provincia di Sassari",
    "ot": "Gallura Nord-Est Sardegna",
    "nu": "Provincia di Nuoro",
    "og": "Ogliastra",
    "or": "Provincia di Oristano",
    "ci": "Sulcis Iglesiente",
    "vs": "Medio Campidano",
}
_scelte = [s.strip().lower() for s in os.environ.get("PROVINCE", "").split(",") if s.strip()]
if _scelte:
    PROVINCE = {k: v for k, v in PROVINCE.items() if k in _scelte}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lupin-case")


# ---------------------------------------------------------------------------
# Memoria (JSON: id -> dati)
# ---------------------------------------------------------------------------

def carica_memoria() -> dict:
    if FILE_MEMORIA.exists():
        try:
            with FILE_MEMORIA.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            # Meglio fermarsi che sovrascrivere i dati raccolti con una memoria vuota
            raise SystemExit("annunci_memoria.json non leggibile: interrompo senza toccarlo")
    return {}


def salva_memoria(memoria: dict) -> None:
    tmp = FILE_MEMORIA.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(FILE_MEMORIA)


# ---------------------------------------------------------------------------
# Estrazione
# ---------------------------------------------------------------------------

# Superficie minima plausibile per tipo di immobile (dal titolo dell'annuncio).
# Serve a scartare i "mq" che non sono la casa: giardino, terrazzo, box...
MQ_MINIMI = [
    ("monolocale", 15), ("bilocale", 30), ("trilocale", 45), ("quadrilocale", 60),
    ("5 locali", 70), ("plurilocale", 70), ("villa", 60), ("casale", 60),
    ("rustico", 40), ("attico", 35), ("loft", 25),
]
MQ_MINIMO_DEFAULT = 20
PAROLE_NON_CASA = (
    "giardino", "terrazz", "balcon", "box", "garage", "cantina", "posto auto",
    "soffitta", "lastrico", "cortile", "terreno", "veranda", "portico", "taverna",
    "solaio", "magazzino", "lotto",
)


def mq_minimo(titolo: str) -> int:
    t = (titolo or "").lower()
    for parola, minimo in MQ_MINIMI:
        if parola in t:
            return minimo
    return MQ_MINIMO_DEFAULT


def estrai_mq(text: str, titolo: str = "") -> int | None:
    """Superficie della casa dal testo dell'annuncio ('80 mq', '80 m²', '80mq').

    Scarta i valori preceduti da parole come giardino, terrazzo o box e quelli
    troppo piccoli per il tipo di immobile, e tiene il primo rimasto."""
    minimo = mq_minimo(titolo)
    fine_precedente = 0
    for m in re.finditer(r"(\d{2,4})\s*m(?:q|²|2)\b", text, re.IGNORECASE):
        inizio = max(m.start() - 25, fine_precedente, text.rfind("\n", 0, m.start()) + 1)
        prima = text[inizio:m.start()].lower()
        fine_precedente = m.end()
        if any(p in prima for p in PAROLE_NON_CASA):
            continue
        val = int(m.group(1))
        if minimo <= val <= 2000:
            return val
    return None


def comune_da_titolo(titolo: str) -> str | None:
    """Il comune è l'ultima parte del titolo: 'Bilocale in Vendita, Via Roma 1, Sestu'."""
    parti = [p.strip() for p in (titolo or "").split(",") if p.strip()]
    return parti[-1] if len(parti) >= 2 else None


SELETTORE_ANNUNCI = "article[data-cy='real-estate-insertion']"


def estrai_annunci(page) -> list[dict]:
    risultati = []
    for art in page.locator(SELETTORE_ANNUNCI).all():
        try:
            aid = art.get_attribute("data-id") or art.get_attribute("id")
            if not aid:
                continue
            relative = art.get_attribute("data-url") or f"/annuncio/{aid}"
            full_url = f"{BASE}{relative}" if relative.startswith("/") else relative

            text = art.inner_text() or ""

            prezzo = None
            m = re.search(r"€\s*([\d]{1,3}(?:\.\d{3})+)", text)
            if m:
                val = int(m.group(1).replace(".", ""))
                if MIN_PREZZO <= val <= MAX_PREZZO:
                    prezzo = val

            titolo = ""
            for lk in art.locator("a[href*='/annuncio/']").all():
                t = (lk.inner_text() or "").strip()
                if len(t) > 15:
                    titolo = t
                    break
            if not titolo:
                lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
                titolo = lines[1] if len(lines) > 1 else (lines[0] if lines else f"Annuncio #{aid}")

            mq = estrai_mq(text, titolo)
            risultati.append({
                "id": aid,
                "url": full_url,
                "prezzo": prezzo,
                "mq": mq,
                "prezzo_mq": round(prezzo / mq) if (prezzo and mq) else None,
                "titolo": titolo[:100],
            })
        except Exception:
            continue
    return risultati


# ---------------------------------------------------------------------------
# Apertura pagina (robusta)
# ---------------------------------------------------------------------------

SELETTORI_CONSENSO = [
    "#onetrust-accept-btn-handler",
    "button#didomi-notice-agree-button",
    "button[aria-label*='Accetta']",
    "button:has-text('Accetta tutto')",
    "button:has-text('Accetta tutti')",
    "button:has-text('Accetta')",
    "button:has-text('Ho capito')",
]


def chiudi_consenso(page) -> None:
    for sel in SELETTORI_CONSENSO:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=3000)
                page.wait_for_timeout(1200)
                return
        except Exception:
            continue


def attendi_annunci(page, timeout_ms: int) -> bool:
    try:
        page.wait_for_selector(SELETTORE_ANNUNCI, timeout=timeout_ms)
        return True
    except PlaywrightTimeout:
        return False


def apri_lista(page, url: str) -> bool:
    """networkidle su Wikicasa non arriva mai: domcontentloaded + attesa del selettore.
    Banner cookie e scroll solo se gli annunci non compaiono."""
    for tentativo in (1, 2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeout:
            log.warning("  Tentativo %d: timeout su %s", tentativo, url)
            continue
        if attendi_annunci(page, 15_000):
            return True
        chiudi_consenso(page)
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            pass
        if attendi_annunci(page, 20_000):
            return True
        if tentativo == 1:
            page.wait_for_timeout(random.randint(3000, 6000))
    log.warning("  Nessun annuncio su %s", url)
    try:
        corpo = " ".join((page.locator("body").inner_text(timeout=5000) or "").split())[:400]
        log.warning("  [DIAG] titolo=%r url=%s body=%s", page.title(), page.url, corpo)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Raccolta per provincia
# ---------------------------------------------------------------------------

def raccogli_provincia(page, sigla: str, nome: str, memoria: dict) -> int:
    nuovi = 0
    visti_ora: set[str] = set()
    for p in range(1, MAX_PAGINE + 1):
        url = f"{BASE}/vendita-case/provincia-{sigla}/" + (f"?p={p}" if p > 1 else "")
        if not apri_lista(page, url):
            break
        annunci = estrai_annunci(page)
        ids = {a["id"] for a in annunci}
        # Oltre l'ultima pagina il sito ripropone pagine già viste: ci si ferma
        if not ids or ids <= visti_ora:
            break
        visti_ora |= ids

        ora = datetime.now(timezone.utc).isoformat()
        for a in annunci:
            if a["id"] in memoria:
                continue
            memoria[a["id"]] = {
                "città": comune_da_titolo(a["titolo"]),
                "zona": nome,
                "provincia": sigla.upper(),
                "regione": "Sardegna",
                "prima_vista": ora,
                "prezzo": a["prezzo"],
                "mq": a["mq"],
                "prezzo_mq": a["prezzo_mq"],
                "titolo": a["titolo"],
                "url": a["url"],
            }
            nuovi += 1
        log.info("  pagina %d: %d annunci, nuovi finora %d", p, len(annunci), nuovi)
        page.wait_for_timeout(random.randint(2000, 4000))
    log.info("  %s: %d annunci letti, %d nuovi", nome, len(visti_ora), nuovi)
    return nuovi


def main():
    log.info("=== LUPIN CASE – Sardegna %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))
    memoria = carica_memoria()
    log.info("Memoria iniziale: %d annunci", len(memoria))

    totale = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT, locale="it-IT", viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        try:
            for sigla, nome in PROVINCE.items():
                log.info("▶ %s (%s)", nome, sigla.upper())
                try:
                    totale += raccogli_provincia(page, sigla, nome, memoria)
                except Exception as e:
                    log.error("  Errore su %s: %s", nome, e)
                # Salvataggio dopo ogni provincia: se il job si interrompe, il lavoro resta
                if not DRY_RUN:
                    salva_memoria(memoria)
                time.sleep(random.uniform(4, 8))
        finally:
            browser.close()

    if DRY_RUN:
        log.info("DRY_RUN: memoria non salvata")
    log.info("=== Fine. Nuovi: %d | Memoria: %d ===", totale, len(memoria))


if __name__ == "__main__":
    main()
