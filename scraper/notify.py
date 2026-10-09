"""Notifiche sul telefono tramite ntfy (app gratuita): nuovi annunci, variazioni di prezzo, venduti."""
import json
import os

from curl_cffi import requests as creq

from .parse_utils import TYPE_LABELS

# Impostazioni modificabili dalla dashboard (sezione "Notifiche"): vedi scraper/settings.py
CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "notify.json")
DEFAULT = {"enabled": True, "events": ["new", "price_down", "price_up", "removed", "sold"],
           "filters": {"types": ["appartamento", "attico", "villa", "casa"]}, "max_single": 5}

LABEL = {"new": ("🆕", "Nuovo"), "price_down": ("📉", "Ribasso"), "price_up": ("📈", "Rialzo"),
         "removed": ("✅", "Non più online (venduto/ritirato)"), "sold": ("✅", "Venduto / sotto offerta")}


def load_config() -> dict:
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return {**DEFAULT, **json.load(f)}
    except FileNotFoundError:
        return dict(DEFAULT)


def _eur(v):
    return f"€ {v:,.0f}".replace(",", ".") if v else "prezzo n.d."


def _matches(rec: dict, flt: dict, kind: str) -> bool:
    p, mq = rec.get("price"), rec.get("mq")
    if not p and not flt.get("include_noprice", True):
        return False
    if flt.get("price_max") and p and p > flt["price_max"]:
        return False
    if flt.get("price_min") and p and p < flt["price_min"]:
        return False
    if flt.get("mq_min") and (not mq or mq < flt["mq_min"]):
        return False
    if flt.get("mq_max") and mq and mq > flt["mq_max"]:
        return False
    if flt.get("rooms_min") and (not rec.get("rooms") or rec["rooms"] < flt["rooms_min"]):
        return False
    if flt.get("types") and (rec.get("type") or "altro") not in flt["types"]:
        return False
    if flt.get("conditions") and (rec.get("condition") or "nd") not in flt["conditions"]:
        return False
    if flt.get("towns") and rec.get("town") not in flt["towns"]:
        return False
    if flt.get("zones") and rec.get("town") == "Trieste" and (rec.get("zone") or "nd") not in flt["zones"]:
        return False
    if flt.get("features") and any((rec.get("features") or {}).get(f) is not True for f in flt["features"]):
        return False
    if flt.get("kinds") and kind not in flt["kinds"]:
        return False
    if flt.get("keywords"):
        blob = " ".join(str(rec.get(k) or "") for k in ("title", "description", "address", "zone")).lower()
        if not all(w.lower() in blob for w in flt["keywords"].split()):
            return False
    return True


def select_events(store) -> list[tuple[dict, dict]]:
    cfg = load_config()
    if not cfg.get("enabled", True):
        return []
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
        kind = "privato" if rec and rec.get("private") else store.sources.get(ev["source"], {}).get("kind", "agenzia")
        if not rec or not _matches(rec, flt, kind):
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


# ------------------------------------------------------------------ riepilogo settimanale

