"""Posizione sulla mappa dagli indirizzi (OpenStreetMap Nominatim, max 1 richiesta/s, con cache permanente)
e zona di Trieste dedotta dalla posizione."""
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

from curl_cffi import requests as creq

from .zones import ZONES_TRIESTE, detect_town, detect_zone, norm

CACHE_FILE = os.path.join(os.environ.get("CASE_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"), "geocache.json")
MAX_PER_RUN = 150
MISS_RETRY_DAYS = 60     # indirizzo non trovato: si riprova dopo due mesi (OpenStreetMap migliora)
MAX_HTTP_ERRORS = 3      # Nominatim non risponde: smetti per questo giro e riprova al prossimo
FAKE_POINT_MIN = 6       # tanti annunci della stessa fonte sullo stesso punto/indirizzo: forse la sede dell'agenzia
BOX = (45.55, 13.55, 45.82, 13.95)  # provincia di Trieste
USER_AGENT = "case-trieste-monitor/1.1 (uso personale; https://github.com/matt99flash/case-trieste)"

_STREET_WORDS = ("via", "viale", "v.le", "piazza", "p.zza", "p.za", "piazzale", "largo", "strada", "salita", "androna",
                 "riva", "scala", "passeggio", "galleria", "corso", "vicolo", "calle", "localita", "loc.", "borgo",
                 "frazione", "fraz.", "contrada", "rotonda", "campo", "str.")
STREET = re.compile(r"(?<![\w.])(" + "|".join(re.escape(w) for w in _STREET_WORDS) + r")(?![\w])", re.I)
# separatori dopo i quali il nome della via è finito ("Via Giulia – Contatti", "VIA X | Bilocale", "Via Y, Trieste")
_CUT = re.compile(r"\s[-–—]\s?|[,;|()\[\]:–—\n]| / |(?<!\b[a-z])\.\s|-$|-\s", re.I)
_NUMBER = re.compile(r"^\d{1,4}[a-z]?(?:/\d{1,3}[a-z]?)?$", re.I)
_CONNECT = {"di", "del", "della", "dei", "degli", "delle", "dello", "dell", "d", "de", "e", "al", "alla", "alle", "san",
            "santa", "sant", "s.", "von", "da", "xx", "xxiv", "iv", "i"}
# parole che non fanno parte del nome di una via (fine del nome)
_STOP = {
    "italy", "italia", "ts", "tri", "in", "con", "a", "vicino", "pressi", "zona", "fronte", "angolo", "ang", "nei", "nel", "nella", "sulla", "presso",
    "posto", "posti", "box", "garage", "autorimessa", "appartamento", "bilocale", "trilocale", "monolocale", "quadrilocale",
    "plurilocale", "casa", "villa", "villetta", "attico", "mansarda", "locale", "ufficio", "negozio", "magazzino",
    "vista", "piano", "primo", "secondo", "terzo", "quarto", "ristrutturato", "ristrutturata", "luminoso", "luminosa",
    "ampio", "ampia", "stabile", "palazzo", "edificio", "mq", "m2", "mq.", "locato", "locata", "vendesi", "vendita",
    "cantina", "giardino", "terrazzo", "terrazza", "contatti", "immobiliare", "immobiliari", "agenzia", "rif", "rif.",
    "uso", "investimento", "ultimo", "attico", "tutto", "arredato", "defilato", "defilata", "tranquilla", "tranquillo",
    # una seconda parola "forte" di via chiude il nome ("VIA VITALBA STRADA COSTIERA"); "campo", "scala", "borgo"...
    # invece compaiono dentro i nomi (Via del Campo Marzio, Via di Scala Santa)
    "via", "viale", "piazza", "piazzale", "strada", "salita", "largo",
}
_PLACE = {"trieste", "muggia", "duino", "aurisina", "sgonico", "monrupino", "dorligo"}   # chiudono il nome, ma non lo aprono
_SKIP = {"n", "n.", "nr", "nr.", "n°", "civico"}                                        # "n. 542"
_NO_NUMBER_AFTER = {"mq", "m2", "mq.", "vani", "locali", "camere", "stanze", "anni"}
# "Del Ponticello 10", "Giacomo Puccini 68": via senza la parola "via" (alcuni siti la omettono)
_BARE = re.compile(r"^\s*([a-zà-ù][a-zà-ù'’. ]{2,40}?)\s*,?\s+(\d{1,4}[a-z]?(?:/\d{1,3}[a-z]?)?)\s*$", re.I)
_PAREN = re.compile(r"(?<![\w])([a-zà-ù][a-zà-ù'’ ]{2,40}?)\s*\((via|viale|piazza|largo|salita|strada|androna|riva|corso)\)", re.I)
_OSM = re.compile(r"^\s*(\d{1,4}[a-z]?(?:/\d{1,3}[a-z]?)?)\s*,\s*([^,]+)")
# "Borgo Teresiano", "Borgo San Sergio": nomi di rione, non vie
_RIONI = {norm(k) for kws in ZONES_TRIESTE.values() for k in kws if norm(k).startswith(("borgo ", "rotonda "))}
_DESC_LEAD = re.compile(r"(?:\b(?:in|sito|sita|situato|situata|ubicato|ubicata)\s+)$", re.I)

