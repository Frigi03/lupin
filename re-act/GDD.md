# RE:ACT — Game Design Document

Versione 1.0 — Concept di sviluppo

> Una reazione può essere un'arma. Una combinazione può diventare una macchina. E una build completamente rotta può essere il tuo unico modo per sopravvivere.

---

## 1. Visione del gioco

RE:ACT è un roguelike strategico a run brevi, basato sulla creazione di reazioni e sinergie.

Il giocatore raccoglie elementi, strumenti e artefatti e li combina per creare effetti sempre più potenti.

Il cuore del gioco non è semplicemente:

> "Trova l'oggetto più forte."

ma:

> "Trova il modo di far funzionare insieme oggetti apparentemente incompatibili."

L'obiettivo è arrivare alla fine della run creando una build capace di produrre reaction chain sempre più elaborate.

**Genere**
- Roguelike
- Deckbuilder-like senza necessariamente usare carte
- Strategy
- Incremental build
- Puzzle/combinazioni
- Single player

**Piattaforme iniziali**
- Android

Successivamente:
- PC
- iOS
- eventualmente Steam Deck

---

## 2. Pillar del gioco

RE:ACT deve essere costruito intorno a 5 principi.

1. **Semplice da capire** — Il giocatore deve poter iniziare a giocare senza leggere 30 pagine di tutorial.
2. **Profondo da padroneggiare** — Dietro poche regole devono esserci moltissime combinazioni.
3. **Sinergie > statistiche** — Un oggetto mediocre può diventare potentissimo con la build giusta.
4. **Ogni run racconta una storia** — Il giocatore deve poter dire: "Questa run è partita con il fuoco, poi ho trovato il petrolio, poi il vento e alla fine ho creato una macchina infernale."
5. **"Ancora una run"** — La durata ideale iniziale: 20–35 minuti.

---

## 3. Fantasy del giocatore

Il giocatore interpreta un Reactor, un individuo capace di manipolare la materia attraverso reazioni.

Il mondo è instabile. Le leggi fisiche non sono completamente affidabili. Ogni zona contiene materiali diversi. Il giocatore deve raggiungere il Nucleo, affrontando esperimenti, creature e boss.

---

## 4. Struttura generale

```
INIZIO
 ↓
Scelta iniziale
 ↓
Zona
 ↓
Combattimento / Evento
 ↓
Ricompensa
 ↓
Scelta percorso
 ↓
Negozio
 ↓
Elite
 ↓
Evento
 ↓
Mini Boss
 ↓
Nuova zona
 ↓
Boss
 ↓
NUCLEO
```

Una run completa contiene: **4 Zone**. Ogni zona contiene circa: 8–12 incontri, 1 Elite, 1 Boss.

---

## 5. Il sistema principale: REACTION ENGINE

Questo è il cuore di RE:ACT. Ogni oggetto possiede:

- **Elemento** — Esempio: 🔥 Fire
- **Proprietà** — Esempio: Combustibile
- **Tag** — Esempio: Fuoco, Liquido, Industriale
- **Potenza** — Valore numerico.
- **Stabilità** — Determina quanto facilmente può provocare una reazione.

---

## 6. I 10 elementi principali

| Elemento | Identità |
|---|---|
| 🔥 Fuoco | danno/esplosioni |
| 💧 Acqua | controllo/raffreddamento |
| 🌱 Natura | crescita/moltiplicazione |
| 🪨 Terra | difesa |
| ⚡ Elettricità | chain |
| 💨 Aria | velocità |
| ☠️ Tossico | danno nel tempo |
| 🧊 Ghiaccio | rallentamento |
| 🔩 Metallo | tecnologia |
| 🌑 Vuoto | caos/manipolazione |

Il Vuoto sarà particolarmente raro.

---

## 7. Tag

La vera profondità arriva dai tag. Un oggetto può avere più tag.

Esempio — Petrolio: FUOCO, LIQUIDO, COMBUSTIBILE, INDUSTRIALE
Esempio — Fulmine: ELETTRICO, ENERGIA, ARIA
Esempio — Spada: METALLO, ARMA, MELEE

