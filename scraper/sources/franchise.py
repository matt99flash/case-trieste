"""Reti in franchising / agenzie nazionali con uffici in provincia di Trieste.

Tecnocasa, RE/MAX, Gabetti, Engel & Völkers, L'Immobiliare.com, Dove.it.
Ogni adattatore legge l'elenco dal canale più pulito disponibile (API JSON o dati incorporati nella pagina)
e scarica la scheda solo per gli annunci nuovi o cambiati (ctx.store.needs_detail).
"""
import html as htmlmod
import json
import re

from .. import parse_utils as pu
from ..models import Listing
from ..zones import detect_town, detect_zone
from . import register
from .base import Source, soup_of

ENERGY_OK = {"A4", "A3", "A2", "A1", "A+", "A", "B", "C", "D", "E", "F", "G"}
# tipologie non residenziali da scartare (sono sezioni "vendita" che mescolano box, negozi, terreni)
NON_RES = re.compile(r"box|garage|post[oi][ -]?(?:auto|moto|barca)|autorimess|terren|negozi|uffic|capannon|magazzin|laborator|cantin[ae]\b|"
                     r"commercial|ristorant|albergh|hotel|attivit", re.I)


# ---------------------------------------------------------------- utilità comuni

def _json(r):
    return json.loads(r.content.decode("utf-8"))


def _text(h: str | None) -> str | None:
    """HTML -> testo semplice."""
    if not h:
        return None
    if "<" in h:
        h = soup_of(h).get_text(" ")
    return re.sub(r"\s+", " ", htmlmod.unescape(h)).strip() or None


def _energy(v) -> str | None:
    v = (str(v or "")).strip().upper().replace("CLASSE", "").strip()
    return v if v in ENERGY_OK else None


def _town(name: str | None) -> str | None:
    """Comune dell'annuncio: nome della provincia se riconosciuto, altrimenti il nome grezzo
    (così un annuncio fuori provincia viene scartato invece di finire su Trieste per default)."""
    if not name:
        return None
    name = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", name.strip())
    # frazioni del Comune di Trieste (Santa Croce, Opicina, Barcola...) riconosciute come rioni
    return detect_town(name) or ("Trieste" if detect_zone(name) else None) or name.strip() or None


def _condition(v: str | None) -> str | None:
    v = (v or "").lower()
    if not v:
        return None
    if "da ristruttur" in v or "needsrenovation" in v or "renovation" in v:
        return "da_ristrutturare"
    if "nuov" in v or "new" in v:
        return "nuovo"
    if "ristruttur" in v or "ottim" in v or "top" in v or "mint" in v or "refurbished" in v:
        return "ristrutturato"
    if "buon" in v or "abitabil" in v or "good" in v:
        return "buono"
    return None


def _yes(v) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    s = str(v).strip().lower()
    if s.startswith(("s", "y", "true")):
        return True
    if s.startswith(("n", "false", "0")):
        return False
    return None


def _feats(**kw) -> dict:
    return {k: v for k, v in kw.items() if v is not None}


def _geo(L: Listing, lat, lon) -> None:
    """Posizione fornita dalla fonte, solo se plausibile (area di Trieste e dintorni)."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return
    if 45.4 <= lat <= 46.0 and 13.3 <= lon <= 14.1:
        L.lat, L.lon, L.geo = lat, lon, "fonte"


def _known(ctx, L: Listing) -> Listing:
    """Annuncio letto solo dall'elenco: completa con i dati della scheda già in archivio, senza farli
    sovrascrivere dalle versioni ridotte dell'elenco (1 foto, descrizione troncata, indirizzo senza civico)."""
    ctx.merge_known(L)
    rec = ctx.store.known(L.id)
    if not rec:
        return L
    if len(rec.get("images") or []) > len(L.images or []):
        L.images = list(rec["images"])
    if rec.get("description") and len(rec["description"]) > len(L.description or ""):
        L.description = rec["description"]
    if rec.get("address") and L.address and rec["address"] != L.address and rec["address"].startswith(L.address):
        L.address = rec["address"]
    if isinstance(rec.get("features"), dict):
        L.features = {**rec["features"], **(L.features or {})}
    return L


def _next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("__NEXT_DATA__ non trovato")
    return json.loads(m.group(1))


# ---------------------------------------------------------------- Tecnocasa

TECNOCASA_OFFICES = {
    "tscs4": "Tecnocasa Trieste Baiamonti", "tscc4": "Tecnocasa Trieste Centro",
    "tsce4": "Tecnocasa Trieste Chiarbola - Campanelle", "tscn7": "Tecnocasa Trieste Roiano",
    "tscc7": "Tecnocasa Trieste San Giacomo", "tscn6": "Tecnocasa Trieste San Giovanni",
    "tscc2": "Tecnocasa Trieste Barriera - San Luigi", "tscc9": "Tecnocasa Trieste San Vito",
    "tscc8": "Tecnocasa Trieste Settefontane", "tsce1": "Tecnocasa Trieste Università",
    "tsce5": "Tecnocasa Trieste Zona Est", "tshe2": "Tecnocasa Muggia", "tsce6": "Tecnocasa San Dorligo della Valle",
}


