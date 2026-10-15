"""Andamento del prezzo al mq per zona: una fotografia al giorno in docs/data/trend.json.

Stesso criterio della dashboard: appartamenti e attici usati (niente nuove costruzioni), una sola volta per casa
anche se è su più agenzie, mediana del €/mq. La prima volta ricostruisce i giorni passati dall'archivio
(data di comparsa, data di ritiro e storico prezzi di ogni annuncio).
"""
import json
import os
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from .store import DATA_DIR, _dump

TREND_FILE = os.path.join(DATA_DIR, "trend.json")
TYPES = {"appartamento", "attico"}
ALL = "Tutta la provincia"


def _price_on(rec: dict, day: str):
    price = None
    for ts, p in rec.get("price_history") or []:
        if ts[:10] <= day:
            price = p
    return price or (rec.get("price") if (rec.get("first_seen") or "")[:10] <= day else None)


def _active_on(rec: dict, day: str) -> bool:
    if (rec.get("first_seen") or "9999")[:10] > day:
        return False
    return rec["status"] == "active" or (rec.get("removed_at") or "0000")[:10] > day


def snapshot(listings: dict[str, dict], day: str) -> dict:
    """{zona: [mediana €/mq, numero di case]} per il giorno `day` (AAAA-MM-GG)."""
    per_group = {}
    for r in listings.values():
        if r.get("type") not in TYPES or r.get("condition") == "nuovo" or (r.get("features") or {}).get("_project"):
            continue
        if not r.get("mq") or not _active_on(r, day):
            continue
        price = _price_on(r, day)
        if not price:
            continue
        per_group.setdefault(r.get("group") or r["id"], (r, price / r["mq"]))
    buckets = defaultdict(list)
    for r, ppm in per_group.values():
        key = r.get("zone") if r.get("town") == "Trieste" else r.get("town")
        if key:
            buckets[key].append(ppm)
        buckets[ALL].append(ppm)
    return {k: [round(median(v)), len(v)] for k, v in sorted(buckets.items()) if len(v) >= 5}


def update(listings: dict[str, dict], today: str | None = None):
    today = today or date.today().isoformat()
    try:
        with open(TREND_FILE, encoding="utf-8") as f:
            days = json.load(f).get("days", {})
    except FileNotFoundError:
        days = {}
        firsts = [r["first_seen"][:10] for r in listings.values() if r.get("first_seen")]
        if firsts:
            d = date.fromisoformat(min(firsts))
            while d.isoformat() < today:
                days[d.isoformat()] = snapshot(listings, d.isoformat())
                d += timedelta(days=1)
    days[today] = snapshot(listings, today)
    _dump(TREND_FILE, {"updated": today, "days": dict(sorted(days.items()))})