Questo permette di creare sinergie senza dover programmare ogni combinazione manualmente.

---

## 8. Sistema di reazioni

Le reazioni possono essere:

**Dirette** — Due elementi interagiscono. Fuoco + Petrolio → Incendio

**Secondarie** — Una reazione produce un nuovo tag. Acqua + Elettricità → CONDUTTIVITÀ

**A catena** — Fuoco → Petrolio → Esplosione → Metallo → Frammentazione → Elettricità → Chain

---

## 9. Esempi di reazioni

- 🔥 + 💧 → **Vapore**: danno moderato, crea ARIA, rimuove FUOCO
- 🔥 + 🌱 → **Bruciatura**: danno elevato, distrugge Nature, genera Cenere
- 💧 + ⚡ → **Elettroshock**: danno, Shock, possibilità di colpire un secondo bersaglio
- 🧊 + 💧 → **Congelamento**: rallenta, accumula Freeze
- 🔥 + 🧊 → **Shock termico**: grande danno, possibilità di distruggere armatura
- 🌱 + 💧 → **Crescita**: genera risorse, aumenta la produzione futura
- 🪨 + 🔩 → **Corazza**: aumenta difesa, genera ARMATURA
- ⚡ + 🔩 → **Macchina**: genera energia, permette effetti tecnologici
- ☠️ + 💧 → **Contaminazione**: veleno ad area
- 🌑 + qualsiasi elemento → **Distorsione**: l'effetto dipende dalla combinazione

---

## 10. Reaction Chain

Uno dei sistemi distintivi. Una reazione può modificare il campo e creare le condizioni per la successiva.

```
ACQUA + ELETTRICITÀ
       ↓
   ELETTROSHOCK
       ↓
     ENERGIA
       ↓
     METALLO
       ↓
     MACCHINA
       ↓
   SOVRACCARICO
       ↓
    ESPLOSIONE
```

Il giocatore vede progressivamente la combinazione diventare più potente.

---

## 11. Moltiplicatore Chain

Ogni reazione consecutiva aumenta il moltiplicatore.

```
Reazione 1 → ×1
Reazione 2 → ×1.5
Reazione 3 → ×2
Reazione 4 → ×3
Reazione 5 → ×5
```

Ma la chain può essere interrotta. Questo crea una decisione: "Continuo a rischiare oppure incasso?"

---

## 12. Overreaction

Meccanica fondamentale. Se il giocatore accumula troppa energia instabile: **OVERREACTION**.

Può accadere qualcosa di estremamente potente. Ma anche qualcosa di negativo.

Esempio: Energia 97/100 + Fulmine → OVERREACTION. Possibili risultati: mega-esplosione, duplicazione, mutazione, perdita di oggetti, effetto casuale.

---

## 13. Instabilità

Ogni oggetto possiede un valore: **Stabilità 0–100**.

Più bassa: più rischio, più potenza, maggiore probabilità di Overreaction.

Questo crea una scelta interessante: Build stabile (danni inferiori ma prevedibili) vs Build instabile (potenzialmente devastante).

---

## 14. Tipologie di oggetti

5 categorie: MATERIALI (usati per creare reazioni), STRUMENTI (modificano il funzionamento), ARMI (producono effetti offensivi), ARTEFATTI (cambiano le regole), CATALIZZATORI (amplificano le reazioni).

---

## 15. Esempi di materiali

**Comune**: Legno, Pietra, Acqua, Carbone, Ferro, Erba, Olio, Sale
**Non comune**: Cristallo, Mercurio, Uranio fantasy, Essenza, Plasma, Resina
**Raro**: Materia oscura, Cuore elementale, Frammento del Vuoto

---

## 16. Artefatti

Gli artefatti devono essere gli oggetti più interessanti.