@register("tecnocasa")
class TecnocasaSource(Source):
    API = "https://www.tecnocasa.it/api/estates/search"
    MAP = "https://www.tecnocasa.it/api/estates/search-map-list"
    HEADERS = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}

    def fetch(self, ctx):
        base = {"province": "TS", "region": "fvg", "contract": "acquis", "sector": "res", "section": "estate"}
        estates, page, total_pages = [], 1, 1
        while page <= min(total_pages, 40):
            j = _json(ctx.http.get(self.API, params={**base, "page": page}, headers=self.HEADERS))
            if "estates" not in j:
                raise ValueError("risposta Tecnocasa senza 'estates'")
            pag = j.get("pagination") or {}
            total_pages = int(pag.get("total_pages") or 1)
            estates += j["estates"]
            if not j["estates"]:
                break
            page += 1
        ctx.log(f"{len(estates)} annunci nell'elenco (dichiarati {(pag or {}).get('total_items')})")
        coords = {}
        try:
            m = _json(ctx.http.get(self.MAP, params=base, headers=self.HEADERS))
            for f in (m.get("collection") or {}).get("features", []):
                c = (f.get("geometry") or {}).get("coordinates") or []
                if len(c) == 2:
                    coords[str(f.get("id"))] = (float(c[1]), float(c[0]))
        except Exception as e:
            ctx.errors.append(f"mappa: {e}")

        out, details = [], 0
        for e in estates:
            try:
                L = self._parse(e, coords)
            except Exception as ex:
                ctx.errors.append(f"{e.get('id')}: {ex}")
                continue
            if L is None:
                continue
            if details < self.max_details and ctx.store.needs_detail(L.id, L.price):
                details += 1
                try:
                    self._detail(ctx, L)
                    ctx.detail_ids.add(L.id)
                except Exception as ex:
                    ctx.errors.append(f"{L.url}: {ex}")
                    _known(ctx, L)
            else:
                _known(ctx, L)
            out.append(L)
        return out

    def _parse(self, e, coords):
        url = e.get("detail_url") or ""
        seg = url.split("/")[4] if url.count("/") > 5 else ""
        title = e.get("title") or ""
        if NON_RES.search(seg) or NON_RES.search(title):
            return None
        sub = e.get("subtitle") or ""
        town, _, rest = sub.partition(",")
        addr, _, zone = rest.strip().partition(" - ")
        L = Listing(source=self.id, ref=str(e["id"]), url=url, title=title,
                    price=pu.parse_price(e.get("price")) if not e.get("price_alternative") else None,
                    mq=pu.parse_mq(e.get("surface")), rooms=pu.parse_rooms(e.get("rooms")),
                    bathrooms=pu.parse_small_count(e.get("bathrooms"), ["bagni", "bagno"]),
                    town=_town(town), address=addr.strip() or None, zone=zone.strip() or None)
        if L.title and L.address:
            L.title = f"{L.title} – {L.address}"
        L.type = pu.detect_type(title)
        if L.type == "altro":
            L.type = ("attico" if re.search(r"attic|mansard", seg) else "villa" if "vill" in seg
                      else "casa" if re.search(r"cas[ae]|rustic|casal|stabil", seg) else "appartamento" if "appart" in seg else "altro")
        code = ((e.get("agency") or {}).get("id") or "").lower()
        L.agency = TECNOCASA_OFFICES.get(code) or (f"Tecnocasa ({code})" if code else None)
        L.images = [(i.get("url") or {}).get("gallery") or (i.get("url") or {}).get("detail") for i in e.get("images") or []][:12]
        if L.ref in coords:
            _geo(L, *coords[L.ref])
        return L

    def _detail(self, ctx, L):
        page = ctx.http.text(L.url)
        m = re.search(r':estate="([^"]*)"', page)
        if not m:
            raise ValueError("dati scheda non trovati")
        d = json.loads(htmlmod.unescape(m.group(1)))
        L.description = _text(d.get("description"))
        if d.get("latitude") and d.get("longitude"):
            _geo(L, d["latitude"], d["longitude"])
        data = {x.get("label"): x.get("valore") for x in d.get("data") or [] if isinstance(x, dict)}
        addr = data.get("Indirizzo") or d.get("address") or L.address
        L.address = re.sub(r",\s*(?:-1|0)\s*$", "", addr).strip() if addr else None   # civico mancante = "-1"
        imgs = [(i.get("url") or {}).get("gallery") for i in ((d.get("media") or {}).get("images") or [])]
        if len([i for i in imgs if i]) > len(L.images):
            L.images = [i for i in imgs if i][:12]
        if (d.get("district") or {}).get("title") and not L.zone:
            L.zone = d["district"]["title"]
        f = d.get("features") or {}
        L.floor = f.get("floor") or None
        L.bedrooms = pu.parse_small_count(f.get("bedrooms") or data.get("Camere da letto"), ["camere"])
        L.bathrooms = L.bathrooms or pu.parse_small_count(data.get("Bagni"), ["bagni"])
        L.mq = L.mq or pu.parse_mq(d.get("numeric_surface"))
        L.price = L.price or pu.parse_price(d.get("numeric_price"))
        en = d.get("energy_data") or {}
        L.energy = _energy(en.get("class"))
        L.condition = _condition(en.get("building_state"))
        garage = None
        if f.get("box") is not None or f.get("car_places") is not None:
            garage = (f.get("box") or 0) + (f.get("car_places") or 0) > 0
        terrace = None
        if f.get("terraces") is not None or f.get("balconies") is not None:
            terrace = (f.get("terraces") or 0) + (f.get("balconies") or 0) > 0
        L.features = _feats(elevator=_yes(f.get("elevator_immo")), garage=garage, terrace=terrace,
                            garden=_yes(f.get("garden")))
        ag = d.get("agency") or {}
        code = (ag.get("id") or "").lower()
        if code not in TECNOCASA_OFFICES and ag.get("district"):
            L.agency = f"Tecnocasa {ag['district']}"


