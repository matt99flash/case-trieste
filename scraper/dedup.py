"""Raggruppa lo stesso immobile pubblicato da fonti diverse (stessa casa presso più agenzie)."""
import math
import re
from collections import defaultdict

from .geocode import street_of
from .parse_utils import floor_level
from .zones import norm

RESIDENTIAL_FAMILY = {"appartamento": "res", "attico": "res", "villa": "house", "casa": "house"}
PRECISE_GEO = ("fonte", "indirizzo")
NEAR_KM, FAR_KM = 0.15, 0.6


def _km(a: dict, b: dict) -> float | None:
    if a.get("geo") in PRECISE_GEO and b.get("geo") in PRECISE_GEO and a.get("lat") and b.get("lat"):
        return math.hypot((a["lat"] - b["lat"]) * 111, (a["lon"] - b["lon"]) * 78)
    return None


def _street(r: dict) -> str | None:
    """Nome della via senza civico, per confronto ("Via Giulia 34" e "VIA GIULIA" sono la stessa via)."""
    if "_street" not in r:
        s = street_of(r.get("address"), r.get("title"), None, r.get("town"))
        s = norm(re.sub(r"\s\d.*$", "", s or "")).replace("viale", "via").strip()
        s = re.sub(r"^(via|piazza|largo|strada|salita|localita)\s+", "", s)
        r["_street"] = re.sub(r"^(?:dell'|(?:della|delle|dello|degli|dei|del|di)\s+)", "", s) or None   # "via di Cologna" = "via Cologna"
    return r["_street"]


def _conflict(a: dict, b: dict) -> bool:
    """Indizi certi che si tratta di due case diverse."""
    if a["source"] == b["source"]:
        return True
    fa, fb = floor_level(a.get("floor")), floor_level(b.get("floor"))
    if fa is not None and fb is not None and fa != fb:
        return True
    km = _km(a, b)
    if km is not None and km > FAR_KM:
        return True
    sa, sb = _street(a), _street(b)
    return bool(sa and sb and sa != sb and not (km is not None and km <= NEAR_KM))


def _score(a: dict, b: dict) -> int:
    """0 = case diverse; altrimenti quanto è sicuro che sia la stessa casa (più alto = più sicuro)."""
    pa, pb, ma, mb = a.get("price"), b.get("price"), a.get("mq"), b.get("mq")
    if not (pa and pb and ma and mb) or _conflict(a, b):
        return 0
    dp, dm = abs(pa - pb) / max(pa, pb), abs(ma - mb)
    if dp > 0.10 or dm > max(5, 0.06 * max(ma, mb)):
        return 0
    if a.get("rooms") and b.get("rooms") and abs(a["rooms"] - b["rooms"]) > 1:
        return 0
    km = _km(a, b)
    same_street = bool(_street(a) and _street(a) == _street(b))
    near = same_street or (km is not None and km <= NEAR_KM)
    same_zone = bool(a.get("zone") and a.get("zone") == b.get("zone"))
    if a.get("zone") and b.get("zone") and not same_zone and not near:
        return 0
    numbers = (2 if pa == pb else 1 if dp <= 0.04 else 0) + (2 if ma == mb else 1 if dm <= max(3, 0.05 * max(ma, mb)) else 0)
    place = 2 if near else 1 if same_zone else 0
    fa = floor_level(a.get("floor"))
    extra = (fa is not None and fa == floor_level(b.get("floor"))) + bool(a.get("rooms") and a.get("rooms") == b.get("rooms"))         + bool(a.get("bathrooms") and a.get("bathrooms") == b.get("bathrooms"))
    total = numbers + place + extra
    # come prima: prezzo e superficie identici bastano; altrimenti numeri vicini e luogo confermato
    if numbers == 4 or (numbers >= 2 and place >= 1 and total >= 5):
        return total
    return 0


def assign_groups(listings: dict[str, dict]):
    active = [r for r in listings.values() if r["status"] == "active"]
    parent = {r["id"]: r["id"] for r in active}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets = defaultdict(list)
    for r in active:
        fam = RESIDENTIAL_FAMILY.get(r.get("type"), r.get("type"))
        buckets[(r.get("town"), fam)].append(r)
    pairs = []
    for items in buckets.values():
        items.sort(key=lambda r: r.get("price") or 0)
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if (b.get("price") or 0) > (a.get("price") or 0) * 1.12:
                    break
                sc = _score(a, b)
                if sc:
                    pairs.append((sc, a, b))
    # prima le coppie più sicure; due gruppi si uniscono solo se nessuna coppia tra i loro annunci è incompatibile
    # (evita le catene: A simile a B, B simile a C, ma A e C sono case diverse della stessa agenzia)
    groups = {r["id"]: [r] for r in active}
    pairs.sort(key=lambda p: (-p[0], p[1]["id"], p[2]["id"]))
    for _, a, b in pairs:
        ra, rb = find(a["id"]), find(b["id"])
        if ra == rb or any(_conflict(x, y) for x in groups[ra] for y in groups[rb]):
            continue
        parent[ra] = rb
        groups[rb] += groups.pop(ra)

    members = defaultdict(list)
    for r in active:
        r.pop("_street", None)
        members[find(r["id"])].append(r)
    for group in members.values():
        # id del gruppo = annuncio visto per primo: resta stabile nel tempo
        first = min(group, key=lambda r: (r.get("first_seen", ""), r["id"]))
        for r in group:
            r["group"] = first["id"]
    for r in listings.values():
        if r["status"] != "active":
            r.setdefault("group", r["id"])
