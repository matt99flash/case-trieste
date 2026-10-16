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


# Locali e piano cercati nella DESCRIZIONE (solo appartamenti/attici: in una villa "al primo piano la zona notte"
# non è il piano dell'immobile). Si prende il valore solo se il testo ne cita uno solo, e mai se è un'ipotesi
# ("possibilità di ricavare un trilocale").
_ROOMS_TXT = re.compile(r"(?<![a-z])(monolocale|bilocale|trilocale|quadrilocale|pentalocale)(?![a-z])"
                        r"|(?<![\d,.])(\d{1,2})\s*(?:locali|vani)(?![a-z])(?!\s+(?:catastal|commercial|access|tecnic|di servizio))")
_HYPOTHESIS = re.compile(r"ricava|trasformabil|possibilit|ottener|frazionab|adattabil|oppure|ipotes|progett|realizzar|cerco|cerchiamo")


def rooms_from_text(text: str | None, mq: int | None = None) -> int | None:
    if not text:
        return None
    t = norm(text)
    found = set()
    for m in _ROOMS_TXT.finditer(t):
        if _HYPOTHESIS.search(t[max(0, m.start() - 40):m.start()]):
            continue
        found.add(_ROOM_WORDS[m.group(1)] if m.group(1) else int(m.group(2)))
    if len(found) != 1:
        return None
    v = found.pop()
    if not 1 <= v <= 8 or (mq and not 12 <= mq / v <= 70):
        return None
    return v


_NUM_WORDS = {"una": 1, "un": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5}
_BEDROOMS_TXT = re.compile(r"(?<![a-z])(bi|tri|quadri)camere(?![a-z])"
                           r"|(?<![a-z\d])(una|due|tre|quattro|cinque|[1-5])\s+(?:(?:ampie|ampia|grandi|grande|spaziose|spaziosa|comode|comoda|luminose|luminosa|belle)\s+)?"
                           r"(?:camer[ae]|stanz[ae] da letto)(?![a-z])")
# "... e una singola", "più una cameretta": il numero letto non è il totale delle camere
_EXTRA_ROOM = re.compile(r"[^.;]{0,30}?(?<![a-z])(?:e|piu|oltre a)\s+(?:una\s+|un'\s*)?(?:(?:camera|stanza)\s+)?(?:singola|cameretta|studio)")


def bedrooms_from_text(text: str | None) -> int | None:
    """'due camere', 'tre ampie camere da letto', 'bicamere' -> 2, 3, 2. None se il testo dà numeri diversi
    ('due camere matrimoniali e una singola') o se è un'ipotesi ('possibilità di ricavare una terza camera')."""
    if not text:
        return None
    t = norm(text)
    found = set()
    for m in _BEDROOMS_TXT.finditer(t):
        if _HYPOTHESIS.search(t[max(0, m.start() - 40):m.start()]):
            continue
        if _EXTRA_ROOM.match(t, m.end()):
            return None
        if m.group(1):
            found.add({"bi": 2, "tri": 3, "quadri": 4}[m.group(1)])
        else:
            found.add(_NUM_WORDS.get(m.group(2)) or int(m.group(2)))
    return found.pop() if len(found) == 1 else None


_FLOOR_WORD = r"(terra|rialzato|primo|secondo|terzo|quarto|quinto|sesto|settimo|ottavo|\d{1,2}\s*[°ºo]?)"
_FLOOR_TXT = re.compile(r"(?<![a-z])(?:al|sito al|posto al|situato al|ubicato al)\s+(?:" + _FLOOR_WORD + r"\s+piano|piano\s+" + _FLOOR_WORD + r")(?![a-z\d])")
# il piano citato riguarda un'altra parte dell'immobile (cantina, garage...) o la casa è su più piani
_FLOOR_OTHER = re.compile(r"cantin|soffitt|garage|box|posto auto|posti auto|lavanderi|deposit|magazzin|"
                          r"zona notte|zona giorno|taverna|portineria|androne|negozi|local[ei] commercial")
_MULTI_LEVEL = re.compile(r"due livelli|piu livelli|tre livelli|duplex|bilivello|su due piani|su tre piani|su piu piani|scala interna")