# ---------------------------------------------------------------- RE/MAX
# cms.remax.it (vecchia API JSON) non risponde più (connessione rifiutata/timeout, anche da rete diversa):
# i dati sono ora incorporati (React Server Components / Next.js "Flight") nelle pagine pubbliche
# https://www.remax.it/vendita-case/<comune>, una ricerca per ciascun comune della provincia (non esiste
# più una ricerca provinciale unica). Quando un comune non ha annunci propri, il sito mostra comunque una
# manciata di "immobili simili" presi altrove: li scartiamo confrontando il campo 'city' dell'annuncio col
# comune cercato, invece di fidarci dell'URL della pagina.

REMAX_TOWNS = {
    "Trieste": "trieste",
    "Muggia": "muggia",
    "Duino-Aurisina": "duino-aurisina",
    "Sgonico": "sgonico",
    "Monrupino": "monrupino",
    "San Dorligo della Valle": "san-dorligo-della-valle-dolina",
}
REMAX_TYPE_LABELS = {
    "appartamento": "Appartamento", "attico_mansarda": "Attico", "nuove_costruzioni": "Nuova costruzione",
    "casa_semindipendente": "Casa semindipendente", "villa": "Villa", "villa_a_schiera": "Villa a schiera",
    "loft": "Loft", "casale": "Casale", "rustico": "Rustico",
}


