# lupin

- **Lupin gare** (`lupin_gare.py`, workflow `lupin_gare.yml`): gare d'appalto ANAC in Sardegna valutate con l'AI, inviate ogni mattina su Telegram. Pagina: `gare/`.
- **Lupin Case** (`lupin_case.py`, workflow `lupin_case.yml`): una volta al giorno raccoglie gli annunci di vendita case di tutta la Sardegna da Wikicasa e li salva in `annunci_memoria.json` (prezzo, mq, prezzo/mq, comune, provincia). Niente notifiche: è la base dei prezzi di zona per Lupin Aste.