- 🔥 **Cuore della Fenice** — Ogni volta che una reazione di Fuoco distrugge un oggetto: +1 Energia permanente nella run.
- ⚡ **Condensatore** — Ogni terza reazione Elettrica: duplica l'effetto.
- 🧪 **Alambicco Instabile** — Le reazioni hanno +50% potenza, ma +20% instabilità.
- 🌀 **Paradosso** — Le reazioni fallite possono diventare reazioni casuali.
- 🧲 **Midas** — Ogni oggetto Metallico consumato: +Denaro.

---

## 17. Rarità

5 livelli: ⚪ Comune, 🟢 Non comune, 🔵 Raro, 🟣 Epico, 🟡 Leggendario.

Attenzione: Leggendario ≠ automaticamente migliore. Un oggetto comune potrebbe essere fondamentale per una build.

---

## 18. Sistema di inventario

Il giocatore inizialmente possiede 6 slot. Durante la run può arrivare a 12 slot. Questo obbliga a scegliere: non puoi tenere tutto.

---

## 19. Fusione

Due oggetti compatibili possono essere combinati. Esempio: Ferro + Carbone → Acciaio. Cristallo + Elettricità → Cristallo energetico. La fusione consuma entrambi gli oggetti.

---

## 20. Evoluzione degli oggetti

Alcuni oggetti possono evolversi. Esempio: Legno → Legno trattato → Legno infiammabile → Legno infernale. La progressione dipende dalle reazioni.

---

## 21. Sistema di combattimento

Il combattimento deve essere molto semplice. Il giocatore non controlla direttamente un personaggio durante ogni attacco. Invece:

```
PREPARA → REAGISCI → ATTIVA → CHAIN
```

Il combattimento diventa quindi una specie di puzzle strategico.

---

## 22. Turno

1. Il giocatore riceve energia
2. Sceglie gli oggetti
3. Costruisce una reazione
4. La reazione viene eseguita
5. Il nemico reagisce
6. Il turno termina

---

## 23. Energia

Ogni turno hai una quantità limitata di energia. Esempio: 5 Energia. Oggetto: Petrolio → 2, Fuoco → 1, Vento → 1, Esplosione → 2. Il giocatore deve decidere come spendere le risorse.

---

## 24. Nemici

I nemici non devono essere semplicemente sacchi di HP. Devono avere regole.

- **Slime d'acqua** — Riduce gli effetti di Fuoco.
- **Golem** — Molto resistente ai danni fisici.
- **Parassita** — Si duplica se non viene eliminato rapidamente.
- **Macchina** — Assorbe Elettricità.
- **Entità del Vuoto** — Modifica casualmente le proprietà degli oggetti.

---

## 25. Elite

- **Reattore** — Ogni 3 turni: Overreaction automatica.
- **Alchimista** — Ruba un materiale dopo ogni combattimento.
- **Mimic** — Copia la tua reazione migliore.

---

## 26. Boss

Ogni boss deve modificare le regole.

- **BOSS 1 — IGNIS**: "Ogni turno il campo diventa più caldo." Build consigliata: Acqua / Ghiaccio.
- **BOSS 2 — LEVIATHAN**: "Ogni reazione di Acqua viene duplicata." Ma anche: "Il boss guadagna energia."
- **BOSS 3 — TITAN**: "Ogni 10 danni inflitti genera armatura."
- **BOSS 4 — NULL** (finale): "Le regole cambiano ogni turno."

---

## 27. Mappa

```
              Evento
                /
START — Combattimento — Elite
                \       \
                 Negozio  Evento
                     \
                     Boss
```

Il giocatore sceglie il percorso.

---

## 28. Tipi di nodi

⚔️ Combattimento, 💀 Elite, 👑 Boss, 🛒 Negozio, ❓ Evento, 🧪 Laboratorio, 💎 Tesoro, 🔥 Reattore, 🌀 Anomalia

---

## 29. Eventi

Gli eventi devono offrire decisioni. Esempio: "Hai trovato un laboratorio abbandonato."

- A — Saccheggia: +3 materiali
- B — Attiva il reattore: +1 artefatto raro, ma +30 instabilità
- C — Distruggilo: ricompensa sicura

---

## 30. Negozio

Il negozio vende: materiali, strumenti, artefatti, catalizzatori, slot inventario, upgrade. Il denaro si chiama **REACT**.