ZONE_CENTER = {
    "Centro / Borgo Teresiano": (45.6530, 13.7760), "Borgo Giuseppino / Cavana": (45.6470, 13.7690),
    "Città Vecchia / San Giusto": (45.6480, 13.7720), "Borgo Franceschino / Giulia": (45.6515, 13.7850),
    "San Vito / Campi Elisi": (45.6405, 13.7610), "San Giacomo": (45.6390, 13.7780), "Barriera": (45.6450, 13.7820),
    "Chiadino / Rozzol": (45.6440, 13.7960), "San Luigi / Guardiella": (45.6540, 13.8040),
    "Cologna / Scorcola / Gretta": (45.6620, 13.7800), "Roiano": (45.6650, 13.7700),
    "Barcola / Grignano / Miramare": (45.6860, 13.7400), "Carso triestino (Opicina e frazioni)": (45.6950, 13.8000),
    "Servola / Valmaura / Borgo San Sergio": (45.6230, 13.7920), "Montebello / Rotonda del Boschetto": (45.6480, 13.8000),
}


def zone_from_point(lat, lon) -> str:
    # quota: sopra i ~250 m di altitudine si è sul Carso; approssimato con la distanza dalla costa verso nord-est
    best, bd = None, 1e9
    for z, (a, b) in ZONE_CENTER.items():
        d = math.hypot((lat - a) * 111, (lon - b) * 78)
        if d < bd:
            best, bd = z, d
    return best


def _street_after(text: str, start: int, word: str) -> str | None:
    """Nome della via (con eventuale numero civico) che segue la parola `word` trovata in posizione `start`."""
    rest = _CUT.split(text[start:], maxsplit=1)[0]
    tokens = rest.split()
    if not tokens:
        return None
    name, number = [], None
    titlecase = tokens[0][:1].isupper() and not tokens[0].isupper()
    for i, tok in enumerate(tokens):
        low = norm(tok).strip(".'’")
        if _NUMBER.match(tok):
            nxt = norm(tokens[i + 1]).strip(".") if i + 1 < len(tokens) else ""
            if name and nxt not in _NO_NUMBER_AFTER:
                number = tok
            break
        if low in _SKIP and name:
            continue
        if tok.startswith("/") or low in _STOP or (name and low in _PLACE) or not re.search(r"[a-zà-ù]", low):
            break
        # "Via Giulia in stabile d'epoca": in un testo con le maiuscole, una parola minuscola chiude il nome
        if name and titlecase and tok[:1].islower() and low not in _CONNECT and not low.startswith(("d'", "dell", "sant'")):
            break
        name.append(tok)
        if len(name) >= 5:
            break
    if not name or len(" ".join(name)) < 3 or norm(name[-1]) in _CONNECT:
        return None
    if number is None:
        # "Calle dei Lauri, 8": civico dopo la virgola
        m = re.match(r"[^,;|\n]*?,\s*(\d{1,4}[a-z]?(?:/\d{1,3}[a-z]?)?)(?![\w/])(?!\s*(?:mq|m2|vani|locali))", text[start:], re.I)
        if m:
            number = m.group(1)
    w = {"v.le": "viale", "p.zza": "piazza", "p.za": "piazza", "loc.": "localita", "fraz.": "", "str.": "strada"}.get(word.lower(), word)
    return re.sub(r"\s+", " ", f"{w} {' '.join(name)} {number or ''}").strip(" .")


def street_in(text: str | None, *, description: bool = False) -> str | None:
    if not text:
        return None
    text = text.replace(" ", " ")
    if not description:
        m = _OSM.match(text)   # "26, Via Karl Ludwig von Bruck, Campi Elisi, ..., Italia"
        if m and STREET.match(m.group(2).strip()):
            s = street_in(m.group(2))
            return f"{s} {m.group(1)}" if s and not re.search(r"\d$", s) else s
        m = _PAREN.search(text)  # "NAVALI (via)"
        if m and norm(m.group(1)).strip() not in _STOP:
            return f"{m.group(2)} {m.group(1).strip()}"
    for m in STREET.finditer(text):
        if description and not _DESC_LEAD.search(text[max(0, m.start() - 12):m.start()]):
            continue
        s = _street_after(text, m.end(), m.group(1))
        # nel testo libero il nome di una via ha una maiuscola ("in via del Carpineto"; non "in corso di")
        if s and description and not re.search(r"\s[A-ZÀ-Ü]", s):
            continue
        if s and norm(s) not in _RIONI:
            return s
    return None


def street_of(address, title, description, town) -> str | None:
    """Via (e civico, se c'è) dell'immobile, cercata nell'indirizzo, poi nel titolo, poi nella descrizione."""
    s = street_in(address) or street_in(title)
    if not s and address and town == "Trieste":
        m = _BARE.match(address)
        if m and norm(m.group(1)).split()[0] not in _STOP:
            s = f"via {m.group(1).strip()} {m.group(2)}"
    return s or street_in(description, description=True)


