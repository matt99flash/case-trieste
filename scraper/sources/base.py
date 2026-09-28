"""Base per gli adattatori delle fonti + estrattori generici dalle pagine dettaglio."""
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import parse_utils as pu
from ..models import Listing
from ..store import LISTING_FIELDS


class Context:
    def __init__(self, http, store, log):
        self.http = http
        self.store = store
        self.log = log
        self.detail_ids: set[str] = set()
        self.errors: list[str] = []

    def merge_known(self, lst: Listing) -> Listing:
        """Completa un annuncio letto solo dall'elenco con i dettagli già in archivio."""
        rec = self.store.known(lst.id)
        if rec:
            for k in LISTING_FIELDS:
                if getattr(lst, k, None) in (None, [], {}, "", "altro") and rec.get(k) not in (None, [], {}, ""):
                    setattr(lst, k, rec[k])
        return lst


class Source:
    """Ogni fonte restituisce l'elenco COMPLETO degli annunci di vendita attivi in provincia di Trieste."""
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.id = cfg["id"]
        self.name = cfg["name"]
        self.default_town = cfg.get("town")
        self.max_details = cfg.get("max_details", 400)   # limite di pagine dettaglio per giro

    def fetch(self, ctx: Context) -> list[Listing]:
        raise NotImplementedError


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def abs_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    return urljoin(base, href).split("#")[0]


def ref_from_url(url: str) -> str:
    """Codice annuncio ricavato dall'URL: l'ultimo numero lungo, altrimenti il percorso."""
    path = urlparse(url).path.rstrip("/")
    nums = re.findall(r"\d{3,}", path)
    if nums:
        return nums[-1]
    q = urlparse(url).query
    nums = re.findall(r"\d{3,}", q)
    return nums[-1] if nums else path.split("/")[-1][:80] or url


# ---------------------------------------------------------------- JSON-LD / meta

def jsonld_objects(soup: BeautifulSoup) -> list[dict]:
    out = []
    for tag in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                out.append(x)
                if "@graph" in x:
                    stack.append(x["@graph"])
    return out


def meta(soup, *names) -> str | None:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


LABELS = {
    "price": r"prezzo|prezzo richiesto|richiesta|costo",
    "mq": r"superficie(?: commerciale| totale| calpestabile)?|mq|metri quadri|metratura|dimensione|m²",
    "rooms": r"locali|n\.? ?locali|numero locali|vani|n\.? ?vani",
    "bedrooms": r"camere(?: da letto)?|n\.? ?camere|camere letto",
    "bathrooms": r"bagni|n\.? ?bagni|servizi(?: igienici)?",
    "floor": r"piano",
    "ref": r"rif\.?|riferimento|codice(?: annuncio| immobile)?|cod\.?|id annuncio|ref\.?",
    "zone": r"zona|quartiere|località|localita|rione",
    "address": r"indirizzo|via",
    "town": r"comune|città|citta",
    "energy": r"classe energetica|classe|ape|certificazione energetica",
    "condition": r"stato(?: immobile| manutenzione| conservazione)?|condizioni",
    "type": r"tipologia|categoria|tipo(?: immobile)?",
    "elevator": r"ascensore",
    "garage": r"box|garage|posto auto|box auto",
    "contract": r"contratto|tipo contratto",
}
_LABEL_RE = {k: re.compile(r"^\s*(?:" + v + r")\s*[:.]?\s*$", re.I) for k, v in LABELS.items()}
_INLINE_RE = {k: re.compile(r"^\s*(?:" + v + r")\s*[:]\s*(.{1,80})$", re.I) for k, v in LABELS.items()}


def kv_pairs(soup: BeautifulSoup) -> dict:
    """Coppie 'etichetta → valore' tipiche delle schede immobile (Superficie: 85 mq, Locali: 3, ...)."""
    out = {}
    # tabelle e liste di definizioni
    for dt in soup.find_all(["dt", "th"]):
        dd = dt.find_next_sibling(["dd", "td"])
        if dd:
            _put(out, dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))
    # righe di testo: "Etichetta" seguita dal valore, oppure "Etichetta: valore"
    lines = [l.strip() for l in soup.get_text("\n").split("\n")]
    lines = [l for l in lines if l]
    for i, line in enumerate(lines):
        if len(line) > 90:
            continue
        for k, rx in _INLINE_RE.items():
            m = rx.match(line)
            if m and k not in out:
                out[k] = m.group(1).strip()
        if i + 1 < len(lines):
            _put(out, line, lines[i + 1])
    return out


def _put(out, label, value):
    if not value or len(value) > 80:
        return
    for k, rx in _LABEL_RE.items():
        if k not in out and rx.match(label):
            out[k] = value
            return


def _yes(v):
    if v is None:
        return None
    v = v.strip().lower()
    if v in ("sì", "si", "yes", "presente", "1", "x", "✓") or v.startswith(("sì", "si ")):
        return True
    if v in ("no", "assente", "0", "-", "non presente"):
        return False
    return True if re.search(r"\d", v) else None