---

## 31. Economia

Il giocatore guadagna: **Crediti** (dai combattimenti), **Materia** (usata per crafting), **Essenza** (risorsa rara).

---

## 32. Crafting

Il laboratorio permette di combinare materiali. Esempio: Ferro + Carbone = Acciaio. Acqua + Cristallo = Cristallo acquatico. Acciaio + Elettricità = Bobina. Bobina + Plasma = Generatore.

---

## 33. Build

- **BUILD FUOCO** — Obiettivo: Esplosioni
- **BUILD ELETTRICA** — Obiettivo: Chain
- **BUILD NATURA** — Obiettivo: Moltiplicazione
- **BUILD METALLO** — Obiettivo: Difesa + macchine
- **BUILD VUOTO** — Obiettivo: Manipolazione delle regole

---

## 34. Build ibride

- FIRE + ELECTRIC — Esplosioni + Chain
- WATER + ICE — Controllo
- NATURE + WATER — Generazione
- METAL + ELECTRIC — Tecnologia
- FIRE + POISON — Danno progressivo
- VOID + QUALSIASI — Caos

---

## 35. Sistema di Synergy Discovery

Il gioco non deve mostrare immediatamente tutte le combinazioni. Quando scopri una nuova reazione: "NUOVA REAZIONE SCOPERTA". Il giocatore la registra nel **REACTION CODEX**.

---

## 36. Reaction Codex

Il Codex contiene: elementi, materiali, reazioni, artefatti, boss, combinazioni scoperte. Percentuale di completamento: 67%. Questo crea un secondo obiettivo oltre alla vittoria.

---

## 37. Meta-progressione

Quando perdi una run non perdi tutto. Mantieni: **Esperienza** (sblocca nuovi contenuti), **Codex** (le scoperte rimangono), **Ricercatore** (livello permanente).

---

## 38. Research Tree

```
RESEARCH
│
├── Fire
│   ├── Burn
│   ├── Explosion
│   └── Plasma
│
├── Water
│   ├── Steam
│   ├── Ice
│   └── Pressure
│
├── Technology
│   ├── Machine
│   ├── Energy
│   └── Automation
│
└── Void
    ├── Chaos
    ├── Mutation
    └── Paradox
```

---

## 39. Modalità di gioco

**CLASSIC** — La modalità principale.
**DAILY REACTION** — Ogni giorno stesso seed per tutti, classifica personale.
**CHAOS** — Reazioni completamente imprevedibili.
**ENDLESS** — Continui finché riesci, nemici progressivamente più forti.
**CHALLENGE** — Regole speciali (es. "Solo oggetti di Fuoco").

---

## 40. Daily Run

Ogni giorno il giocatore riceve: stesso inventario iniziale, stessa mappa, stessi eventi. Obiettivo: ottenere il punteggio più alto possibile.

---

## 41. Punteggio

Il punteggio non deve essere solo il danno.

```
SCORE = Danno × Chain × Rarità × Reazioni × Efficienza
```

Bonus per: reaction chain, nessun danno ricevuto, utilizzo di pochi oggetti, Overreaction, combo rare.

---

## 42. "Broken Builds"

Il gioco deve permettere volutamente combinazioni esagerate.

Esempio: Catalizzatore + Duplicatore + Elettricità + Overcharge + Artefatto Chain → Risultato: 1 → 2 → 4 → 8 → 16 → 32 → 64 reazioni.

Il giocatore deve avere la sensazione: "NON DOVEVA FARE QUESTO." Questo è un momento fondamentale per la condivisione sui social.

---

## 43. Grafica

Per contenere i costi: **Pixel art HD**, risoluzione di riferimento 1280×720.

Stile: pixel art moderna, effetti luminosi, particelle, UI pulita, sfondi relativamente semplici.

Non serve creare centinaia di animazioni. Gli effetti delle reazioni possono essere generati con: particelle, sprite, shader, scaling, flash, screen shake.

---

## 44. UI

