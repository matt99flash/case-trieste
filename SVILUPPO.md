# Guida tecnica (per chi mantiene il programma)

## Struttura
- `scraper/run.py` – giro completo: legge le fonti in parallelo, geocodifica, aggiorna l'archivio, deduplica, notifica.
- `scraper/sources/*.py` – adattatori. Ogni classe `Source` ha `fetch(ctx) -> list[Listing]` e deve restituire
  **l'elenco completo** degli annunci di VENDITA attivi (gli annunci assenti per 2 giri diventano "non più online").
- `scraper/sources/base.py` – `parse_detail(html, url, source, base)` estrattore generico da scheda immobile
  (JSON-LD, meta, coppie etichetta/valore, testo), `ref_from_url`, `soup_of`, `abs_url`.
- `scraper/sources/generic.py` – adattatore configurabile (pagine elenco → link schede → dettaglio).
- `scraper/models.py` – `Listing` (campi normalizzati). `finalize()` deduce comune, zona, tipologia, stato.
- `scraper/store.py` – archivio in `docs/data/*.json` (`listings.json` leggero per la dashboard, testi e foto in
  `details.json` caricato dopo; `last_seen` salvato solo come data per non riscrivere tutto l'archivio a ogni giro), eventi (new, price_down, price_up, removed, sold, back).
  Una fonte con pochi annunci che all'improvviso risulta vuota è "sospetta" (nessun annuncio rimosso) per 12 giri.
- `scraper/clean.py` – pulizia applicata a ogni giro a tutto l'archivio: zone canoniche, nomi caratteristiche,
  superfici impossibili scartate, box scambiati per appartamenti. Per appartamenti e attici locali, camere e piano
  mancanti vengono letti dalla descrizione (solo se il testo dà un valore unico e non è un'ipotesi); i campi ricavati
  così sono elencati in `txt`. Se il sito dice "Trieste" ma il titolo nomina un altro comune della provincia
  ("Casetta Frazione Sistiana") vale il titolo. Annunci con titolo fuori provincia ("...a Monfalcone") e richieste
  "Cerco casa" sono scartati in `run.py` (i nomi di via come "Via Udine" non contano).
  Nella dashboard il filtro "locali" usa camere + 1 quando i locali mancano.
- `scraper/geocode.py` – posizione dalla via (indirizzo, poi titolo, poi descrizione "situato in via ...") con
  OpenStreetMap; "non trovato" ritentato dopo 60 giorni; i punti condivisi da 6+ annunci della stessa fonte sono la sede
  dell'agenzia e vengono scartati. La zona di Trieste si ricava dalla posizione.
- `scraper/strade.py` – elenco delle vie della provincia da OpenStreetMap (Overpass) in `data/strade.json`; si
  aggiorna a mano ogni tanto (`python -m scraper.strade`, o `python -m scraper.strade Trieste` per un solo comune).
  `geocode.osm_street` lo usa per le vie scritte a metà nei titoli ("BATTISTI 20", "Zona Baiamonti", "V.Del Carpineto"):
  serve un civico subito dopo, una parola come "zona/via" subito prima, o il nome all'inizio del titolo. Se l'indirizzo
  preciso non si trova, la casa va al centro della via (`geo: "via"`, solo vie lunghe al massimo ~1 km): posizione
  approssimata, azzurra in mappa, esclusa da OMI e doppioni.
- `scraper/stima.py` – "prezzo giusto" di ogni casa (`docs/data/stima.json`, chiave = gruppo della casa): regressione
  ridge sul logaritmo del prezzo (superficie, zona, zona OMI, tipologia, stato, piano/ascensore, terrazzo, giardino,
  box, vista mare, 2+ bagni, classe energetica), una riga per casa, in Python puro. Ogni casa è stimata da un modello
  che non l'ha vista (5 gruppi a rotazione) e l'errore tipico (`mdape`, ~18%) è mostrato in dashboard.
  "Possibile affare": appartamento/attico con stato noto, zona con almeno 8 annunci nel campione, prezzo almeno il
  30% (e 1,5 volte l'errore tipico) sotto la stima, non oltre il 50% (oltre è un dato sbagliato), esclusi nuda
  proprietà, locati, aste. Notifica "deal" in `notify.py`: solo da annunci nuovi/ribassati/tornati online, una
  notifica per casa al posto di quella normale; nel riepilogo settimanale i 3 prezzi più sotto la stima.
- `scraper/dedup.py` – stessa casa su più agenzie: prezzo/superficie vicini + luogo (stessa via, < 150 m o stessa zona);
  vie diverse, piani diversi o > 600 m = case diverse. Due gruppi si uniscono solo se nessuna coppia è incompatibile.
- `scraper/trend.py` – `docs/data/trend.json`: mediana €/mq per zona, una fotografia al giorno (scheda "Prezzi"
  della dashboard); se il file manca ricostruisce i giorni passati dall'archivio.
- Dashboard, strumenti per decidere: storia della casa, calcolo del costo vero e della rata, stato e appunti
  (solo nel browser, `localStorage`: il repository è pubblico), confronto dei preferiti.
- `scraper/omi.py` – quotazioni ufficiali OMI (Agenzia Entrate) dalla consultazione pubblica geopoi_omi, aggiornate al
  massimo una volta al mese: valori in `docs/data/omi.json`, confini in `data/omi_zone.json`; ogni casa con posizione
  precisa riceve il campo `omi` ("codice comune/zona"). Attenzione: nei confini un "Polygon" può contenere più pezzi
  separati, per questo si usa la regola pari-dispari su tutti gli anelli.
- Posizione data dalla fonte fuori provincia: se il testo cita un luogo fuori provincia l'annuncio si scarta,
  altrimenti si toglie solo la posizione (`run.py`).
- `scraper/notify.py` – notifiche ntfy, avviso fonti rotte, riepilogo settimanale (primo giro dopo lunedì alle 7).
- `scraper/favorites.py` + `.github/workflows/preferiti.yml` – preferiti condivisi tra i dispositivi collegati
  (issue "Preferiti ..." del proprietario → `docs/favorites.json`, codici FNV-1a a 8 cifre dell'id della casa).
- `config/sources.yaml` – elenco fonti. `config/notify.yaml` – criteri notifiche.
- `docs/` – dashboard statica (GitHub Pages).

## Regole per un adattatore
- Solo **vendita** (niente affitti), solo **provincia di Trieste** (il filtro sul comune è automatico dopo `finalize`,
  ma se il sito copre più province conviene filtrare già la ricerca).
- `ref` stabile nel tempo (codice/id dell'annuncio, non la posizione in pagina).
- Se l'elenco contiene già prezzo e dati principali, evitare di scaricare tutte le schede a ogni giro:
  usare `ctx.store.needs_detail(id, prezzo_elenco)` e `ctx.merge_known(listing)` per riusare i dettagli già noti,
  e aggiungere a `ctx.detail_ids` gli id di cui si è letta la scheda.
- Errori su singole schede: `ctx.errors.append(...)` e proseguire. Errore sull'elenco: sollevare eccezione
  (la fonte viene segnata in errore e i suoi annunci NON vengono considerati rimossi).
- Cortesia: `ctx.http` rispetta già una pausa minima per sito (`delay` in config, default 1 s).

## Prove
```
python -m unittest discover tests                    # regole di lettura (anche su GitHub a ogni modifica)
python -m scraper.run --only id_fonte --no-notify    # una fonte; con CASE_DATA_DIR=cartella usa una copia dei dati
```
