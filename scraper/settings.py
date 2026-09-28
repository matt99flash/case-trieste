"""Applica le impostazioni notifiche inviate dalla dashboard (tramite una 'issue' GitHub aperta dal proprietario).

La dashboard apre una issue con titolo "Impostazioni notifiche" e nel testo un blocco ```json ...```.
Questo script (lanciato da .github/workflows/impostazioni.yml) la valida e la salva in docs/notify.json.
"""
import json
import os
import re
import sys

from curl_cffi import requests as creq

from .notify import CONFIG, DEFAULT, LABEL
from .zones import TOWNS, ZONES_TRIESTE

TYPES = {"appartamento", "attico", "villa", "casa", "terreno", "box", "commerciale", "altro"}
CONDITIONS = {"nuovo", "ristrutturato", "buono", "da_ristrutturare", "nd"}
FEATURES = {"elevator", "garage", "terrace", "garden", "sea_view"}
KINDS = {"agenzia", "costruttore", "privato"}


def _num(v, lo, hi):
    try:
        v = int(float(v))
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def validate(raw: dict) -> dict:
    f = raw.get("filters") or {}
    pick = lambda vals, allowed: [v for v in (vals or []) if v in allowed]
    out = {
        "enabled": bool(raw.get("enabled", True)),
        "events": pick(raw.get("events"), set(LABEL)) or DEFAULT["events"],
        "max_single": _num(raw.get("max_single"), 1, 30) or 5,
        "filters": {
            "price_min": _num(f.get("price_min"), 1, 50_000_000),
            "price_max": _num(f.get("price_max"), 1, 50_000_000),
            "mq_min": _num(f.get("mq_min"), 1, 10_000),
            "mq_max": _num(f.get("mq_max"), 1, 10_000),
            "rooms_min": _num(f.get("rooms_min"), 1, 20),
            "types": pick(f.get("types"), TYPES),
            "conditions": pick(f.get("conditions"), CONDITIONS),
            "towns": pick(f.get("towns"), set(TOWNS)),
            "zones": pick(f.get("zones"), set(ZONES_TRIESTE) | {"nd"}),
            "features": pick(f.get("features"), FEATURES),
            "kinds": pick(f.get("kinds"), KINDS),
            "keywords": str(f.get("keywords") or "")[:100].strip() or None,
            "include_noprice": bool(f.get("include_noprice", True)),
        },
    }
    out["filters"] = {k: v for k, v in out["filters"].items() if v not in (None, [], "")}
    return out


def describe(cfg: dict) -> str:
    f = cfg["filters"]
    eur = lambda v: f"€ {v:,.0f}".replace(",", ".")
    bits = []
    if f.get("price_min") or f.get("price_max"):
        bits.append("prezzo " + (f"da {eur(f['price_min'])} " if f.get("price_min") else "") +
                    (f"fino a {eur(f['price_max'])}" if f.get("price_max") else ""))
    if f.get("mq_min") or f.get("mq_max"):
        bits.append("superficie " + (f"da {f['mq_min']} " if f.get("mq_min") else "") + (f"fino a {f['mq_max']} " if f.get("mq_max") else "") + "mq")
    if f.get("rooms_min"):
        bits.append(f"almeno {f['rooms_min']} locali")
    if f.get("towns"):
        bits.append("comuni: " + ", ".join(f["towns"]))
    if f.get("zones"):
        bits.append("zone: " + ", ".join(f["zones"]))
    if not cfg["enabled"]:
        return "Notifiche SOSPESE."
    return ("Filtri: " + "; ".join(bits)) if bits else "Nessun limite su prezzo, superficie e zona."


def main():
    body = os.environ.get("ISSUE_BODY", "")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body, re.S) or re.search(r"(\{.*\})", body, re.S)
    if not m:
        print("Nessun JSON trovato nella richiesta")
        return 1
    cfg = validate(json.loads(m.group(1)))
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
    summary = describe(cfg)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as fh:
        fh.write(summary + "\n")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            creq.post(os.environ.get("NTFY_SERVER", "https://ntfy.sh"), timeout=20, json={
                "topic": topic, "title": "⚙️ Impostazioni notifiche salvate", "message": summary, "tags": ["gear"]})
        except Exception as e:
            print("Conferma ntfy non inviata:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
