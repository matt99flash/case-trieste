"""Elenco delle vie della provincia di Trieste (OpenStreetMap, servizio Overpass), salvato in data/strade.json.

Serve a riconoscere le vie scritte "a metà" negli annunci ("BATTISTI 20", "Godeas 99", "Zona Baiamonti") e a dare
una posizione approssimata (il centro della via) quando l'indirizzo preciso non si trova.
Si aggiorna a mano, raramente (le vie cambiano poco):  python -m scraper.strade
"""
import json
import math
import os
import sys
import time
from collections import defaultdict

from curl_cffi import requests as creq

from .zones import norm

FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "strade.json")
# il servizio principale è spesso occupato: si provano anche i server alternativi
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
# comune del monitoraggio -> nome del comune in OpenStreetMap (espressione regolare)
COMUNI = {"Trieste": "^Trieste$", "Muggia": "^Muggia", "Duino-Aurisina": "^Duino", "San Dorligo della Valle": "^San Dorligo",
          "Sgonico": "^Sgonico", "Monrupino": "^Monrupino"}


def _fetch(town_rx: str) -> list[dict]:
    q = f"""[out:json][timeout:180];
area["boundary"="administrative"]["admin_level"="8"]["name"~"{town_rx}"]->.a;
(way["highway"]["name"](area.a); way["place"="square"]["name"](area.a););
out center tags;"""
    err = None
    for url in OVERPASS * 2:
        try:
            r = creq.post(url, data={"data": q}, timeout=240,
                          headers={"User-Agent": "case-trieste-monitor/1.1 (uso personale; https://github.com/matt99flash/case-trieste)"})
            r.raise_for_status()
            return r.json()["elements"]
        except Exception as e:
            err = e
            print(f"  {url}: {e}")
            time.sleep(15)
    raise RuntimeError(f"Overpass non raggiungibile: {err}")


def build(only: list[str] | None = None) -> dict:
    try:
        with open(FILE, encoding="utf-8") as f:
            out = json.load(f)
    except FileNotFoundError:
        out = {}
    for town, rx in COMUNI.items():
        if only and town not in only:
            continue
        pts = defaultdict(list)
        names = {}
        try:
            elements = _fetch(rx)
        except RuntimeError as e:   # comune non scaricato: resta l'elenco precedente, si riprova un'altra volta
            print(f"{town}: {e}")
            continue
        for el in elements:
            name = el.get("tags", {}).get("name")
            c = el.get("center")
            if not name or not c:
                continue
            name = name.split(" - ")[0].strip()          # "Via Giulia - Ulica Giulia": nome italiano
            k = norm(name)
            pts[k].append((c["lat"], c["lon"]))
            names[k] = name
        streets = {}
        for k, ps in pts.items():
            lat = sum(p[0] for p in ps) / len(ps)
            lon = sum(p[1] for p in ps) / len(ps)
            # distanza massima di un pezzo dal centro: le vie lunghe (Costiera, Flavia...) attraversano più zone
            spread = max(math.hypot((p[0] - lat) * 111, (p[1] - lon) * 78) for p in ps)
            streets[k] = [names[k], round(lat, 5), round(lon, 5), round(spread, 2)]
        out[town] = dict(sorted(streets.items()))
        print(f"{town}: {len(streets)} vie")
    return out


def main():
    data = build(sys.argv[1:] or None)   # es. "python -m scraper.strade Trieste": solo quel comune
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
