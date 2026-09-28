"""Notifiche sul telefono tramite ntfy (app gratuita): nuovi annunci, variazioni di prezzo, venduti."""
import os

import yaml
from curl_cffi import requests as creq

from .parse_utils import TYPE_LABELS

CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "notify.yaml")

LABEL = {"new": ("🆕", "Nuovo"), "price_down": ("📉", "Ribasso"), "price_up": ("📈", "Rialzo"),
         "removed": ("✅", "Non più online (venduto/ritirato)"), "sold": ("✅", "Venduto / sotto offerta")}


def load_config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _eur(v):
    return f"€ {v:,.0f}".replace(",", ".") if v else "prezzo n.d."


def _matches(rec: dict, flt: dict) -> bool:
    p, mq = rec.get("price"), rec.get("mq")
    if flt.get("price_max") and p and p > flt["price_max"]:
        return False
    if flt.get("price_min") and p and p < flt["price_min"]:
        return False
    if flt.get("mq_min") and mq and mq < flt["mq_min"]:
        return False
    if flt.get("rooms_min") and rec.get("rooms") and rec["rooms"] < flt["rooms_min"]:
        return False
    if flt.get("types") and rec.get("type") not in flt["types"]:
        return False
    if flt.get("towns") and rec.get("town") not in flt["towns"]:
        return False
    if flt.get("zones") and rec.get("town") == "Trieste" and rec.get("zone") not in flt["zones"]:
        return False
    if flt.get("require_price") and not p:
        return False
    return True


def select_events(store) -> list[tuple[dict, dict]]:
    cfg = load_config()
    wanted = set(cfg.get("events") or LABEL)
    flt = cfg.get("filters") or {}
    groups = {}
    for r in store.listings.values():
        groups.setdefault(r.get("group", r["id"]), []).append(r)
    out, seen_groups = [], set()
    for ev in store.new_events:
        if ev["type"] not in wanted:
            continue
        rec = store.listings.get(ev["id"])
        if not rec or not _matches(rec, flt):
            continue
        members = groups.get(rec.get("group", rec["id"]), [rec])
        others_active = [m for m in members if m["id"] != rec["id"] and m["status"] == "active"]
        if ev["type"] == "new" and any(m.get("first_seen", "") < rec["first_seen"] for m in others_active):
            continue  # stessa casa già nota presso un'altra agenzia
        if ev["type"] == "removed" and others_active:
            continue  # ancora in vendita presso un'altra agenzia
        key = (ev["type"], rec.get("group", rec["id"]))
        if key in seen_groups:
            continue
        seen_groups.add(key)
        out.append((ev, rec))
    return out


def _line(ev, rec, source_names):
    icon, label = LABEL[ev["type"]]
    what = TYPE_LABELS.get(rec.get("type"), "Immobile")
    bits = [what]
    if rec.get("mq"):
        bits.append(f"{rec['mq']} mq")
    if rec.get("rooms"):
        bits.append(f"{rec['rooms']} locali")
    where = rec.get("zone") if rec.get("town") == "Trieste" and rec.get("zone") else rec.get("town") or ""
    price = _eur(rec.get("price"))
    if ev["type"] in ("price_down", "price_up") and ev.get("old_price"):
        diff = (ev["price"] - ev["old_price"]) / ev["old_price"] * 100
        price = f"{_eur(ev['old_price'])} → {_eur(ev['price'])} ({diff:+.0f}%)"
    title = f"{icon} {label}: {' · '.join(bits)}" + (f" – {where}" if where else "")
    body = f"{price}\n{source_names.get(rec['source'], rec['source'])}"
    return title, body


def send(store, source_names: dict):
    topic = os.environ.get("NTFY_TOPIC")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    dashboard = os.environ.get("DASHBOARD_URL", "")
    items = select_events(store)
    if not items:
        print("Notifiche: nessuna novità da segnalare")
        return
    if not topic:
        print(f"Notifiche: NTFY_TOPIC non impostato, {len(items)} novità non inviate")
        for ev, rec in items[:20]:
            print("  ", *_line(ev, rec, source_names))
        return
    max_single = load_config().get("max_single", 5)
    msgs = []
    if len(items) <= max_single:
        for ev, rec in items:
            title, body = _line(ev, rec, source_names)
            m = {"topic": topic, "title": title, "message": body, "click": rec["url"],
                 "tags": ["house"], "priority": 4 if ev["type"] in ("new", "price_down") else 3}
            if rec.get("images"):
                m["attach"] = rec["images"][0]
            if dashboard:
                m["actions"] = [{"action": "view", "label": "Dashboard", "url": f"{dashboard}#novita"}]
            msgs.append(m)
    else:
        counts = {}
        for ev, _ in items:
            counts[ev["type"]] = counts.get(ev["type"], 0) + 1
        summary = " · ".join(f"{LABEL[t][0]} {n} {LABEL[t][1].split(' (')[0].lower()}" for t, n in counts.items())
        lines = [_line(ev, rec, source_names)[0] + f" – {_eur(rec.get('price'))}" for ev, rec in items[:12]]
        if len(items) > 12:
            lines.append(f"…e altre {len(items) - 12}")
        msgs.append({"topic": topic, "title": f"🏠 Case Trieste – {summary}", "message": "\n".join(lines),
                     "click": f"{dashboard}#novita" if dashboard else None, "tags": ["house"], "priority": 3})
    for m in msgs:
        m = {k: v for k, v in m.items() if v is not None}
        try:
            r = creq.post(server, json=m, timeout=20)
            if r.status_code >= 400:
                print("Notifica fallita:", r.status_code, r.text[:200])
        except Exception as e:
            print("Notifica fallita:", e)
    print(f"Notifiche: inviati {len(msgs)} messaggi per {len(items)} novità")


def send_health(store):
    """Avviso una tantum quando una fonte non si legge da ~1 giorno (9 giri): probabilmente il sito è cambiato."""
    topic = os.environ.get("NTFY_TOPIC")
    broken = [m["name"] for m in store.sources.values() if m.get("fail_streak") == 9]
    if not broken or not topic:
        return
    dashboard = os.environ.get("DASHBOARD_URL", "")
    msg = {"topic": topic, "title": "⚠️ Case Trieste: fonti da controllare",
           "message": "Non riesco più a leggere: " + ", ".join(broken) + ". Probabilmente il sito è cambiato.",
           "tags": ["warning"], "priority": 2}
    if dashboard:
        msg["click"] = f"{dashboard}#fonti"
    try:
        creq.post(os.environ.get("NTFY_SERVER", "https://ntfy.sh"), json=msg, timeout=20)
    except Exception as e:
        print("Avviso fonti fallito:", e)