```
┌─────────────────────────────┐
│ HP  ████████    ENERGY  4/6 │
├─────────────────────────────┤
│                             │
│          ENEMY              │
│                             │
│         👹                  │
│                             │
│     REACTION AREA           │
│                             │
├─────────────────────────────┤
│ 🔥  💧  ⚡  🪨  🔩  ☠️      │
├─────────────────────────────┤
│ INVENTORY                   │
│ [🔥] [💧] [⚡] [🔩] [🧪]    │
└─────────────────────────────┘
```

---

## 45. Feedback visivo

Una reazione importante deve essere spettacolare. Esempio: Reaction Chain ×5 → CHAIN ×5 → ×10 → OVERREACTION → ×20 CRITICAL REACTION → ×50 SYSTEM BREAK.

Questo crea momenti perfetti per clip TikTok/YouTube Shorts.

---

## 46. Audio

Pochi suoni ma molto riconoscibili. Ogni elemento ha una firma sonora: 🔥 Fuoco → crackling, ⚡ Elettricità → buzzing, 💧 Acqua → liquido, 🧊 Ghiaccio → cristallo, 🌑 Vuoto → distorsione.

Una Chain aumenta progressivamente il ritmo musicale.

---

## 47. Tutorial

Il tutorial deve durare massimo 3–5 minuti. Il giocatore scopre: Oggetto → Elemento → Combinazione → Reazione → Chain. Niente tutorial infinito.

---

## 48. Onboarding

Prima partita: il giocatore riceve 🔥 Fuoco, 🛢️ Olio, 💨 Vento. Il gioco praticamente lo guida a creare **INFERNO**. Poi: "Hai appena creato la tua prima Reaction Chain." Da quel momento è libero.

---

## 49. Monetizzazione

Se il gioco è mobile, evitare pubblicità aggressiva.

**Versione Free** — Gioco completo. Pubblicità opzionale: "Guarda un video per ottenere una ricompensa." Mai pubblicità durante una reazione.

**Premium** — RE:ACT Complete: rimuove pubblicità, skin, effetti speciali, contenuti cosmetici.

---

## 50. Niente Pay-to-Win

Non vendere: ❌ oggetti più forti, ❌ energia, ❌ vittorie, ❌ artefatti esclusivi competitivi.

Possibili acquisti: ✅ cosmetici, ✅ effetti delle reazioni, ✅ temi UI, ✅ musiche, ✅ pacchetti estetici.

---

## 51. Viralità

Il gioco deve generare automaticamente contenuti condivisibili. Alla fine di una run: card riassuntiva (elementi usati, CHAIN ×27, DAMAGE 18.492) con pulsante SHARE BUILD.

---

## 52. Replayability

Dalla casualità di: oggetti, eventi, mappe, boss, artefatti, combinazioni, challenge, Daily Run, Codex, difficoltà.

---

## 53. Difficoltà

Dopo il completamento: **REACTOR LEVEL** 0 (normale) → 1 (nemici +10%) → 2 (meno risorse) → 3 (eventi più difficili) → 4 (boss modificati) → 5 (regole severe) → ... → 10 (CHAOS MODE).

---

## 54. Contenuti della versione 1.0 (MVP)

6 elementi, 30 materiali, 15 artefatti, 20 reazioni, 10 nemici, 3 boss, 1 zona, 1 modalità.

---

## 55. Versione 1.0 completa (target)

10 elementi, 100+ materiali, 200+ reazioni, 60+ artefatti, 30+ nemici, 8+ boss, 4 zone, 50+ eventi, 20+ challenge.

---

## 56. Roadmap di sviluppo

**FASE 1 — Core**: inventario, oggetti, elementi, tag, reazioni, danno, energia. Obiettivo: gioco già giocabile senza grafica.

**FASE 2 — Synergy Engine** (la più importante): tag, combinazioni, reaction chain, moltiplicatori, instabilità, Overreaction.

**FASE 3 — Roguelike**: mappa, ricompense, negozio, eventi, boss.

**FASE 4 — Progressione**: Codex, Research Tree, XP, sbloccabili, difficoltà.

