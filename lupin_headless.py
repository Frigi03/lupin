import os
import re
import time
import requests
from camoufox.sync_api import Camoufox

# --- CONFIGURAZIONE ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FILE_MEMORIA = "annunci_inviati.txt"

POSTI_PRINCIPALI = [
    {"nome": "Milano", "url": "https://www.wikicasa.it/vendita-case/milano", "media": 5200},
    {"nome": "Roma", "url": "https://www.wikicasa.it/vendita-case/roma", "media": 3500},
    {"nome": "Torino", "url": "https://www.wikicasa.it/vendita-case/torino", "media": 1900},
    {"nome": "Napoli", "url": "https://www.wikicasa.it/vendita-case/napoli", "media": 2500},
    {"nome": "Bologna", "url": "https://www.wikicasa.it/vendita-case/bologna", "media": 3200},
    {"nome": "Firenze", "url": "https://www.wikicasa.it/vendita-case/firenze", "media": 3900}
]

def gia_inviato(url):
    if not os.path.exists(FILE_MEMORIA): return False
    with open(FILE_MEMORIA, "r") as f:
        return url in f.read()

def salva_inviato(url):
    with open(FILE_MEMORIA, "a") as f:
        f.write(url + "\n")

def invia_telegram(messaggio):
    url_msg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url_msg, json={"chat_id": TELEGRAM_CHAT_ID, "text": messaggio, "parse_mode": "Markdown"}, timeout=15)
    except: pass

def missione_lupin(target):
    nome, url_citta, media = target['nome'], target['url'], target['media']
    trovati = 0
    
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        try:
            # User agent realistico per evitare blocchi
            page.goto(url_citta, wait_until="networkidle", timeout=60000)
            
            # 1. Identifichiamo tutte le 'Card' degli annunci
            # Wikicasa usa spesso tag article o div con classi specifiche per le proprietà
            items = page.locator("a[href*='/vendita-case/']").all()
            
            for item in items:
                href = item.get_attribute("href")
                
                # PULIZIA LINK: 
                # Un link di un annuncio vero è lungo e ha un ID o un indirizzo specifico.
                # Se il link contiene solo la città o 'quotazioni', è un filtro e lo scartiamo.
                if not href or len(href.split('/')) < 6 or "quotazioni" in href or "napoli" == href.split('/')[-1]:
                    continue
                
                full_link = f"https://www.wikicasa.it{href}" if href.startswith("/") else href
                
                if not gia_inviato(full_link):
                    # Proviamo a prendere il prezzo dal testo dentro la card
                    testo_card = item.inner_text()
                    prezzo_m = re.search(r'(\d{1,3}(?:\.\d{3})+)', testo_card)
                    
                    if prezzo_m:
                        valore = int(prezzo_m.group(1).replace('.', ''))
                        if valore < 40000: continue
                        
                        msg = (f"🚨 *LUPIN: NUOVO ANNUNCIO A {nome.upper()}*\n"
                               f"💰 Prezzo: €{valore:,}\n"
                               f"📍 [CLICCA QUI PER L'ANNUNCIO DIRETTO]({full_link})")
                        
                        invia_telegram(msg)
                        salva_inviato(full_link)
                        trovati += 1
                        time.sleep(1.5) # Evitiamo lo spam selvaggio
        except Exception as e:
            print(f"Errore su {nome}: {e}")
    return trovati

if __name__ == "__main__":
    for t in POSTI_PRINCIPALI:
        print(f"[*] Analisi: {t['nome']}")
        missione_lupin(t)
