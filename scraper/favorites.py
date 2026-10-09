"""Salva i preferiti inviati dalla dashboard (issue GitHub "Preferiti ..." aperta dal proprietario) in docs/favorites.json.

I preferiti viaggiano come codici brevi (8 caratteri esadecimali, calcolati dalla dashboard dall'id della casa):
così il link per aprire la issue resta corto anche con molti preferiti.
"""
import json
import os
import re
import sys

from .store import now_iso

FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "favorites.json")


def main():
    body = os.environ.get("ISSUE_BODY", "")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body, re.S)
    if not m:
        print("Nessun elenco trovato nella richiesta")
        return 1
    raw = json.loads(m.group(1))
    codes = sorted({c for c in raw.get("favs") or [] if isinstance(c, str) and re.fullmatch(r"[0-9a-f]{8}", c)})[:1000]
    with open(FILE, "w", encoding="utf-8") as fh:
        json.dump({"updated": now_iso(), "favs": codes}, fh, indent=0)
    print(f"Salvati {len(codes)} preferiti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
