"""Giro completo: legge tutte le fonti, aggiorna l'archivio, raggruppa i doppioni, invia le notifiche.

Uso:
  python -m scraper.run                 # tutte le fonti
  python -m scraper.run --only id1,id2  # solo alcune fonti (prova)
  python -m scraper.run --no-notify
"""
import argparse
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from . import dedup, notify
from .geocode import Geocoder, enrich
from .http import Http
from .sources import load_all
from .sources.base import Context
from .store import Store
from .zones import in_province

ROOT = os.path.dirname(os.path.dirname(__file__))
_print_lock = threading.Lock()


def log(src, msg):
    with _print_lock:
        print(f"[{src}] {msg}", flush=True)


def load_sources() -> list[dict]:
    with open(os.path.join(ROOT, "config", "sources.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return [s for s in cfg.get("sources", []) if s.get("enabled", True)]


def run_source(cfg, adapters, store, time_budget):
    sid = cfg["id"]
    cls = adapters.get(cfg.get("adapter", "generic"))
    if cls is None:
        return sid, None, f"adattatore sconosciuto: {cfg.get('adapter')}", set(), {}
    src = cls(cfg)
    http = Http(min_delay=cfg.get("delay", 1.0))
    ctx = Context(http, store, lambda m: log(sid, m))
    t0 = time.time()
    try:
        items = src.fetch(ctx)
    except Exception as e:
        log(sid, f"ERRORE: {e}")
        if os.environ.get("DEBUG"):
            traceback.print_exc()
        return sid, None, str(e)[:300], set(), {"requests": http.requests, "seconds": round(time.time() - t0)}
    out, skipped = [], 0
    for L in items:
        L.finalize(default_town=src.default_town)
        if L.agency is None and cfg.get("agency"):
            L.agency = cfg["agency"]
        if not in_province(L.town):
            skipped += 1
            continue
        out.append(L.to_dict())
    # stesso annuncio elencato due volte: tieni il primo
    uniq = {}
    for d in out:
        uniq.setdefault(d["id"], d)
    stats = {"requests": http.requests, "seconds": round(time.time() - t0), "outside": skipped,
             "details": len(ctx.detail_ids), "partial_errors": len(ctx.errors)}
    err = f"{len(ctx.errors)} schede non lette (es. {ctx.errors[0][:150]})" if ctx.errors else None
    log(sid, f"{len(uniq)} annunci in provincia ({skipped} fuori), {http.requests} richieste, {stats['seconds']}s")
    return sid, list(uniq.values()), err, set(ctx.detail_ids), stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="id fonti separati da virgola")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", 10)))
    ap.add_argument("--budget", type=int, default=40 * 60, help="secondi massimi")
    args = ap.parse_args()

    adapters = load_all()
    sources = load_sources()
    all_ids = {s["id"] for s in sources}
    if args.only:
        wanted = set(args.only.split(","))
        sources = [s for s in sources if s["id"] in wanted]
    names = {s["id"]: s["name"] for s in sources}
    store = Store()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(run_source, s, adapters, store, args.budget) for s in sources]
        results = [f.result() for f in as_completed(futs)]
    # applica i risultati in sequenza (l'archivio non è thread-safe)
    geocoder = Geocoder()
    for r in results:
        if r[1]:
            enrich(r[1], geocoder)
    geocoder.save()
    if geocoder.done:
        print(f"Geocodifica: {geocoder.done} indirizzi nuovi cercati")
    by_id = {s["id"]: s for s in sources}
    for sid, items, err, detail_ids, stats in results:
        store.apply_source(sid, names[sid], items, err, detail_ids, stats)
        store.sources[sid]["kind"] = by_id[sid].get("kind", "agenzia")
        store.sources[sid]["website"] = by_id[sid].get("website") or (by_id[sid].get("start_urls") or [None])[0]
    if not args.only:
        store.disable_missing_sources(all_ids)
    dedup.assign_groups(store.listings)
    all_names = {s["id"]: s["name"] for s in load_sources()}
    if not args.no_notify:
        notify.send(store, all_names)
        notify.send_health(store)
    store.save()
    ok = sum(1 for r in results if r[1] is not None)
    active = sum(1 for r in store.listings.values() if r["status"] == "active")
    print(f"\nFatto in {round(time.time() - t0)}s: {ok}/{len(results)} fonti ok, {active} annunci attivi, "
          f"{len(store.new_events)} novità")
    return 0


if __name__ == "__main__":
    sys.exit(main())
