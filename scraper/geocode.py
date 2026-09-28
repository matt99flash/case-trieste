"""Posizione sulla mappa dagli indirizzi (OpenStreetMap Nominatim, max 1 richiesta/s, con cache permanente)
e zona di Trieste dedotta dalla posizione."""
import json
import math
import os
import re
import time

from curl_cffi import requests as creq

from .zones import norm

CACHE_FILE = os.path.join(os.environ.get("CASE_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"), "geocache.json")
MAX_PER_RUN = 80
STREET = re.compile(r"\b(via|viale|piazza|piazzale|largo|strada|salita|androna|riva|scala|passeggio|galleria|corso|vicolo|"
                    r"localit[aà]|loc\.|borgo|frazione|contrada|rotonda|campo|str\.)\b", re.I)
BOX = (45.55, 13.55, 45.82, 13.95)  # provincia di Trieste

ZONE_CENTER = {
    "Centro / Borgo Teresiano": (45.6530, 13.7760), "Borgo Giuseppino / Cavana": (45.6470, 13.7690),
    "Città Vecchia / San Giusto": (45.6480, 13.7720), "Borgo Franceschino / Giulia": (45.6515, 13.7850),
    "San Vito / Campi Elisi": (45.6405, 13.7610), "San Giacomo": (45.6390, 13.7780), "Barriera": (45.6450, 13.7820),
    "Chiadino / Rozzol": (45.6440, 13.7960), "San Luigi / Guardiella": (45.6540, 13.8040),
    "Cologna / Scorcola / Gretta": (45.6620, 13.7800), "Roiano": (45.6650, 13.7700),
    "Barcola / Grignano / Miramare": (45.6860, 13.7400), "Carso triestino (Opicina e frazioni)": (45.6950, 13.8000),
    "Servola / Valmaura / Borgo San Sergio": (45.6230, 13.7920), "Montebello / Rotonda del Boschetto": (45.6480, 13.8000),
}


def zone_from_point(lat, lon) -> str:
    # quota: sopra i ~250 m di altitudine si è sul Carso; approssimato con la distanza dalla costa verso nord-est
    best, bd = None, 1e9
    for z, (a, b) in ZONE_CENTER.items():
        d = math.hypot((lat - a) * 111, (lon - b) * 78)
        if d < bd:
            best, bd = z, d
    return best


class Geocoder:
    def __init__(self):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                self.cache = json.load(f)
        except FileNotFoundError:
            self.cache = {}
        self.done = 0
        self.last = 0.0

    def save(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=0, sort_keys=True)

    def lookup(self, address: str, town: str):
        if not address or not STREET.search(address):
            return None
        street = re.sub(r"\s+", " ", address.split(" - ")[0]).strip(" ,")
        street = re.sub(r",?\s*(trieste|muggia|duino[- ]aurisina|sgonico|monrupino|san dorligo della valle)\s*$", "", street, flags=re.I)
        key = norm(f"{street}|{town}")
        if key in self.cache:
            return self.cache[key]
        if self.done >= MAX_PER_RUN:
            return None
        wait = self.last + 1.1 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        self.done += 1
        try:
            r = creq.get("https://nominatim.openstreetmap.org/search", timeout=20, params={
                "q": f"{street}, {town}, Italia", "format": "json", "limit": 1, "countrycodes": "it",
                "viewbox": f"{BOX[1]},{BOX[2]},{BOX[3]},{BOX[0]}", "bounded": 1},
                headers={"User-Agent": "case-trieste-monitor/1.0 (uso personale)"})
            res = r.json() if r.status_code == 200 else None
        except Exception:
            return None  # riprova al prossimo giro
        val = None
        if res:
            lat, lon = float(res[0]["lat"]), float(res[0]["lon"])
            precise = res[0].get("addresstype") in ("building", "house", "road", "residential", "place", "square", "pedestrian") \
                or res[0].get("class") in ("highway", "building", "place")
            val = [round(lat, 5), round(lon, 5)] if precise else None
        self.cache[key] = val
        return val


def enrich(listings: list[dict], geocoder: Geocoder):
    """Aggiunge lat/lon dagli indirizzi e ricava la zona di Trieste dalla posizione quando possibile."""
    for d in listings:
        if d.get("lat") is None and d.get("address") and d.get("town"):
            ll = geocoder.lookup(d["address"], d["town"])
            if ll:
                d["lat"], d["lon"] = ll
                d["geo"] = "indirizzo"
        elif d.get("lat") is not None and not d.get("geo"):
            d["geo"] = "fonte"
        if d.get("town") == "Trieste" and d.get("lat") is not None:
            if BOX[0] <= d["lat"] <= BOX[2] and BOX[1] <= d["lon"] <= BOX[3]:
                d["zone"] = zone_from_point(d["lat"], d["lon"])
