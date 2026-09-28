# Guida tecnica (per chi mantiene il programma)

## Struttura
- `scraper/run.py` – giro completo: legge le fonti in parallelo, geocodifica, aggiorna l'archivio, deduplica, notifica.
- `scraper/sources/*.py` – adattatori. Ogni classe `Source` ha `fetch(ctx) -> list[Listing]` e deve restituire
  **l'elenco completo** degli annunci di VENDITA attivi (gli annunci assenti per 2 giri diventano "non più online").
- `scraper/sources/base.py` – `parse_detail(html, url, source, base)` estrattore generico da scheda immobile
  (JSON-LD, meta, coppie etichetta/valore, testo), `ref_from_url`, `soup_of`, `abs_url`.
- `scraper/sources/generic.py` – adattatore configurabile (pagine elenco → link schede → dettaglio).
- `scraper/models.py` – `Listing` (campi normalizzati). `finalize()` deduce comune, zona, tipologia, stato.
- `scraper/store.py` – archivio in `docs/data/*.json`, eventi (new, price_down, price_up, removed, sold, back).
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
python -m scraper.run --only id_fonte --no-notify
```
