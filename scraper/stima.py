"""Stima del "prezzo giusto" di ogni casa, imparata dagli annunci stessi (docs/data/stima.json).

Modello: regressione lineare con penalità (ridge) sul logaritmo del prezzo, a partire da superficie, zona,
tipologia, stato, piano/ascensore, terrazzo/giardino/box/vista mare e classe energetica. Una riga per casa (i doppioni
contano una volta), solo abitazioni con prezzo e superficie.

Onestà della stima: ogni casa del campione è stimata da un modello che NON l'ha vista (5 gruppi a rotazione), e
l'errore tipico che ne risulta è salvato e mostrato in dashboard. Sono prezzi CHIESTI, non prezzi di vendita.
Tutto in Python puro: nessuna libreria in più.
"""
import json
import math
import os
import re
import zlib
from datetime import datetime, timezone

from .zones import foreign_title, norm

FILE = os.path.join(os.environ.get("CASE_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "data"),
                    "stima.json")
TYPES = ("appartamento", "attico", "casa", "villa")
FOLDS = 5
RIDGE = 2.0              # penalità sui coefficienti delle categorie: zone con pochi annunci restano vicine alla media
MIN_TRAIN = 300
PPM_RANGE = (500, 9000)  # €/mq fuori da qui: quasi sempre dati letti male
# annunci il cui prezzo non è quello di una casa libera e intera: fuori dal campione e senza "affare"
SPECIAL = re.compile(r"nuda propriet|usufrutt|\basta\b|aste giudiziar|\bquota\b|porzione di propriet|\blocat[oa]\b|"
                     r"a reddito|messo a reddito|affittat[oa]|contratto di locazione|rendita vitalizia|multiproprieta")
# quanto sotto la stima deve stare una casa per essere un "possibile affare": almeno il 30% e almeno una volta e
# mezza l'errore tipico. Solo appartamenti e attici con stato noto e una zona con abbastanza annunci: case e ville
# hanno superfici poco affidabili (giardino, terreno) e senza stato un prezzo basso vuol dire quasi sempre "da rifare".
DEAL_MIN = 0.30
DEAL_TYPES = ("appartamento", "attico")
DEAL_MIN_AREA = 8
EXTREME = (-0.5, 1.0)    # scarti oltre questi limiti: dati sbagliati, non affari


def _floor_bucket(r: dict) -> str:
    from .parse_utils import floor_level
    if r.get("type") not in ("appartamento", "attico"):
        return "nd"
    f = floor_level(r.get("floor")) if r.get("floor") else None
    if f is None:
        return "nd"
    if f <= 0:
        return "terra"
    lift = (r.get("features") or {}).get("elevator")
    if f >= 3:
        return "alto_senza_asc" if lift is False else "alto"
    return "basso"


def _energy(r: dict) -> str:
    e = (r.get("energy") or "").upper()
    if not e:
        return "nd"
    return "buona" if e[0] in "AB" else "media" if e[0] in "CDE" else "scarsa"


def area_of(r: dict) -> str | None:
    if r.get("town") == "Trieste":
        return "z:" + (r.get("zone") or "Trieste")
    return "t:" + r["town"] if r.get("town") else None


def features(r: dict) -> dict[str, float]:
    """Variabili della casa: nome -> valore. Le categorie mancanti hanno la loro voce "nd" (la riga non si scarta)."""
    feats = r.get("features") or {}
    x = {"log_mq": math.log(r["mq"]), "type:" + r["type"]: 1.0, "area:" + area_of(r): 1.0,
         "cond:" + (r.get("condition") or "nd"): 1.0, "floor:" + _floor_bucket(r): 1.0, "energy:" + _energy(r): 1.0}
    for k in ("terrace", "garden", "garage", "sea_view"):
        if feats.get(k) is True:
            x["f:" + k] = 1.0
    if (r.get("bathrooms") or 0) >= 2:
        x["f:bagni2"] = 1.0
    if r.get("omi"):
        x["omi:" + r["omi"]] = 1.0     # zona OMI: più fine del rione, quando la posizione è precisa
    return x


def is_special(r: dict) -> bool:
    return bool(SPECIAL.search(norm(" ".join(str(r.get(k) or "") for k in ("title", "description")))))


def eligible(r: dict) -> bool:
    """Casa per cui ha senso una stima: abitazione con superficie, non un cantiere con prezzi "da"."""
    return (r.get("type") in TYPES and r.get("mq") and 20 <= r["mq"] <= 1500 and area_of(r)
            and not (r.get("features") or {}).get("_project") and not foreign_title(r.get("title")))


# ---------------------------------------------------------------- algebra minima (sistemi piccoli, ~50 variabili)

def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit(rows: list[tuple[dict, float]]) -> dict:
    """rows: (variabili, log prezzo). Restituisce i coefficienti {nome: peso} con "_": termine costante."""
    names = sorted({k for x, _ in rows for k in x})
    idx = {k: i + 1 for i, k in enumerate(names)}
    n = len(names) + 1
    xtx = [[0.0] * n for _ in range(n)]
    xty = [0.0] * n
    for x, y in rows:
        v = [(0, 1.0)] + [(idx[k], val) for k, val in x.items()]
        for i, vi in v:
            xty[i] += vi * y
            for j, vj in v:
                xtx[i][j] += vi * vj
    for k, i in idx.items():
        if k != "log_mq":
            xtx[i][i] += RIDGE
    beta = _solve(xtx, xty)
    coef = {k: beta[i] for k, i in idx.items()}
    coef["_"] = beta[0]
    return coef


def predict(coef: dict, x: dict) -> float:
    return math.exp(coef["_"] + sum(coef.get(k, 0.0) * v for k, v in x.items()))


# ---------------------------------------------------------------- calcolo per tutto l'archivio

def _groups(listings: dict) -> dict[str, list[dict]]:
    g = {}
    for r in listings.values():
        if r.get("status") == "active":
            g.setdefault(r.get("group", r["id"]), []).append(r)
    return g


def _rep(members: list[dict]) -> dict:
    """Annuncio che rappresenta la casa: quello con più dati (superficie, stato, piano), a parità il prezzo più basso."""
    score = lambda r: (bool(r.get("mq")), bool(r.get("condition")), bool(r.get("floor")), bool(r.get("price")), -(r.get("price") or 0))
    return max(members, key=score)


def compute(listings: dict) -> dict:
    groups = _groups(listings)
    reps = {}
    for gid, members in groups.items():
        r = _rep(members)
        if eligible(r):
            prices = [m["price"] for m in members if m.get("price")]
            reps[gid] = (r, min(prices) if prices else None)
    train = []
    for gid, (r, price) in reps.items():
        if price and PPM_RANGE[0] <= price / r["mq"] <= PPM_RANGE[1] and not is_special(r):
            train.append((gid, features(r), math.log(price)))
    if len(train) < MIN_TRAIN:
        return {}
    fold = lambda gid: zlib.crc32(gid.encode()) % FOLDS
    full = fit([(x, y) for _, x, y in train])
    # un primo giro per togliere i casi assurdi (superficie del terreno presa per quella della casa, ecc.)
    resid = sorted(abs(y - math.log(predict(full, x))) for _, x, y in train)
    cut = max(0.6, 3 * resid[len(resid) // 2])
    train = [(g, x, y) for g, x, y in train if abs(y - math.log(predict(full, x))) <= cut]
    full = fit([(x, y) for _, x, y in train])
    models = [fit([(x, y) for g, x, y in train if fold(g) != k]) for k in range(FOLDS)]
    in_train = {g for g, _, _ in train}
    ratios = sorted(math.exp(y) / predict(models[fold(g)], x) for g, x, y in train)
    errs = sorted(abs(q - 1) for q in ratios)
    mdape = errs[len(errs) // 2]
    # forbice in cui cade il prezzo chiesto di 8 case su 10 rispetto alla loro stima
    band = (ratios[len(ratios) // 10], ratios[len(ratios) * 9 // 10])
    area_n = {}
    for g, x, _ in train:
        a = next(k for k in x if k.startswith("area:"))
        area_n[a] = area_n.get(a, 0) + 1

    est = {}
    for gid, (r, price) in reps.items():
        x = features(r)
        model = models[fold(gid)] if gid in in_train else full
        v = predict(model, x)
        est[gid] = {"v": round(v, -3), "n": area_n.get("area:" + area_of(r), 0), "special": is_special(r),
                    "dealable": r["type"] in DEAL_TYPES and bool(r.get("condition"))}
    return {"n": len(train), "mdape": round(mdape, 3), "band": [round(band[0], 3), round(band[1], 3)],
            "deal": round(max(DEAL_MIN, 1.5 * mdape), 3), "est": est}


def deal_pct(e: dict | None, price: int | None, mdape_deal: float) -> float | None:
    """Scarto del prezzo dalla stima (es. -0.2 = 20% sotto) se la casa è un possibile affare, altrimenti None."""
    if not e or not price or e.get("special") or not e.get("dealable") or e.get("n", 0) < DEAL_MIN_AREA:
        return None
    d = price / e["v"] - 1
    return d if EXTREME[0] < d <= -mdape_deal else None


def save(result: dict, ts: str | None = None):
    if not result:
        return
    out = {"updated": ts or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           "n": result["n"], "mdape": result["mdape"], "band": result["band"], "deal": result["deal"],
           # [valore stimato, annunci della stessa zona nel campione, può essere un affare (0/1), prezzo "speciale"
           # (nuda proprietà, locato...: 0/1)]
           "est": {g: [int(e["v"]), e["n"], int(e["dealable"] and e["n"] >= DEAL_MIN_AREA), int(e["special"])]
                   for g, e in sorted(result["est"].items())}}
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in out.items() if k != "est"))
        f.write(',\n"est": {\n' + ",\n".join(f"{json.dumps(g)}: {json.dumps(v)}" for g, v in out["est"].items()) + "\n}}\n")
    os.replace(tmp, FILE)


def apply(store) -> dict:
    """Calcola le stime, le salva e le appoggia sugli annunci (campo temporaneo `_stima`, non salvato nell'archivio)
    per le notifiche."""
    res = compute(store.listings)
    save(res, store.ts)
    for r in store.listings.values():
        r.pop("_stima", None)
    for gid, members in _groups(store.listings).items():
        e = res.get("est", {}).get(gid)
        if e:
            for m in members:
                m["_stima"] = e
    store.stima = res
    return res