def _remax_push_payloads(html: str) -> list[str]:
    """Argomenti (JSON-string, quindi con virgolette interne escapate) dei self.__next_f.push([1,"...")
    con cui Next.js invia la pagina a pezzi (React Flight)."""
    out, marker, pos = [], 'self.__next_f.push([1,"', 0
    while True:
        idx = html.find(marker, pos)
        if idx == -1:
            break
        start = idx + len(marker)
        i = start
        while i < len(html):
            c = html[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                break
            i += 1
        try:
            out.append(json.loads('"' + html[start:i] + '"'))
        except Exception:
            pass
        pos = i
    return out


def _remax_flight_chunks(html: str) -> dict:
    """I pezzi React Flight (`self.__next_f.push`) sono testo concatenato a righe 'id:payload'; un payload
    'T<len_hex>,' è seguito da esattamente <len> BYTE (UTF-8) grezzi (così una stringa lunga, es. la
    descrizione, può proseguire nel pezzo successivo senza essere ri-escapata): operiamo sui byte, non sui
    caratteri Python, perché le lettere accentate occupano più di un byte e sfaserebbero il conteggio."""
    buf = "".join(_remax_push_payloads(html)).encode("utf-8")
    chunks, i, n = {}, 0, len(buf)
    while i < n:
        m = re.match(rb"([0-9a-fA-F]+):", buf[i:])
        if not m:
            i += 1
            continue
        cid, j = m.group(1).decode(), i + m.end()
        if j < n and buf[j:j + 1] == b"T":
            m2 = re.match(rb"T([0-9a-fA-F]+),", buf[j:])
            if not m2:
                i = j
                continue
            length = int(m2.group(1), 16)
            start = j + m2.end()
            chunks[cid] = buf[start:start + length].decode("utf-8", errors="replace")
            i = start + length
        else:
            nl = buf.find(b"\n", j)
            if nl == -1:
                nl = n
            chunks[cid] = buf[j:nl].decode("utf-8", errors="replace")
            i = nl + 1
    return chunks


def _remax_resolve(value, chunks: dict):
    """Un valore tipo '$1a' è un riferimento a un pezzo caricato altrove (usato per i testi lunghi, es.
    la descrizione, mandati in streaming separatamente)."""
    if isinstance(value, str) and re.match(r"^\$[0-9a-fA-F]+$", value):
        raw = chunks.get(value[1:])
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return value


def _remax_properties(html: str) -> list[dict]:
    """Oggetti annuncio ('"property":{...}') incorporati nella pagina: li isoliamo contando le graffe
    (ignorando quelle dentro le stringhe escapate) perché non sono JSON valido di per sé (fanno parte
    della stringa React Flight)."""
    out, marker, pos = [], '\\"property\\":{', 0
    while True:
        idx = html.find(marker, pos)
        if idx == -1:
            break
        start = idx + len(marker) - 1
        i, depth = start, 0
        while i < len(html):
            c = html[i]
            if c == "\\":
                i += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        obj_text = html[start:i]
        pos = i
        try:
            obj = json.loads(json.loads('"' + obj_text + '"'))
        except Exception:
            obj = None
        if isinstance(obj, dict) and obj.get("code"):
            out.append(obj)
    return out


def _remax_detail_url(html: str, code: str) -> str | None:
    """URL pubblico della scheda (su un sottodominio per agenzia, es. enterprise.remax.it): non ricavabile
    dai soli campi dell'annuncio (lo slug di zona non è una semplice trasformazione del nome zona), ma
    presente alla lettera nel JSON-LD incorporato nella stessa pagina elenco."""
    m = re.search(r"https://[a-z0-9.-]+\.remax\.it/vendita-[a-z-]+/[a-z-]+/[a-z0-9'-]+-" + re.escape(code), html)
    return m.group(0) if m else None


@register("remax")
class RemaxSource(Source):
    def fetch(self, ctx):
        found = {}
        for town, slug in REMAX_TOWNS.items():
            seen, page = set(), 1
            while page <= 30:
                html = ctx.http.text(f"https://www.remax.it/vendita-case/{slug}", params={"page": page})
                matched = [p for p in _remax_properties(html) if _town(p.get("city")) == town]
                new = [p for p in matched if p["code"] not in seen]
                if not new:
                    break
                for p in new:
                    seen.add(p["code"])
                    found[p["code"]] = (town, p, _remax_detail_url(html, p["code"]))
                page += 1
            ctx.log(f"{town}: {len(seen)} annunci")
        ctx.log(f"{len(found)} annunci nell'elenco (provincia di Trieste)")
        out, details = [], 0
        for code, (town, p, url) in found.items():
            try:
                L = self._parse(code, town, p, url)
            except Exception as ex:
                ctx.errors.append(f"{code}: {ex}")
                continue
            if L is None:
                continue
            if url and details < self.max_details and ctx.store.needs_detail(L.id, L.price):
                details += 1
                try:
                    self._detail(ctx, L)
                    ctx.detail_ids.add(L.id)
                except Exception as ex:
                    ctx.errors.append(f"{L.url}: {ex}")
                    _known(ctx, L)
            else:
                _known(ctx, L)
            out.append(L)
        return out

    def _parse(self, code, town, p, url):
        utype = p.get("propertyType") or ""
        if NON_RES.search(utype.replace("_", " ")):
            return None
        label = REMAX_TYPE_LABELS.get(utype) or (utype.replace("_", " ").strip().capitalize() or "Immobile")
        L = Listing(source=self.id, ref=code,
                    url=url or f"https://www.remax.it/vendita-case/{REMAX_TOWNS.get(town, 'trieste')}#{code}",
                    title=f"{label} in vendita a {town}",
                    price=None if p.get("isConfidential") else pu.parse_price(p.get("price")),
                    mq=pu.parse_mq(p.get("totalArea")), rooms=pu.parse_rooms(p.get("rooms")),
                    bedrooms=pu.parse_small_count(p.get("bedrooms"), ["camere"]),
                    bathrooms=pu.parse_small_count(p.get("bathrooms"), ["bagni"]),
                    town=town, zone=p.get("neighborhood") or p.get("quadrantName") or None,
                    address=p.get("address") or None,
                    floor=str(p["floor"]) if p.get("floor") not in (None, "") else None,
                    agency=p.get("agencyName"))
        if p.get("latitude") and p.get("longitude"):
            _geo(L, p["latitude"], p["longitude"])
        L.type = self._type(utype)
        if p.get("isNew") or utype == "nuove_costruzioni":
            L.condition = "nuovo"
        if p.get("mainPhotoUrl"):
            L.images = [p["mainPhotoUrl"]]
        return L

    @staticmethod
    def _type(utype):
        t = (utype or "").lower()
        if "attic" in t or "mansard" in t:
            return "attico"
        if "vill" in t or "schiera" in t:
            return "villa"
        if "casa" in t or "rustic" in t or "casale" in t or "stabile" in t:
            return "casa"
        if "appart" in t or "loft" in t:
            return "appartamento"
        return None

    def _detail(self, ctx, L):
        html = ctx.http.text(L.url)
        d = next((x for x in _remax_properties(html) if x.get("code") == L.ref), None)
        if not d:
            raise ValueError("scheda vuota")
        desc = _remax_resolve(d.get("description"), _remax_flight_chunks(html))
        if isinstance(desc, str) and desc.strip():
            L.description = _text(htmlmod.unescape(desc))
        if d.get("address"):
            L.address = d["address"]
        coords = d.get("coordinates") or {}
        if coords.get("lat") and coords.get("lng"):
            _geo(L, coords["lat"], coords["lng"])
        photos = sorted((d.get("photos") or []), key=lambda x: x.get("order") or 0)
        imgs = [x.get("url") for x in photos if isinstance(x, dict) and x.get("url")]
        if len(imgs) > len(L.images or []):
            L.images = imgs[:12]
        L.energy = _energy(d.get("energyClass"))
        L.condition = L.condition or _condition((d.get("conservationState") or "").replace("_", " "))
        if d.get("floor") not in (None, ""):
            L.floor = str(d["floor"])
        if not L.type or L.type == "altro":
            L.type = pu.detect_type(L.title, L.description)
        L.features = _feats(elevator=_yes(d.get("hasElevator")),
                            garage=_yes(d.get("hasGarage") or d.get("hasBox") or d.get("hasParking")),
                            terrace=_yes(d.get("hasTerrace") or d.get("hasBalcony")),
                            garden=_yes(d.get("hasGarden")))


# ---------------------------------------------------------------- Gabetti

GABETTI_AGENCIES = {880: "Gabetti Trieste San Giusto", 1726: "Gabetti Trieste San Vito", 1588: "Gabetti Udine"}


@register("gabetti")
class GabettiSource(Source):
    """Solo pagine HTML (robots.txt vieta /api/): i dati sono nel payload RSC di Next.js."""
    URL = "https://www.gabetti.it/vendita/provincia-ts"

    def fetch(self, ctx):
        items, page, total_pages, total = [], 1, 1, None
        while page <= min(total_pages, 30):
            html = ctx.http.text(self.URL + (f"?page={page}" if page > 1 else ""))
            blob = self._flight(html)
            data = self._results(blob)
            if data is None:
                if page == 1:
                    raise ValueError("dati elenco Gabetti non trovati")
                break
            texts = self._texts(blob)
            total_pages, total = int(data.get("totalPages") or 1), data.get("totalCount")
            batch = data.get("initialData") or []
            for x in batch:
                for k, v in list(x.items()):
                    if v == "$undefined":
                        x[k] = None
                d = x.get("description")
                if isinstance(d, str) and re.fullmatch(r"\$[0-9a-f]+", d):
                    x["description"] = texts.get(d[1:])
            items += batch
            if not batch:
                break
            page += 1
        ctx.log(f"{len(items)} annunci nell'elenco (dichiarati {total})")
        out, seen = [], set()
        for x in items:
            if str(x.get("id")) in seen:
                continue
            seen.add(str(x.get("id")))
            try:
                L = self._parse(x)
            except Exception as ex:
                ctx.errors.append(f"{x.get('id')}: {ex}")
                continue
            if L:
                out.append(L)
        return out

    @staticmethod
    def _flight(html: str) -> str:
        parts = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html)
        return "".join(json.loads(p) for p in parts)

    @staticmethod
    def _results(blob: str) -> dict | None:
        dec = json.JSONDecoder()
        pos = blob.find('"totalCount"')
        while pos != -1:
            st = pos
            for _ in range(200):
                st = blob.rfind("{", 0, st)
                if st < 0:
                    break
                try:
                    o, _end = dec.raw_decode(blob, st)
                except ValueError:
                    continue
                if isinstance(o, dict) and "totalCount" in o and "initialData" in o:
                    return o
            pos = blob.find('"totalCount"', pos + 1)
        return None

    @staticmethod
    def _texts(blob: str) -> dict:
        """Blocchi di testo RSC 'id:T<lunghezza hex in byte>,<testo>' (le descrizioni lunghe stanno lì)."""
        # un blocco T non termina con "\n": il successivo può iniziare subito dopo il testo
        rx = re.compile(r"([0-9a-f]+):T([0-9a-f]+),")
        out, done = {}, set()
        queue = [m.start(1) for m in re.finditer(r"(?:^|\n)([0-9a-f]+):T[0-9a-f]+,", blob)]
        while queue:
            p = queue.pop()
            if p in done:
                continue
            done.add(p)
            m = rx.match(blob, p)
            if not m:
                continue
            n = int(m.group(2), 16)
            text = blob[m.end():m.end() + n].encode("utf-8")[:n].decode("utf-8", "ignore")
            out[m.group(1)] = text
            queue.append(m.end() + len(text))
        return out

    def _parse(self, x):
        if (x.get("category") or "vendita") != "vendita":
            return None
        f = x.get("features") or {}
        ttype = x.get("type") or f.get("type") or ""
        tslug = x.get("typeSlug") or f.get("typeSlug") or ""
        if NON_RES.search(tslug) or NON_RES.search(ttype):
            return None
        city = x.get("city") or ""
        prov = re.search(r"\(([A-Z]{2})\)", city)
        town = _town(city) if not prov or prov.group(1) == "TS" else re.sub(r"\s*\(.*", "", city)
        url = f"https://www.gabetti.it/vendita/{x.get('citySlug')}/{tslug}/{x['id']}"
        addr = x.get("address")
        if addr and re.search(r"\bSNC\b", addr):
            addr = re.sub(r"\s*\bSNC\b", "", addr).strip() or None
        L = Listing(source=self.id, ref=str(x["id"]), url=url,
                    title=f"{ttype} in vendita" + (f" – {addr}" if addr else f" a {re.sub(r' *[(].*', '', city)}"),
                    price=pu.parse_price(x.get("price") or f.get("price")),
                    mq=pu.parse_mq(x.get("squareMeters") or f.get("surface")),
                    rooms=pu.parse_rooms(x.get("rooms") or f.get("rooms")),
                    bathrooms=pu.parse_small_count(x.get("bathrooms") or f.get("bathrooms"), ["bagni"]),
                    bedrooms=pu.parse_small_count(f.get("bedrooms"), ["camere"]),
                    town=town, address=addr, floor=f.get("floor"), energy=_energy(f.get("energy_class")),
                    condition=_condition(f.get("property_condition")),
                    description=_text(x.get("description")) if isinstance(x.get("description"), str) else None,
                    agency=GABETTI_AGENCIES.get(x.get("agencyId")) or (f"Gabetti (agenzia {x.get('agencyId')})" if x.get("agencyId") else None))
        L.type = RemaxSource._type(ttype) or pu.detect_type(ttype + " ")
        if x.get("lat") and x.get("lon"):
            _geo(L, x["lat"], x["lon"])
        L.images = [i for i in x.get("images") or [] if isinstance(i, str)][:12]
        terrace = None
        if f.get("terraces") is not None or f.get("balconies") is not None:
            terrace = (f.get("terraces") or 0) + (f.get("balconies") or 0) > 0
        L.features = _feats(elevator=f.get("elevator") if isinstance(f.get("elevator"), bool) else None,
                            garage=True if (f.get("parking_spaces") or f.get("box")) else None, terrace=terrace,
                            garden=True if f.get("garden") else None)
        return L