def _week_start(now):
    """Lunedì più recente alle 7:00 (ora italiana) già passato."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    local = now.astimezone(ZoneInfo("Europe/Rome"))
    monday = (local - timedelta(days=local.weekday())).replace(hour=7, minute=0, second=0, microsecond=0)
    return monday if monday <= local else monday - timedelta(days=7)


def _median(vals):
    vals = sorted(vals)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def build_weekly(store, now) -> dict | None:
    """Riepilogo degli ultimi 7 giorni, limitato ai criteri scelti per le notifiche."""
    from datetime import datetime, timedelta
    cfg = load_config()
    flt = cfg.get("filters") or {}
    cut = now - timedelta(days=7)
    kind_of = lambda r: "privato" if r.get("private") else store.sources.get(r["source"], {}).get("kind", "agenzia")
    ok = lambda r: r is not None and _matches(r, flt, kind_of(r))

    # case (gruppi di annunci) oggi in vendita che rispettano i criteri
    groups = {}
    for r in store.listings.values():
        if r["status"] == "active" and ok(r):
            groups.setdefault(r.get("group", r["id"]), r)
    ppm = [r["price"] / r["mq"] for r in groups.values()
           if r.get("price") and r.get("mq") and r.get("type") in ("appartamento", "attico")]
    stats = {"count": len(groups), "median_ppm": round(_median(ppm)) if len(ppm) >= 5 else None}

    new, drops, gone, seen = [], [], 0, set()
    for ev in store.events + store.new_events:
        if datetime.fromisoformat(ev["ts"]) < cut:
            continue
        rec = store.listings.get(ev["id"])
        if not ok(rec):
            continue
        key = (ev["type"], rec.get("group", rec["id"]))
        if key in seen:
            continue
        seen.add(key)
        if ev["type"] == "new" and rec["status"] == "active":
            new.append(rec)
        elif ev["type"] == "price_down" and ev.get("old_price") and rec["status"] == "active":
            drops.append((ev["price"] / ev["old_price"] - 1, ev, rec))
        elif ev["type"] in ("removed", "sold") and (rec["status"] != "active" or rec.get("sold")):
            gone += 1

    def short(r):
        bits = [TYPE_LABELS.get(r.get("type"), "Immobile")]
        if r.get("mq"):
            bits.append(f"{r['mq']} mq")
        where = r.get("zone") if r.get("town") == "Trieste" and r.get("zone") else r.get("town") or ""
        return " ".join(bits) + (f", {where}" if where else "")

    lines = [f"🆕 {len(new)} nuovi · 📉 {len(drops)} ribassi · ✅ {gone} venduti/ritirati"]
    prev = store.weekly_stats or {}
    tot = f"In vendita ora: {stats['count']} immobili con i tuoi criteri"
    if prev.get("count") is not None:
        tot += f" ({stats['count'] - prev['count']:+d} rispetto a 7 giorni fa)"
    lines.append(tot)
    if stats["median_ppm"]:
        m = f"Prezzo mediano appartamenti: {_eur(stats['median_ppm'])}/mq"
        if prev.get("median_ppm"):
            m += f" ({(stats['median_ppm'] / prev['median_ppm'] - 1) * 100:+.1f}%)"
        lines.append(m)
    if drops:
        lines.append("\nRibassi maggiori:")
        for pct, ev, r in sorted(drops, key=lambda x: x[0])[:3]:
            lines.append(f"• {short(r)}: {_eur(ev['old_price'])} → {_eur(ev['price'])} ({pct * 100:.0f}%)")
    if new:
        lines.append("\nNuovi annunci:")
        for r in sorted(new, key=lambda r: r.get("price") or 9e12)[:6]:
            lines.append(f"• {short(r)} – {_eur(r.get('price'))}")
        if len(new) > 6:
            lines.append(f"…e altri {len(new) - 6} nella dashboard")
    msg = "\n".join(lines)
    return {"title": "📊 Case Trieste – riepilogo della settimana", "message": msg[:3800], "stats": stats}


def send_weekly(store, now=None):
    """Una volta a settimana (primo giro dopo lunedì alle 7) manda il riepilogo, se le notifiche sono attive."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    start = _week_start(now)
    if store.weekly_at and datetime.fromisoformat(store.weekly_at) >= start:
        return
    if not load_config().get("enabled", True):
        return
    w = build_weekly(store, now)
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("Riepilogo settimanale (NTFY_TOPIC non impostato):\n" + w["title"] + "\n" + w["message"])
        return
    dashboard = os.environ.get("DASHBOARD_URL", "")
    m = {"topic": topic, "title": w["title"], "message": w["message"], "tags": ["bar_chart"], "priority": 3}
    if dashboard:
        m["click"] = f"{dashboard}#novita"
    try:
        r = creq.post(os.environ.get("NTFY_SERVER", "https://ntfy.sh"), json=m, timeout=20)
        if r.status_code >= 400:
            print("Riepilogo settimanale fallito:", r.status_code, r.text[:200])
            return
    except Exception as e:
        print("Riepilogo settimanale fallito:", e)
        return
    store.weekly_at = store.ts
    store.weekly_stats = w["stats"]
    print("Riepilogo settimanale inviato")
