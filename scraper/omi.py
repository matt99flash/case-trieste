"""Quotazioni ufficiali OMI (Agenzia delle Entrate - Osservatorio del Mercato Immobiliare).

Dalla consultazione pubblica geopoi_omi (nessuna registrazione): zone OMI dei 6 comuni della provincia,
con confini e valori di compravendita residenziali (€/mq, min-max). Si aggiornano al massimo una volta
al mese (escono ogni semestre). A ogni giro ogni casa con posizione precisa viene abbinata alla sua zona.

- docs/data/omi.json   valori per zona, letti dalla dashboard (fonte da citare: "Agenzia Entrate - OMI")
- data/omi_zone.json   confini delle zone (servono solo qui)
"""
import html as htmlmod
import json
import os
import re
import time
from datetime import date, timedelta

from curl_cffi import requests as creq

from .store import DATA_DIR, _dump

BASE = "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/"
ROOT = os.path.dirname(os.path.dirname(__file__))
OMI_FILE = os.path.join(DATA_DIR, "omi.json")
ZONES_FILE = os.path.join(os.environ.get("CASE_DATA_DIR") or os.path.join(ROOT, "data"), "omi_zone.json")
COMUNI = {"L424": "Trieste", "F795": "Muggia", "D383": "Duino-Aurisina", "D324": "San Dorligo della Valle",
          "I715": "Sgonico", "F378": "Monrupino"}
REFRESH_DAYS = 30
PRECISE_GEO = ("fonte", "indirizzo")
UA = {"User-Agent": "case-trieste-monitor/1.1 (uso personale; https://github.com/matt99flash/case-trieste)"}

_ROW = re.compile(r"<tr><td[^>]*>([^<]+?)(?:&nbsp;)?</td><td[^>]*>([^<]*?)(?:&nbsp;)?</td>"
                  r"<td[^>]*vmmin[^>]*>([\d.,]*)\*?(?:&nbsp;)?</td><td[^>]*vmmax[^>]*>([\d.,]*)\*?(?:&nbsp;)?</td>")


def _get(path, **params):
    time.sleep(0.5)
    r = creq.get(BASE + path, params=params or None, headers=UA, timeout=30)
    r.raise_for_status()
    return r


def _num(s):
    s = (s or "").replace(".", "").replace(",", ".")
    return round(float(s)) if s else None


def _round_geom(geom):
    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], list) else [round(c[0], 5), round(c[1], 5)]
    return {"type": geom["type"], "coordinates": rnd(geom["coordinates"])}


def _first_point(geom):
    c = geom["coordinates"]
    while isinstance(c[0], list):
        c = c[0]
    return c


def refresh(force: bool = False) -> bool:
    """Scarica le quotazioni se il file manca o ha più di un mese. True se aggiornate."""
    try:
        with open(OMI_FILE, encoding="utf-8") as f:
            fetched = json.load(f).get("fetched")
        if not force and fetched and date.fromisoformat(fetched) > date.today() - timedelta(days=REFRESH_DAYS):
            return False
    except FileNotFoundError:
        pass
    sem = _get("zoneomi.php", richiesta=5).json()[0]["SEMESTRE"]
    zones, shapes = {}, []
    for codcom, comune in COMUNI.items():
        info = {z["ZONA"]: z for z in _get("zoneomi.php", richiesta=3, codcom=codcom).json()}
        feats = _get("zoneomi.php", richiesta=6, codcom=codcom, semestre=sem).json()["dat"]["features"]
        for f in feats:
            code = f["properties"]["zona"]
            z = info.get(code)
            if not z:
                continue
            e, n = _first_point(f["geometry"])
            page = _get(f"stampaomi.php?{codcom}/{z['LINK_ZONA']}/{sem}/R/{code}/{e}/{n}").text
            values = {}
            for tip, stato, lo, hi in _ROW.findall(page):
                tip = htmlmod.unescape(tip).strip()
                if tip.lower().startswith(("abitazioni", "ville")) and _num(lo) and _num(hi):
                    values[tip] = [_num(lo), _num(hi), htmlmod.unescape(stato).strip().lower()]
            key = f"{codcom}/{code}"
            zones[key] = {"comune": comune, "zona": code, "nome": htmlmod.unescape(z["DIZIONE"]).title(), "valori": values}
            shapes.append({"key": key, "geometry": _round_geom(f["geometry"])})
    if not zones:
        raise RuntimeError("nessuna zona OMI letta")
    _dump(OMI_FILE, {"fetched": date.today().isoformat(), "semestre": f"{sem[:4]}/{sem[4:]}",
                     "fonte": "Agenzia Entrate - OMI", "zone": zones})
    os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
    with open(ZONES_FILE, "w", encoding="utf-8") as f:
        json.dump(shapes, f, separators=(",", ":"))
    return True


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_geom(x, y, geom):
    """Regola pari-dispari su tutti gli anelli: i file OMI mettono nello stesso "Polygon" sia pezzi separati
    della zona sia eventuali buchi, quindi non si può assumere "primo anello = esterno, altri = buchi"."""
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    inside = False
    for poly in polys:
        for ring in poly:
            if _in_ring(x, y, ring):
                inside = not inside
    return inside


def assign(listings: dict[str, dict]):
    """Zona OMI ("codice comune/zona") per ogni casa con posizione precisa."""
    try:
        with open(ZONES_FILE, encoding="utf-8") as f:
            shapes = json.load(f)
    except FileNotFoundError:
        return
    for s in shapes:   # riquadro per scartare in fretta le zone lontane
        pts = []
        stack = [s["geometry"]["coordinates"]]
        while stack:
            c = stack.pop()
            if isinstance(c[0], list):
                stack.extend(c)
            else:
                pts.append(c)
        s["bbox"] = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    for r in listings.values():
        if r.get("geo") not in PRECISE_GEO or r.get("lat") is None:
            r.pop("omi", None)
            continue
        x, y = r["lon"], r["lat"]
        r["omi"] = next((s["key"] for s in shapes if s["bbox"][0] <= x <= s["bbox"][2] and s["bbox"][1] <= y <= s["bbox"][3]
                         and _in_geom(x, y, s["geometry"])), None)