# ---------------------------------------------------------------- Engel & Völkers

@register("engelvoelkers")
class EngelVoelkersSource(Source):
    URL = "https://www.engelvoelkers.com/it/it/proprieta/res/vendita/immobili/friuli-venezia-giulia/trieste"
    DETAIL = "https://www.engelvoelkers.com/it/it/exposes/{}"
    IMG = "https://uploadcare.engelvoelkers.com/{}/"

    def fetch(self, ctx):
        j = _next_data(ctx.http.text(self.URL))
        pp = ((j.get("props") or {}).get("pageProps") or {})
        # il sito non espone più i risultati in searchModule.initialSearchResults: ora sono nella query
        # React-Query "listings" dentro dehydratedState (il resto della struttura è invariato).
        queries = (pp.get("dehydratedState") or {}).get("queries") or []
        res = next((q.get("state", {}).get("data") for q in queries
                    if (q.get("queryKey") or [None])[0] == "listings"), None)
        if not res or "listings" not in res:
            raise ValueError("risultati E&V non trovati")
        rows = [r.get("listing") or {} for r in res["listings"]]
        if (res.get("listingsTotal") or 0) > len(rows):
            ctx.errors.append(f"elenco E&V incompleto: {len(rows)} di {res.get('listingsTotal')}")
        shop = self.cfg.get("shop", "Trieste")
        rows = [r for r in rows if shop.lower() in (r.get("shopName") or "").lower()]
        ctx.log(f"{len(rows)} annunci dell'ufficio {shop} (su {res.get('listingsTotal')})")
        out, details = [], 0
        for r in rows:
            try:
                L = self._parse(r)
            except Exception as ex:
                ctx.errors.append(f"{r.get('id')}: {ex}")
                continue
            if L is None:
                continue
            if details < self.max_details and ctx.store.needs_detail(L.id, L.price):
                details += 1
                try:
                    self._detail(ctx, L, r["id"])
                    ctx.detail_ids.add(L.id)
                except Exception as ex:
                    ctx.errors.append(f"{L.url}: {ex}")
                    _known(ctx, L)
            else:
                _known(ctx, L)
            out.append(L)
        return out

    @staticmethod
    def _min(d):
        return (d or {}).get("min") if isinstance(d, dict) else d

    def _parse(self, r):
        if (r.get("propertyMarketingType") or r.get("marketingType") or "sale") != "sale":
            return None
        if (r.get("businessArea") or "residential") != "residential":
            return None
        area = r.get("area") or {}
        loc = next((c.get("text") for c in r.get("addressComponents") or [] if c.get("placeType") == "locality"), None)
        title = (r.get("profile") or {}).get("title")
        L = Listing(source=self.id, ref=r.get("displayId") or r["id"], url=self.DETAIL.format(r["id"]), title=title,
                    price=None if r.get("hasPriceOnRequest") else pu.parse_price(self._min((r.get("price") or {}).get("salesPrice"))),
                    mq=pu.parse_mq(self._min(area.get("livingSurface")) or self._min(area.get("totalSurface"))),
                    rooms=pu.parse_rooms(self._min(r.get("rooms"))),
                    bedrooms=pu.parse_small_count(self._min(r.get("bedrooms")), ["camere"]),
                    bathrooms=pu.parse_small_count(self._min(r.get("bathrooms")), ["bagni"]),
                    town=(detect_town(loc) or ("Trieste" if detect_zone(loc) else None)) if loc else None,
                    agency=r.get("shopName"))
        if loc and loc.lower() not in ("trieste",):
            L.zone = loc
        ptype = (r.get("propertyType") or "").lower()
        t = pu.detect_type(title)
        if t in ("altro", "box", "commerciale", "terreno"):
            # "house" può essere villa o casa: lo precisa la scheda (o l'archivio, se la scheda non si rilegge)
            t = "appartamento" if ptype == "apartment" else "altro"
        L.type = t
        L.images = [self.IMG.format(i) for i in r.get("uploadCareImageIds") or []][:12]
        if area.get("gardenSurface"):
            L.features["garden"] = True
        if area.get("terraceSurface") or area.get("balconySurface"):
            L.features["terrace"] = True
        return L

    def _detail(self, ctx, L, uuid):
        j = _next_data(ctx.http.text(L.url))
        qs = (((j.get("props") or {}).get("pageProps") or {}).get("dehydratedState") or {}).get("queries") or []
        d = next((((q.get("state") or {}).get("data") or {}).get("listing") for q in qs
                  if (q.get("queryKey") or [None])[0] == "listing"), None)
        if not d:
            raise ValueError("scheda E&V senza dati")
        prof = d.get("profile") or {}
        L.description = _text(prof.get("description"))
        if d.get("displayLat") and d.get("displayLng"):
            _geo(L, d["displayLat"], d["displayLng"])
        fl = self._min(d.get("floor"))
        if fl is not None:
            L.floor = "terra" if fl == 0 else str(fl)
        L.energy = _energy(self._min(d.get("energyClassNormalized")))
        L.condition = _condition(d.get("condition"))
        sub = (d.get("propertySubType") or "").lower()
        ptype = (d.get("propertyType") or "").lower()
        if ptype == "house" and re.search(r"villa|detached|semi|terrace|row|bungalow", sub):
            L.type = "villa"
        elif ptype == "apartment" and re.search(r"penthouse|attic|roof", sub):
            L.type = "attico"
        elif L.type in (None, "altro") and ptype == "house":
            L.type = pu.detect_type(L.title, L.description)
            if L.type in ("altro", "box", "commerciale", "terreno"):
                L.type = "casa"
        f = d.get("features") or d
        terrace = None
        if f.get("hasTerrace") is not None or f.get("hasBalcony") is not None:
            terrace = bool(f.get("hasTerrace") or f.get("hasBalcony"))
        L.features = _feats(elevator=f.get("hasElevator"), terrace=terrace, garden=f.get("hasGarden"),
                            garage=True if (f.get("hasCoveredParking") or f.get("hasGarage")) else None)


