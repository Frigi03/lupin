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

def gia_inviato(id_annuncio):
    if not os.path.exists(FILE_MEMORIA): return False
    with open(FILE_MEMORIA, "r") as f:
        return id_annuncio in f.read()

def salva_inviato(id_annuncio):
    with open(FILE_MEMORIA, "a") as f:
        f.write(id_annuncio + "\n")

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
            page.goto(url_citta, wait_until="networkidle", timeout=60000)
            # Cerchiamo tutti i link che portano a un annuncio specifico
            # Solitamente su Wikicasa hanno un pattern specifico
            cards = page.locator("a[href*='/vendita-case/']").all()
            
            for card in cards:
                href = card.get_attribute("href")
                if not href or href == url_citta or "/milano" == href or len(href) < 30: 
                    continue # Salta i link generici
                
                full_link = f"https://www.wikicasa.it{href}" if href.startswith("/") else href
                
                # Per brevità, usiamo l'URL come ID unico
                if not gia_inviato(full_link):
                    # Estraiamo il prezzo dal testo della card (se possibile) o andiamo a intuito
                    testo_card = card.inner_text()
                    prezzo_m = re.search(r'(\d{1,3}(?:\.\d{3})+)', testo_card)
                    
                    if prezzo_m:
                        valore = int(prezzo_m.group(1).replace('.', ''))
                        # Filtro base
                        if valore < 45000: continue
                        
                        msg = (f"🚨 *AFFARE IDENTIFICATO - {nome.upper()}*\n"
                               f"💰 Prezzo: €{valore:,}\n"
                               f"🔗 [VEDI CASA ORA]({full_link})")
                        
                        invia_telegram(msg)
                        salva_inviato(full_link)
                        trovati += 1
                        time.sleep(1) # Piccola pausa tra invii
        except Exception as e:
            print(f"Errore su {nome}: {e}")
    return trovati

if __name__ == "__main__":
    for t in POSTI_PRINCIPALI:
        print(f"Scansione {t['nome']}...")
        missione_lupin(t)