class Geocoder:
    def __init__(self):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                self.cache = json.load(f)
        except FileNotFoundError:
            self.cache = {}
        self.done = 0
        self.stats = Counter()
        self.last = 0.0

    def save(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=0, sort_keys=True)

    def _cached(self, key):
        """(trovato_in_cache, valore). Un "non trovato" vecchio o in formato precedente va ritentato."""
        if key not in self.cache:
            return False, None
        v = self.cache[key]
        if isinstance(v, list):
            return True, v
        if isinstance(v, dict) and v.get("miss"):
            if date.fromisoformat(v["miss"]) > date.today() - timedelta(days=MISS_RETRY_DAYS):
                return True, None
        return False, None

    def _search(self, street: str, town: str):
        wait = self.last + 1.1 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        r = creq.get("https://nominatim.openstreetmap.org/search", timeout=20, params={
            "q": f"{street}, {town}, Italia", "format": "json", "limit": 1, "countrycodes": "it",
            "viewbox": f"{BOX[1]},{BOX[2]},{BOX[3]},{BOX[0]}", "bounded": 1},
            headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            raise RuntimeError(r.status_code)
        return r.json()

    def lookup(self, street: str, town: str):
        key = norm(f"{street}|{town}")
        hit, val = self._cached(key)
        if hit:
            return val
        if self.done >= MAX_PER_RUN or self.stats["errore"] >= MAX_HTTP_ERRORS:
            return None
        self.done += 1
        try:
            res = self._search(street, town)
            no_number = re.sub(r"\s\d[\w/]*$", "", street)
            if not res and no_number != street:
                # civico assente su OpenStreetMap: basta la via
                hit, val = self._cached(norm(f"{no_number}|{town}"))
                if hit:
                    self.cache[key] = val if val else {"miss": date.today().isoformat()}
                    return val
                res = self._search(no_number, town)
        except Exception:
            self.stats["errore"] += 1
            return None  # nessuna traccia in cache: riprova al prossimo giro
        val = None
        if res:
            lat, lon = float(res[0]["lat"]), float(res[0]["lon"])
            precise = res[0].get("addresstype") in ("building", "house", "road", "residential", "place", "square", "pedestrian") \
                or res[0].get("class") in ("highway", "building", "place")
            val = [round(lat, 5), round(lon, 5)] if precise else None
        self.stats["trovato" if val else "non trovato"] += 1
        self.cache[key] = val if val else {"miss": date.today().isoformat()}
        return val


def _shared_values(listings, key) -> set:
    """Valori (punti o indirizzi) condivisi da tanti annunci della stessa fonte che parlano di luoghi diversi:
    sono la sede dell'agenzia o il centro città. Un cantiere con tanti appartamenti nello stesso posto resta valido."""
    groups = defaultdict(list)
    for d in listings:
        v = key(d)
        if v:
            groups[v].append(d)
    fake = set()
    for v, ds in groups.items():
        if len(ds) < FAKE_POINT_MIN:
            continue
        places = set()
        for d in ds:
            t = d.get("title")
            place = street_in(t) or detect_zone(t) or detect_town(t)
            if place:
                places.add(norm(re.sub(r"\s\d[\w/]*$", "", place)))
        if len(places) >= 2:
            fake.add(v)
    return fake


def enrich(listings: list[dict], geocoder: Geocoder):
    """Aggiunge lat/lon dagli indirizzi e ricava la zona di Trieste dalla posizione quando possibile.
    `listings` sono gli annunci di UNA fonte appena letti."""
    point = lambda d: (d["lat"], d["lon"]) if d.get("lat") is not None else None
    fake_points = _shared_values(listings, point)
    fake_addresses = _shared_values(listings, lambda d: norm(d.get("address") or "").strip() or None)
    for d in listings:
        if point(d) in fake_points:
            d["lat"] = d["lon"] = d["geo"] = None
            d["_nogeo"] = True
        if d.get("lat") is None and d.get("town"):
            address = None if norm(d.get("address") or "").strip() in fake_addresses else d.get("address")
            street = street_of(address, d.get("title"), d.get("description"), d["town"])
            ll = geocoder.lookup(street, d["town"]) if street else None
            if ll:
                d["lat"], d["lon"] = ll
                d["geo"] = "indirizzo"
                d.pop("_nogeo", None)
            elif address is None and d.get("address"):
                d["_nogeo"] = True   # posizione ricavata in passato dall'indirizzo dell'agenzia: da togliere
        elif d.get("lat") is not None and not d.get("geo"):
            d["geo"] = "fonte"
        if d.get("town") == "Trieste" and d.get("lat") is not None:
            if BOX[0] <= d["lat"] <= BOX[2] and BOX[1] <= d["lon"] <= BOX[3]:
                d["zone"] = zone_from_point(d["lat"], d["lon"])
