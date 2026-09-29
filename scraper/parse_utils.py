"""Funzioni per estrarre prezzo, superficie, locali, tipologia, stato dal testo italiano degli annunci."""
import re

from .zones import norm

_NUM = r"(\d{1,3}(?:[.\s']\d{3})+|\d+)(?:,\d+)?"


def to_int(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    m = re.search(_NUM, str(s))
    if not m:
        return None
    return int(re.sub(r"[.\s']", "", m.group(1)))


def parse_price(s) -> int | None:
    """'€ 185.000' -> 185000. 'Trattativa riservata' -> None. Scarta valori non plausibili."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        v = int(s)
    else:
        t = str(s)
        t = re.sub(r"(\d),(\d{3})(?!\d)", r"\1.\2", t)  # "125,000 €" (virgola come separatore migliaia)
        if re.search(r"trattativa|riservat|su richiesta", t, re.I) and not re.search(r"\d{2}", t):
            return None
        m = re.search(r"(?:€|eur(?:o)?)\s*" + _NUM, t, re.I) or re.search(_NUM + r"\s*(?:€|eur)", t, re.I) or re.search(_NUM, t)
        if not m:
            return None
        v = int(re.sub(r"[.\s']", "", m.group(1)))
    return v if 5_000 <= v <= 20_000_000 else None


def parse_mq(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        v = int(s)
    else:
        t = str(s)
        m = (re.search(_NUM + r"\s*(?:mq|m²|m2|m\s2(?!\d)|metri\s*quadr|sqm)", t, re.I)
             or re.search(r"(?:mq|m²|superficie|sup\.)\s*[:.]?\s*" + _NUM, t, re.I))
        if not m:
            m = re.fullmatch(r"\s*" + _NUM + r"\s*", t)
        if not m:
            return None
        v = int(re.sub(r"[.\s']", "", m.group(1)))
    return v if 10 <= v <= 5000 else None


_ROOM_WORDS = {"monolocale": 1, "bilocale": 2, "trilocale": 3, "quadrilocale": 4, "pentalocale": 5, "plurilocale": 5}


def parse_rooms(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        v = int(s)
        return v if 1 <= v <= 20 else None
    t = norm(str(s))
    for w, n in _ROOM_WORDS.items():
        if w in t:
            return n
    m = re.search(r"(\d{1,2})\s*(?:locali|vani|stanze)", t) or re.search(r"(?:locali|vani)\s*[:.]?\s*(\d{1,2})", t)
    if not m:
        m = re.fullmatch(r"\s*(\d{1,2})\+?\s*", t)
    if m:
        v = int(m.group(1))
        return v if 1 <= v <= 20 else None
    return None


def parse_small_count(s, words) -> int | None:
    """Camere/bagni: '2 camere', 'Bagni: 2'."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s) if 0 <= s <= 15 else None
    t = norm(str(s))
    w = "|".join(words)
    m = re.search(r"(\d{1,2})\s*(?:" + w + r")", t) or re.search(r"(?:" + w + r")\s*[:.]?\s*(\d{1,2})", t)
    if not m:
        m = re.fullmatch(r"\s*(\d{1,2})\s*", t)
    return int(m.group(1)) if m and int(m.group(1)) <= 15 else None


TYPES = [
    ("attico", ["attico", "superattico", "penthouse", "mansarda"]),
    ("villa", ["villa ", "villa,", "villetta", "villino", "villa a schiera", "schiera", "bifamiliare", "trifamiliare"]),
    ("casa", ["casa indipendente", "casa singola", "casa semindipendente", "porzione di casa", "casa carsica", "casa di paese",
              "rustico", "casale", "stabile", "palazzina", "intero edificio", "cielo-terra", "terra-cielo", "casa "]),
    ("terreno", ["terreno", "lotto edificabile", "area edificabile"]),
    ("box", ["box auto", "garage", "posto auto", "autorimessa", "posto macchina"]),
    ("commerciale", ["negozio", "ufficio", "locale commerciale", "capannone", "magazzino", "laboratorio", "attivita commerciale",
                     "studio professionale", "albergo", "hotel", "bar ", "ristorante"]),
    ("appartamento", ["appartamento", "monolocale", "bilocale", "trilocale", "quadrilocale", "pentalocale", "plurilocale",
                      "loft", "open space", "duplex", "alloggio"]),
]
TYPE_LABELS = {"appartamento": "Appartamento", "attico": "Attico / Mansarda", "villa": "Villa / Villetta", "casa": "Casa indipendente",
               "terreno": "Terreno", "box": "Box / Posto auto", "commerciale": "Commerciale", "altro": "Altro"}


def detect_type(title: str | None, extra: str | None = None) -> str:
    """Tipologia dal titolo (prioritario) o dal testo aggiuntivo."""
    for blob in (norm(title or "") + " ", norm(extra or "")[:400] + " "):
        if not blob.strip():
            continue
        for key, kws in TYPES:
            if any(k in blob for k in kws):
                return key
    return "altro"


CONDITIONS = [
    ("nuovo", ["nuova costruzione", "nuove costruzioni", "di nuova realizzazione", "in costruzione", "classe a4", "classe a3",
               "mai abitato", "nuovo cantiere", "consegna prevista"]),
    ("da_ristrutturare", ["da ristrutturare", "da rimodernare", "da riattare", "da sistemare", "da ammodernare",
                          "necessita di ristrutturazione", "necessita di lavori", "da rinnovare", "da risanare"]),
    ("ristrutturato", ["ristrutturato", "ristrutturata", "completamente rinnovato", "recentemente ristrutturat", "ottimo stato", "come nuovo"]),
    ("buono", ["buono stato", "buone condizioni", "abitabile", "discreto stato"]),
]
CONDITION_LABELS = {"nuovo": "Nuova costruzione", "ristrutturato": "Ristrutturato / Ottimo", "buono": "Buono / Abitabile",
                    "da_ristrutturare": "Da ristrutturare"}


def detect_condition(*texts) -> str | None:
    blob = norm(" ".join(t for t in texts if t))
    for key, kws in CONDITIONS:
        if any(k in blob for k in kws):
            return key
    return None


def detect_features(*texts) -> dict:
    blob = norm(" ".join(t for t in texts if t))
    def has(pos, neg=()):
        if any(n in blob for n in neg):
            return False
        return True if any(p in blob for p in pos) else None
    return {
        "elevator": has(["ascensore"], ["senza ascensore", "privo di ascensore", "no ascensore", "ascensore: no", "ascensore no"]),
        "garage": has(["garage", "box auto", "posto auto", "autorimessa", "posto macchina"], ["garage: no", "posto auto: no"]),
        "terrace": has(["terrazzo", "terrazza", "poggiolo", "balcone", "loggia"]),
        "garden": has(["giardino", "scoperto", "cortile privato"], ["giardino: no", "giardino condominiale"]),
        "sea_view": has(["vista mare", "vista sul mare", "vista golfo", "vista sul golfo", "fronte mare"]),
    }


def detect_energy(*texts) -> str | None:
    blob = " ".join(t for t in texts if t)
    m = re.search(r"classe\s*(?:energetica)?\s*[:.]?\s*(A4|A3|A2|A1|A\+|A|B|C|D|E|F|G)\b", blob, re.I)
    return m.group(1).upper() if m else None


def detect_sold(*texts) -> bool:
    blob = norm(" ".join(t for t in texts if t))
    return bool(re.search(r"\b(venduto|venduta|compromesso|sotto offerta|trattativa in corso|sold)\b", blob))


def clean_text(s: str | None, limit: int = 1500) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] if s else None
