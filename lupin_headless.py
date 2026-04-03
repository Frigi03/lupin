import os
import re
import time
import random
import requests
from datetime import datetime
from camoufox.sync_api import Camoufox

# --- CONFIGURAZIONE ---
# Usiamo le 'Secrets' di GitHub per non mostrare i token in chiaro
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FILE_MEMORIA = "annunci_inviati.txt"

MEDIE = {
    "Milano": 5200, "Roma": 3500, "Torino": 1900,
    "Bologna": 3200, "Napoli": 2500, "Firenze": 3900
}

POSTI_PRINCIPALI = [
    {"nome": "Milano", "url": "https://www.wikicasa.it/vendita-case/milano", "media": 5200},
    {"nome": "Roma", "url": "https://www.wikicasa.it/vendita-case/roma", "media": 3500},
    {"nome": "Torino", "url": "https://www.wikicasa.it/vendita-case/torino", "media": 1900},
    {"nome": "Napoli", "url": "https://www.wikicasa.it/vendita-case/napoli", "media": 2500},
    {"nome": "Bologna", "url": "https://www.wikicasa.it/vendita-case/bologna", "media": 3200},
    {"nome": "Firenze", "url": "https://www.wikicasa.it/vendita-case/firenze", "media": 3900}
]

def gia_inviato(id_annuncio):
    if not os.path.exists(FILE_MEMORIA): return False
    with open(FILE_MEMORIA, "r") as f:
        return id_annuncio in f.read()

def salva_inviato(id_annuncio):
    with open(FILE_MEMORIA, "a") as f:
        f.write(id_annuncio + "\n")

def invia_telegram(messaggio, path_foto=None):
    url_msg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    url_photo = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        if path_foto and os.path.exists(path_foto):
            with open(path_foto, 'rb') as f:
                requests.post(url_photo, data={"chat_id": TELEGRAM_CHAT_ID}, files={"photo": f})
        requests.post(url_msg, json={"chat_id": TELEGRAM_CHAT_ID, "text": messaggio, "parse_mode": "Markdown"})
    except: pass

def missione_lupin(target):
    nome, url, media = target['nome'], target['url'], target['media']
    trovati = 0
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.evaluate("window.scrollTo(0, 1000)")
            time.sleep(5)
            testo = page.locator("body").inner_text()
            
            prezzi = re.findall(r'(?:€\s*)?(\d{1,3}(?:\.\d{3})+)', testo)
            for p_str in prezzi:
                valore = int(p_str.replace('.', ''))
                if valore < 45000 or valore > 5000000: continue
                
                # Logica semplificata per velocità su GitHub
                id_an = f"{valore}_{nome}"
                if not gia_inviato(id_an):
                    # Qui potresti aggiungere il calcolo MQ se necessario
                    invia_telegram(f"🚨 POTENZIALE AFFARE A {nome.upper()}!\nPrezzo: €{valore:,}\nLink: {url}")
                    salva_inviato(id_an)
                    trovati += 1
        except: pass
    return trovati

if __name__ == "__main__":
    totale = 0
    for t in POSTI_PRINCIPALI:
        totale += missione_lupin(t)
    print(f"Fine giro. Trovati: {totale}")