# ---------------------------------------------------------------- L'Immobiliare.com

@register("limmobiliare")
class LImmobiliareSource(Source):
    """Rete L'Immobiliare.com (gruppo Grimaldi): pagine elenco legate alla sessione (PHPSESSID)."""
    LIST = "https://www.limmobiliare.com/r-immobili/"
    AJAX = "https://www.limmobiliare.com/moduli/realestate/immobili_elenco_dettaglio.php"

    def fetch(self, ctx):
        agency = str(self.cfg.get("id_agenzia", "9031010"))
        html = ctx.http.text(self.LIST, params={"cf": "yes", "Motivazione[]": "1", "id_agenzia": agency})
        cards = self._cards(html)
        if not cards:
            raise ValueError("nessun annuncio nella prima pagina (struttura cambiata?)")
        seen = {c.ref for c in cards}
        for p in range(1, 30):   # p=1 è la seconda pagina
            more = self._cards(ctx.http.text(self.AJAX, params={"p": p, "loadAjax": "yes"}))
            new = [c for c in more if c.ref not in seen]
            if not new:
                break
            seen.update(c.ref for c in new)
            cards += new
        ctx.log(f"{len(cards)} annunci dell'agenzia {agency}")
        out, details = [], 0
        for L in cards:
            # il titolo è "<tipologia> in vendita a <comune>"
            if L.type in ("box", "commerciale", "terreno") or NON_RES.search(L.title or ""):
                continue
            # schede solo per gli annunci in provincia (molti sono nel Goriziano)
            if detect_town(L.town) and details < self.max_details and ctx.store.needs_detail(L.id, L.price):
                details += 1
                try:
                    self._detail(ctx, L)
                    ctx.detail_ids.add(L.id)
                except Exception as ex:
                    ctx.errors.append(f"{L.url}: {ex}")
                    _known(ctx, L)
            else:
                _known(ctx, L)
            out.append(L)
        return out

    def _cards(self, html):
        soup = soup_of(html)
        out = []
        for li in soup.select("li[data-idimmobile]"):
            try:
                L = self._card(li)
            except Exception:
                continue
            if L:
                out.append(L)
        return out

    def _card(self, li):
        ref = li["data-idimmobile"].strip()
        mu = li.find("meta", attrs={"itemprop": "url"})
        a = li.find("a", href=re.compile(r"/i-\d+-"))
        url = (mu.get("content") if mu else None) or (a.get("href") if a else None)
        if not url:
            return None
        mot = li.select_one(".motivazione")
        if mot and "vendita" not in mot.get_text(" ", strip=True).lower():
            return None
        tit = li.select_one(".titolo")
        title = re.sub(r"\s+", " ", tit.get_text(" ", strip=True)) if tit else ""
        mt = re.search(r"vendita a (.+)$", title, re.I)
        comune = mt.group(1).strip() if mt else None
        icons = {}
        for ico in li.select(".icone .ico"):
            lab = ico.find("span")
            if lab:
                k = lab.get_text(strip=True).rstrip(":").lower()
                icons[k] = ico.get_text(" ", strip=True)[len(lab.get_text(strip=True)):].strip()
        price_tag = li.select_one(".prezzo")
        testo = li.select_one(".testo")
        L = Listing(source=self.id, ref=ref, url=url, title=title or None,
                    price=pu.parse_price(price_tag.get_text(" ", strip=True)) if price_tag else None,
                    mq=pu.parse_mq(icons.get("totale mq")), rooms=pu.parse_rooms(icons.get("locali")),
                    bathrooms=pu.parse_small_count(icons.get("bagni"), ["bagni"]),
                    description=testo.get_text(" ", strip=True) if testo else None,
                    town=_town(comune), agency=self.cfg.get("agency_name", "L'Immobiliare.com Trieste – Di Casa in Casa"))
        # zona dall'indirizzo della scheda: /i-46-vendita-appartamento-trieste-centro/
        if comune:
            slug_town = re.sub(r"[^a-z0-9]+", "-", comune.lower()).strip("-")
            mz = re.search(re.escape(slug_town) + r"-([a-z0-9-]+)/?$", url)
            if mz:
                L.zone = mz.group(1).replace("-", " ").strip().title()
        lat = li.find("meta", attrs={"itemprop": "latitude"})
        lon = li.find("meta", attrs={"itemprop": "longitude"})
        try:
            if lat and lon and float(lat["content"]) and float(lon["content"]):
                _geo(L, lat["content"], lon["content"])
        except (ValueError, KeyError):
            pass
        img = li.find("img", attrs={"data-src": True})
        if img and not re.search(r"nofoto|loader|logo", img["data-src"], re.I):
            L.images = [img["data-src"].replace("/foto/thumb/", "/foto/")]
        L.type = pu.detect_type(title)
        return L

    def _detail(self, ctx, L):
        html = ctx.http.text(L.url)
        soup = soup_of(html)
        box = {}
        for b in soup.select("div.box"):
            s = b.find("strong")
            if s:
                k = s.get_text(strip=True).rstrip(":").strip().lower()
                box.setdefault(k, b.get_text(" ", strip=True)[len(s.get_text(strip=True)):].strip())
        L.bedrooms = pu.parse_small_count(box.get("camere"), ["camere"]) or L.bedrooms
        L.bathrooms = L.bathrooms or pu.parse_small_count(box.get("bagni"), ["bagni"])
        L.rooms = L.rooms or pu.parse_rooms(box.get("locali"))
        L.mq = L.mq or pu.parse_mq(box.get("totale mq"))
        L.floor = box.get("piano") or L.floor
        L.energy = _energy(box.get("classe energetica") or box.get("classe")) or L.energy
        L.condition = _condition(box.get("stato immobile") or box.get("condizioni"))
        L.features = _feats(elevator=_yes(box.get("ascensore")), garage=_yes(box.get("box") or box.get("posto auto")),
                            terrace=_yes(box.get("terrazzo") or box.get("balcone") or box.get("terrazzi")),
                            garden=_yes(box.get("giardino")))
        com = soup.select_one(".blocco_1 .comune")
        if com:
            t = com.get_text(" ", strip=True)
            town_part, _, addr = t.partition(" - ")
            if addr:
                L.address = addr.strip()
        zona = soup.select_one(".blocco_1 .zona")
        if zona and zona.get_text(strip=True):
            L.zone = zona.get_text(" ", strip=True)
        # descrizione completa: il paragrafo più lungo della scheda
        best = None
        for tag in soup.find_all(["p", "div"]):
            if tag.find(["div", "p", "ul", "table", "form"]):
                continue
            tx = tag.get_text(" ", strip=True)
            if len(tx) > 150 and (best is None or len(tx) > len(best)) and \
                    not re.search(r"cookie|privacy|trattamento dei|informazioni contenute|riproduzione", tx[:300], re.I):
                best = tx
        if best and len(best) > len(L.description or "") - 10:
            L.description = best
        m1 = re.search(r'var lat\s*=\s*"([\d.]+)"', html)
        m2 = re.search(r'var lgt\s*=\s*"([\d.]+)"', html)
        if m1 and m2:
            _geo(L, m1.group(1), m2.group(1))
        # foto della scheda, solo dalla cartella dell'agenzia (la pagina mostra anche immobili di altri)
        folder = next((m.group(1) for u in L.images for m in [re.search(r"/custom/(\d+)/", u)] if m), r"\d+")
        imgs = re.findall(r'https://cdn\d*\.gestim\.biz/custom/' + folder + r'/foto/(?!thumb/)[^"\'\s)]+\.(?:jpe?g|png|webp)', html, re.I)
        if imgs:
            L.images = list(dict.fromkeys(imgs + L.images))[:12]