def floor_from_text(text: str | None) -> str | None:
    """'situato al secondo piano' -> '2'; 'al piano terra' -> 'terra'; None se ambiguo."""
    if not text:
        return None
    t = norm(text)
    if _MULTI_LEVEL.search(t):
        return None
    found = set()
    for m in _FLOOR_TXT.finditer(t):
        # stessa frase, poche parole prima e dopo: "cantina al piano terra", "al piano terra il garage"
        before = re.split(r"[.;!?]", t[max(0, m.start() - 30):m.start()])[-1]
        after = re.split(r"[.;!?]", t[m.end():m.end() + 35])[0]
        if _FLOOR_OTHER.search(before + " " + after):
            continue
        w = m.group(1) or m.group(2)
        lvl = w if w in ("terra", "rialzato") else floor_level(re.sub(r"(\d)\s*[°ºo]$", r"\1", w))
        if lvl is not None:
            found.add(str(lvl))
    if len(found) != 1:
        return None
    v = found.pop()
    return v if v in ("terra", "rialzato") or 0 < int(v) <= 15 else None


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
    ("commerciale", ["negozio", "negozi ", "ufficio", "uffici ", "locale commerciale", "locale senza canna", "capannone", "magazzino",
                     "laboratorio", "attivita", "cessione", "licenza", "studio professionale", "albergo", "hotel", "bar ",
                     "ristorante", "ristorazione", "locale ", "b&b", "bed and breakfast", "affittacamere", "profumeria", "pizzeria"]),
    ("box", ["box auto", "garage", "posto auto", "posti auto", "autorimessa", "posto macchina", "posti moto", "cantina ", "cantine"]),
    ("terreno", ["terreno", "terreni", "lotto edificabile", "area edificabile", "fondo agricolo"]),
    ("attico", ["attico", "superattico", "penthouse", "mansard"]),
    ("villa", ["villa ", "villa,", "ville ", "villetta", "villino", "villa a schiera", "schiera", "bifamiliare", "trifamiliare"]),
    ("casa", ["casa indipendente", "casa singola", "casa semindipendente", "porzione di casa", "casa carsica", "casa di paese",
              "rustico", "casale", "stabile", "palazzina", "intero edificio", "cielo-terra", "terra-cielo", "casetta", "casa "]),
    ("appartamento", ["appartamento", "appartamenti", "monolocale", "bilocale", "trilocale", "quadrilocale", "pentalocale",
                      "plurilocale", "loft", "open space", "duplex", "bilivello", "alloggio", "unita abitativ", "nuda proprieta",
                      "ultimo piano", "piano alto", "residenza"]),
]
TYPE_LABELS = {"appartamento": "Appartamento", "attico": "Attico / Mansarda", "villa": "Villa / Villetta", "casa": "Casa indipendente",
               "terreno": "Terreno", "box": "Box / Posto auto", "commerciale": "Commerciale", "altro": "Altro"}


def detect_type(title: str | None, extra: str | None = None) -> str:
    """Tipologia dal titolo (prioritario) o dal testo aggiuntivo.
    Nel titolo vince la parola chiave che compare PRIMA ("Villa con garage" → villa, "Garage in palazzina" → box).
    Nella descrizione si privilegiano le abitazioni (citano spesso garage, cantine, uffici vicini...)."""
    t = " " + norm(title or "") + " "
    if t.strip():
        best = None
        for key, kws in TYPES:
            for k in kws:
                i = t.find(k)
                if i >= 0 and (best is None or i < best[0]):
                    best = (i, key)
        if best:
            return best[1]
    d = " " + norm(extra or "")[:400] + " "
    order = ["attico", "villa", "casa", "appartamento", "commerciale", "terreno", "box"]
    kw = dict(TYPES)
    for key in order:
        words = [k for k in kw[key] if k != "casa "]   # "la casa è..." non vuol dire casa indipendente
        if any(k in d for k in words):
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


_ORDINALS = {"primo": 1, "secondo": 2, "terzo": 3, "quarto": 4, "quinto": 5, "sesto": 6, "settimo": 7, "ottavo": 8,
             "nono": 9, "decimo": 10}


def floor_level(s) -> int | None:
    """'2°', 'secondo', '2 piano', 'P2' -> 2; 'piano terra', 'T', 'rialzato' -> 0; 'seminterrato' -> -1.
    None se il testo non indica un piano (es. 'su più livelli', 'Autonomo')."""
    t = norm(str(s or "")).strip(" :.")
    if not t:
        return None
    if re.search(r"seminterrat|interrat|sotto ?strada", t):
        return -1
    if re.search(r"\bterra\b|^t$|^pt$|^p\.?t\.?$|rialzat|ammezzat|piano strada", t):
        return 0
    m = re.search(r"(?<![\d/])(-?\d{1,2})(?![\d/])", t)
    if m and abs(int(m.group(1))) <= 30:
        return int(m.group(1))
    for w, n in _ORDINALS.items():
        if re.search(r"\b" + w + r"\b", t):
            return n
    return None