def parse_detail(html: str, url: str, source: str, base: Listing | None = None) -> Listing:
    """Estrattore generico da pagina dettaglio: JSON-LD, meta tag, coppie etichetta/valore, testo."""
    soup = soup_of(html)
    L = base or Listing(source=source, ref=ref_from_url(url), url=url)
    kv = kv_pairs(soup)
    for obj in jsonld_objects(soup):
        t = obj.get("@type")
        t = " ".join(t) if isinstance(t, list) else str(t or "")
        if not re.search(r"Residence|Apartment|House|Product|Offer|RealEstate|Accommodation|SingleFamily|Place", t):
            continue
        L.title = L.title or obj.get("name")
        L.description = L.description or obj.get("description")
        offers = obj.get("offers")
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict) and not L.price:
            L.price = pu.parse_price(offers.get("price") or offers.get("lowPrice"))
        if not L.price and obj.get("price"):
            L.price = pu.parse_price(obj.get("price"))
        fs = obj.get("floorSize")
        if isinstance(fs, dict) and not L.mq:
            L.mq = pu.parse_mq(fs.get("value"))
        L.rooms = L.rooms or pu.parse_rooms(obj.get("numberOfRooms"))
        L.bathrooms = L.bathrooms or pu.parse_small_count(obj.get("numberOfBathroomsTotal"), ["bagni"])
        L.bedrooms = L.bedrooms or pu.parse_small_count(obj.get("numberOfBedrooms"), ["camere"])
        geo = obj.get("geo")
        if isinstance(geo, dict) and not L.lat:
            try:
                L.lat, L.lon = float(geo.get("latitude")), float(geo.get("longitude"))
            except (TypeError, ValueError):
                pass
        adr = obj.get("address")
        if isinstance(adr, dict) and not L.address:
            L.address = ", ".join(x for x in (adr.get("streetAddress"), adr.get("addressLocality")) if x) or None
        img = obj.get("image")
        if img:
            imgs = img if isinstance(img, list) else [img]
            L.images += [i.get("url") if isinstance(i, dict) else i for i in imgs]
        if obj.get("sku") and not base:
            L.ref = str(obj["sku"])

    h1 = soup.find("h1")
    L.title = L.title or (h1.get_text(" ", strip=True) if h1 else None) or meta(soup, "og:title") or (soup.title.string if soup.title else None)
    L.description = L.description or _longest_description(soup) or meta(soup, "og:description", "description")
    if not L.price:
        L.price = pu.parse_price(kv.get("price")) or _price_from_page(soup)
    L.mq = L.mq or pu.parse_mq(kv.get("mq")) or pu.parse_mq(kv.get("mq", "") + " mq")
    L.rooms = L.rooms or pu.parse_rooms(kv.get("rooms"))
    L.bedrooms = L.bedrooms or pu.parse_small_count(kv.get("bedrooms"), ["camere"])
    L.bathrooms = L.bathrooms or pu.parse_small_count(kv.get("bathrooms"), ["bagni"])
    L.floor = L.floor or kv.get("floor")
    L.energy = L.energy or (re.search(r"\b(A4|A3|A2|A1|A\+|[A-G])\b", kv.get("energy", "")) or [None, None])[1]
    L.address = L.address or kv.get("address")
    L.zone = L.zone or kv.get("zone")
    if kv.get("town") and not L.town:
        from ..zones import detect_town
        L.town = detect_town(kv["town"])
    if kv.get("condition") and not L.condition:
        L.condition = pu.detect_condition(kv["condition"]) or (
            "da_ristrutturare" if "ristruttur" in kv["condition"].lower() and "da" in kv["condition"].lower() else None)
    if kv.get("type") and (not L.type or L.type == "altro"):
        L.type = pu.detect_type(kv["type"] + " ", None)
    if kv.get("elevator"):
        L.features["elevator"] = _yes(kv["elevator"])
    if kv.get("garage"):
        L.features["garage"] = _yes(kv["garage"])
    if kv.get("contract") and re.search(r"affitt|locazion", kv["contract"], re.I):
        L.features["_rent"] = True
    if not L.images:
        og = meta(soup, "og:image")
        if og:
            L.images.append(abs_url(url, og))
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
            src = abs_url(url, src)
            if src and re.search(r"\.(jpe?g|webp)(\?|$)", src, re.I) and not re.search(r"logo|icon|banner|sprite|placeholder", src, re.I):
                L.images.append(src)
            if len(L.images) >= 12:
                break
    return L


def _longest_description(soup) -> str | None:
    best = None
    for tag in soup.find_all(["p", "div", "section"]):
        if tag.find(["p", "div", "section", "ul", "table"]):
            continue
        t = tag.get_text(" ", strip=True)
        if 150 <= len(t) <= 6000 and (best is None or len(t) > len(best)):
            if not re.search(r"cookie|privacy|copyright|p\.? ?iva", t[:300], re.I):
                best = t
    return best


def _price_from_page(soup) -> int | None:
    for sel in ['[itemprop="price"]', ".price", ".prezzo", '[class*="price"]', '[class*="prezzo"]']:
        for tag in soup.select(sel)[:5]:
            v = pu.parse_price(tag.get("content") or tag.get_text(" ", strip=True))
            if v:
                return v
    m = re.search(r"€\s*(\d{1,3}(?:\.\d{3})+)", soup.get_text(" "))
    return pu.parse_price(m.group(0)) if m else None