**FASE 5 — Grafica**: pixel art, animazioni, particelle, UI, effetti.

**FASE 6 — Audio**: musica, effetti, feedback.

**FASE 7 — Monetizzazione**: solo dopo aver verificato che il gioco sia divertente.

---

## 57. Tecnologia consigliata

**Godot** — gratuito, open source, leggero, ottimo per 2D, esportazione Android, facile da usare per un progetto di questo tipo.

Alternativa: **Unity** — più potente per ecosistemi complessi, ma per questo progetto Godot può essere sufficiente.

---

## 58. Architettura del codice

Importante: non codificare ogni combinazione a mano. Sistema basato sui dati.

```
REACTION
Input: FIRE + COMBUSTIBLE
Output: BURNING
Damage: 25
Tags: FIRE, DAMAGE
```

In questo modo aggiungere 100 nuove reazioni non richiede riscrivere il gioco.

---

## 59. Reaction Engine (pipeline)

```
INPUT
 ↓
TAG DETECTION
 ↓
REACTION MATCH
 ↓
EFFECT GENERATION
 ↓
CHAIN CHECK
 ↓
INSTABILITY CHECK
 ↓
OVERREACTION
 ↓
FINAL RESULT
```

Probabilmente la parte tecnicamente più importante dell'intero progetto.

---

## 60. Prima demo giocabile

Niente: 100 oggetti, storia, 20 mappe, multiplayer, grafica spettacolare.

Solo: 6 elementi → 12 materiali → 10 reazioni → 3 artefatti → 3 nemici → 1 boss.

Se questa versione è divertente anche con grafica provvisoria, abbiamo un gioco.

---

## 61. La prima build (esempio concreto)

**Elementi**: 🔥 Fuoco, 💧 Acqua, ⚡ Elettricità, 🌱 Natura, 🔩 Metallo, 🌑 Vuoto

**Oggetti**: Legno, Pietra, Acqua, Olio, Carbone, Ferro, Erba, Cristallo, Batteria, Plasma, Essenza, Frammento del Vuoto

**Prime reazioni**:
- Fuoco + Legno → Bruciatura
- Fuoco + Olio → Esplosione
- Acqua + Fuoco → Vapore
- Acqua + Elettricità → Elettroshock
- Elettricità + Metallo → Energia
- Natura + Acqua → Crescita
- Metallo + Fuoco → Fusione
- Cristallo + Elettricità → Cristallo energetico
- Vuoto + qualsiasi cosa → Distorsione
- Fuoco + Elettricità → Plasma

---

## 62. La filosofia di RE:ACT

> "Non aggiungere contenuti. Aggiungi possibilità."

Un nuovo oggetto non dovrebbe semplicemente essere "+20% danno". Dovrebbe introdurre una nuova possibilità. Esempio: "Le reazioni di Fuoco possono propagarsi." Quella singola proprietà può interagire con decine di altri sistemi.

---

## 63. Il vero obiettivo

RE:ACT non deve diventare "un gioco con 500 oggetti". Deve diventare "un gioco in cui 50 oggetti possono creare centinaia di combinazioni."

---

## 64. Identità finale

- **Nome**: RE:ACT
- **Sottotitolo**: Break the rules. Create the reaction.
- **Genere**: Roguelike / Strategy / Synergy Builder
- **Partita**: 20–35 minuti
- **Target**: Casual → hardcore
- **Stile**: HD Pixel Art + particelle + effetti moderni
- **USP**: Un sistema di reazioni interconnesse in cui ogni oggetto può diventare parte di una catena e le combinazioni più assurde possono rompere le regole del gioco.

---

## Nota di chiusura dell'autore

Non partire subito dalla grafica. Il primo prototipo dovrebbe essere praticamente brutto: quadrati, numeri e pulsanti.

Se si riesce a fare questo:

```
🔥 + 🛢️ → 💥 → ⚡ → 🔩 → 🤖 → 💥 → CHAIN ×20
```

e il giocatore pensa "Aspetta... posso rifarlo?", si è trovato il cuore del gioco.
