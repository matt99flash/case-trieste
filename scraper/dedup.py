"""Raggruppa lo stesso immobile pubblicato da fonti diverse (stessa casa presso più agenzie)."""
from collections import defaultdict

RESIDENTIAL_FAMILY = {"appartamento": "res", "attico": "res", "villa": "house", "casa": "house"}


def _similar(a: dict, b: dict) -> bool:
    if a["source"] == b["source"]:
        return False
    pa, pb, ma, mb = a.get("price"), b.get("price"), a.get("mq"), b.get("mq")
    if not (pa and pb and ma and mb):
        return False
    if abs(pa - pb) > 0.04 * max(pa, pb):
        return False
    if abs(ma - mb) > max(3, 0.05 * max(ma, mb)):
        return False
    if a.get("rooms") and b.get("rooms") and abs(a["rooms"] - b["rooms"]) > 1:
        return False
    if a.get("zone") and b.get("zone") and a["zone"] != b["zone"]:
        return False
    if a.get("floor") and b.get("floor") and a["floor"] != b["floor"]:
        return False
    # prezzo e superficie entrambi quasi identici: segnale forte; altrimenti serve anche la zona uguale
    exact = pa == pb and ma == mb
    return exact or bool(a.get("zone") and a.get("zone") == b.get("zone"))


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
    for items in buckets.values():
        items.sort(key=lambda r: r.get("price") or 0)
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if (b.get("price") or 0) > (a.get("price") or 0) * 1.05:
                    break
                if _similar(a, b):
                    parent[find(a["id"])] = find(b["id"])

    members = defaultdict(list)
    for r in active:
        members[find(r["id"])].append(r)
    for group in members.values():
        # id del gruppo = annuncio visto per primo: resta stabile nel tempo
        first = min(group, key=lambda r: (r.get("first_seen", ""), r["id"]))
        for r in group:
            r["group"] = first["id"]
    for r in listings.values():
        if r["status"] != "active":
            r.setdefault("group", r["id"])