# ---------------------------------------------------------------- Dove.it

@register("doveit")
class DoveitSource(Source):
    URL = "https://www.dove.it/vendita-case/trieste"

    def fetch(self, ctx):
        out, page, total_pages = [], 1, 1
        while page <= min(total_pages, 20):
            pp = (_next_data(ctx.http.text(self.URL, params={"p": page} if page > 1 else None)).get("props") or {}).get("pageProps") or {}
            loc = ((pp.get("query") or {}).get("locality") or "").lower()
            if loc != "trieste":
                # slug sconosciuto: il sito ripiega sui risultati di tutta Italia -> non è un vero
                # elenco di Trieste (e se capitasse con annunci già in archivio, non vanno segnati
                # come "rimossi" per questo): solleva un'eccezione invece di restituire una lista vuota.
                raise ValueError(f"località inattesa '{loc}' (il sito è ripiegato su tutta Italia)")
            props = pp.get("properties")
            if not isinstance(props, dict) or "content" not in props:
                raise ValueError("elenco Dove.it non trovato")
            total_pages = int(props.get("totalPages") or 0)
            for x in props["content"]:
                try:
                    L = self._parse(x)
                except Exception as ex:
                    ctx.errors.append(f"{x.get('id')}: {ex}")
                    continue
                if L:
                    out.append(_known(ctx, L))
            if not props["content"]:
                break
            page += 1
        ctx.log(f"{len(out)} annunci")
        return out

    def _parse(self, x):
        if (x.get("status") or "LIVE") != "LIVE":
            return None
        hl = {h.get("label"): h.get("value") for h in x.get("highlights") or []}
        loc = x.get("locality")
        L = Listing(source=self.id, ref=str(x["id"]), url="https://www.dove.it" + (x.get("relativePath") or ""),
                    title=x.get("title"), price=pu.parse_price(x.get("price")), mq=pu.parse_mq(hl.get("superficie")),
                    rooms=pu.parse_rooms(x.get("roomsNumber") or hl.get("locali")), description=x.get("abstract"),
                    address=x.get("route"),
                    town=_town(loc) if (x.get("plateCode") or "TS") == "TS" else (loc or "fuori provincia"),
                    agency="Dove.it")
        imgs = [x.get("coverImageSet") or {}] + list(x.get("additionalCoverSet") or [])
        L.images = [i.get("large") or i.get("original") for i in imgs if i][:12]
        L.type = pu.detect_type(L.title, L.description)
        return L
