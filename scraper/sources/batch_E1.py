"""Adattatori specifici per il batch E1 (agenzie di Trieste senza pagina annunci nota).

Ogni classe si registra con prefisso ``e1_`` per non collidere con altri batch.
"""
import json
import re
from urllib.parse import unquote, urljoin

from .. import parse_utils as pu
from ..models import Listing
from ..zones import detect_town, detect_zone
from . import register
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of

# ---------------------------------------------------------------- Hometrieste (Studio Roiano + Home San Vito)
# Sito Vue "vetrina immobiliare": l'elenco vero arriva da GET /?ajax=<pagina>&condition=1&slice=15 (JSON).
# banner 15 = "Venduto", 16 = "Locato" (confermato via /web/data?component=tab&element=banner): da escludere.

_SOLD_OR_RENTED_BANNERS = {15, 16}
_TYPOLOGY_MAP = {
    "appartamento": "appartamento",
    "magazzino": "commerciale",
    "attico/mansarda": "attico",
    "box/posto auto": "box",
    "locale d'affari/ufficio": "commerciale",
    "terreno": "terreno",
    "terreno edificabile": "terreno",
}


@register("e1_hometrieste")
class HometriesteSource(Source):
    def fetch(self, ctx):
        base = self.cfg.get("api_base", self.cfg["website"]).rstrip("/")
        out, seen = [], set()
        page = 1
        while page <= 30:
            r = ctx.http.get(base + "/", params={"ajax": page, "condition": 1, "slice": 15})
            j = r.json()
            if "data" not in j:
                raise ValueError("risposta hometrieste senza 'data'")
            rows = j["data"]
            if not rows:
                break
            for x in rows:
                rid = x.get("id")
                if rid is None or rid in seen:
                    continue
                seen.add(rid)
                L = self._parse(x, base, self.id)
                if L:
                    out.append(L)
            if len(rows) < 15:
                break
            page += 1
        if not out:
            raise ValueError("nessun annuncio in vendita trovato (struttura API cambiata?)")
        return out

    @staticmethod
    def _parse(x, base, source_id):
        banners = set(x.get("banners") or [])
        if banners & _SOLD_OR_RENTED_BANNERS or x.get("status") == "Completato con successo":
            return None
        wd = x.get("webdata") or {}
        if wd.get("renting") and not wd.get("selling"):
            return None
        slug = wd.get("slug")
        url = f"{base}/property/{slug}" if slug else f"{base}/property/{x.get('id')}"
        title = wd.get("title") or (x.get("seo") or {}).get("title")
        agent = x.get("agent") or {}
        desc = x.get("description") or re.sub(r"<[^>]+>", " ", wd.get("description") or "")
        L = Listing(source=source_id, ref=str(x["id"]), url=url, title=title,
                    price=pu.parse_price(x.get("price")),
                    mq=pu.parse_mq(x.get("surface")),
                    bedrooms=pu.parse_small_count(x.get("bedroom_number"), ["camere"]),
                    bathrooms=pu.parse_small_count(x.get("bathroom_number"), ["bagni"]),
                    floor=x.get("floor_number") or None,
                    address=x.get("address") or None,
                    description=pu.clean_text(desc),
                    agency=agent.get("agency"))
        L.images = [m for m in x.get("media") or [] if isinstance(m, str)][:12]
        typ = (x.get("typology") or "").strip().lower()
        if typ == "casa/villa":
            L.type = "villa" if re.search(r"\bvilla|villino|villetta|schiera|bifamiliar", title or "", re.I) else "casa"
        elif typ in _TYPOLOGY_MAP:
            L.type = _TYPOLOGY_MAP[typ]
        return L


# ---------------------------------------------------------------- OK CASA web Italia (rete Monfalcone/Gorizia, Gestim)
# Elenco vendita: /ricerca/?s_sellrent=sell&s_category=0&s_orderby=price_up&s_price_min=0&s_price_max=1000000&page=N
# (il parametro page funziona SOLO se sono presenti anche gli altri parametri della query). Rete multi-provincia
# (Belluno, Gorizia, Milano, Trieste, Udine, Venezia, Verona...): filtriamo per comune di provincia di Trieste
# già sul titolo della card (che riporta il comune) per non scaricare centinaia di schede fuori zona.
_OKCASAWEB_LIST = ("https://www.okcasaweb.it/ricerca/?s_sellrent=sell&s_category=0&s_orderby=price_up"
                   "&s_price_min=0&s_price_max=1000000&page={n}")
_OKCASAWEB_CARD_RE = re.compile(r'<article class="ric-card">.*?</article>', re.S)
_OKCASAWEB_HREF_RE = re.compile(r'href="(/ricerca/(\d+)/)"')
_OKCASAWEB_TITLE_RE = re.compile(r'ric-title">\s*(.*?)\s*</h3>', re.S)


@register("e1_okcasaweb")
class OkCasaWebSource(Source):
    def fetch(self, ctx):
        base = "https://www.okcasaweb.it"
        candidates, seen_ids = [], set()
        page, found_new = 1, True
        while page <= 40 and found_new:
            html = ctx.http.text(_OKCASAWEB_LIST.format(n=page))
            cards = _OKCASAWEB_CARD_RE.findall(html)
            if not cards:
                break
            found_new = False
            for c in cards:
                m = _OKCASAWEB_HREF_RE.search(c)
                if not m or m.group(2) in seen_ids:
                    continue
                seen_ids.add(m.group(2))
                found_new = True
                tm = _OKCASAWEB_TITLE_RE.search(c)
                title = re.sub(r"\s+", " ", tm.group(1)).strip() if tm else ""
                if detect_town(title):
                    candidates.append((m.group(2), urljoin(base + "/", m.group(1))))
            page += 1
        if page > 40:
            raise ValueError("troppe pagine di risultati: struttura o paginazione cambiata?")
        if not seen_ids:
            raise ValueError("nessun risultato di vendita trovato (struttura pagina cambiata?)")
        out = []
        for rid, url in candidates:
            L = Listing(source=self.id, ref=rid, url=url)
            if ctx.store.needs_detail(L.id):
                html = ctx.http.text(url)
                L = parse_detail(html, url, self.id, L)
                if L.features.pop("_rent", False):
                    continue
                ctx.detail_ids.add(L.id)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


# ---------------------------------------------------------------- Immobili Senza Confini (Getrix / Immobiliare.it)
# Sito Getrix ASP: l'elenco visibile richiede JS, ma i dati arrivano puliti in JSON da
# /web/include/aj-ricerca.asp (richiede prima una GET su /web/immobili.asp per ottenere il cookie di sessione ASP).
_ISC_BASE = "https://www.immobilisenzaconfini.com"
_ISC_LIST_URL = _ISC_BASE + "/web/immobili.asp?tipo_contratto=V"
_ISC_API_URL = _ISC_BASE + "/web/include/aj-ricerca.asp"
_ISC_PARAMS = {
    "showkind": "", "num_page": "1", "group_cod_agenzia": "2001533", "cod_sede": "0", "cod_sede_aw": "0",
    "cod_gruppo": "0", "pagref": "", "ref": "", "language": "ita", "maxann": "50", "shid": "0", "estero": "0",
    "cod_campi": "", "cod_nazione": "", "cod_regione": "", "tipo_contratto": "V",
}


@register("e1_immobilisenzaconfini")
class ImmobiliSenzaConfiniSource(Source):
    def fetch(self, ctx):
        ctx.http.text(_ISC_LIST_URL)  # instaura la sessione ASP (cookie), altrimenti l'API risponde 404
        headers = {"Referer": _ISC_LIST_URL, "X-Requested-With": "XMLHttpRequest"}
        r = ctx.http.get(_ISC_API_URL, params=_ISC_PARAMS, headers=headers)
        try:
            j = json.loads(r.content.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"risposta aj-ricerca.asp non valida: {e}")
        if j.get("status") not in ("ok", "maxreach"):
            raise ValueError(f"risposta aj-ricerca.asp inattesa: {j.get('status')!r}")
        items = j.get("AN") or []
        out = []
        for it in items:
            town = detect_town(it.get("COMU"), it.get("TITD"))
            if not town or pu.detect_sold(it.get("DESC"), it.get("TITD")):
                continue
            rid = it.get("IDAN")
            if not rid:
                continue
            url = urljoin(_ISC_BASE, it.get("LINK") or "")
            L = Listing(source=self.id, ref=it.get("RIFE") or str(rid), url=url)
            if ctx.store.needs_detail(L.id):
                try:
                    html = ctx.http.text(url)
                    L = parse_detail(html, url, self.id, L)
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
            else:
                ctx.merge_known(L)
            L.title = L.title or it.get("TITD")
            L.price = L.price or pu.parse_price(it.get("PREZ"))
            L.mq = L.mq or pu.parse_mq(it.get("SUPE"))
            L.town = L.town or town
            L.zone = L.zone or it.get("ZONA")
            L.rooms = L.rooms or pu.parse_rooms(it.get("VANI"))
            L.bedrooms = L.bedrooms or pu.parse_small_count(it.get("CAME"), ["camere"])
            L.bathrooms = L.bathrooms or pu.parse_small_count(it.get("BAGN"), ["bagni"])
            L.description = L.description or pu.clean_text(it.get("DESC"))
            if not L.images and it.get("FPRE"):
                L.images = [it["FPRE"]]
            out.append(L)
        if not out:
            raise ValueError("nessun annuncio di vendita in provincia trovato (struttura API cambiata?)")
        return out


# ---------------------------------------------------------------- Wix (sitemap dinamica) — Abitha, Predonzani
# Siti Wix: i link alle schede immobile non compaiono nell'HTML statico (SPA), ma il sitemap.xml sì, e le pagine
# scheda sono renderizzate lato server (prezzo/descrizione già nel testo), quindi parse_detail funziona.

def _wix_sitemap_links(ctx, sitemap_urls, path_regex, exclude_regex=None):
    out, seen = [], set()
    for su in sitemap_urls:
        xml = ctx.http.text(su)
        for loc in re.findall(r"<loc>(.*?)</loc>", xml):
            if not re.search(path_regex, loc):
                continue
            if exclude_regex and re.search(exclude_regex, loc, re.I):
                continue
            if loc not in seen:
                seen.add(loc)
                out.append(loc)
    return out


@register("e1_abitha")
class AbithaSource(Source):
    def fetch(self, ctx):
        links = _wix_sitemap_links(
            ctx, ["https://www.abithaimmobiliare.it/pages-sitemap.xml"],
            r"/scheda-?\d+$", exclude_regex=r"copia-di-",
        )
        if not links:
            raise ValueError("nessuna scheda immobile nel sitemap Wix (struttura cambiata?)")
        out = []
        for url in links:
            L = Listing(source=self.id, ref=ref_from_url(url), url=url)
            if ctx.store.needs_detail(L.id):
                try:
                    html = ctx.http.text(url)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    out.append(L)
                    continue
                text = soup_of(html).get_text(" ", strip=True)
                if re.search(r"\bCanone\s*:", text, re.I) and not re.search(r"\bPrezzo\s*:", text, re.I):
                    continue  # affitto, non vendita
                # NB: niente controllo "venduto" qui: nel testo libero di queste schede compaiono spesso frasi come
                # "viene venduto arredato" (= con mobili, non status di vendita) che darebbero falsi positivi.
                L = parse_detail(html, url, self.id, L)
                if L.features.pop("_rent", False):
                    continue
                if not L.mq:
                    m = re.search(r"\b(\d{2,4})\s*mq\b", text, re.I)
                    if m:
                        L.mq = pu.parse_mq(m.group(1))
                ctx.detail_ids.add(L.id)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("e1_predonzani")
class PredonzaniSource(Source):
    def fetch(self, ctx):
        links = _wix_sitemap_links(
            ctx,
            ["https://www.predonzani-immobiliare.it/dynamic-properties-1_p_59fb1c9b_48bc_4db7_b3b7_"
             "b27b17619f8e_0_5000-sitemap.xml"],
            r"/properties-1/",
        )
        if not links:
            raise ValueError("nessuna scheda immobile nel sitemap Wix (struttura cambiata?)")
        out = []
        for url in links:
            L = Listing(source=self.id, ref=ref_from_url(url), url=url)
            if ctx.store.needs_detail(L.id):
                try:
                    html = ctx.http.text(url)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    out.append(L)
                    continue
                text = soup_of(html).get_text(" ", strip=True)
                # NB: niente controllo "venduto" (falsi positivi tipo "viene venduto libero da persone e cose").
                L = parse_detail(html, url, self.id, L)
                if L.features.pop("_rent", False):
                    continue
                if not L.mq:
                    m = re.search(r"\bmq\s*(\d{2,4})\b", text, re.I) or re.search(r"\b(\d{2,4})\s*mq\b", text, re.I)
                    if m:
                        L.mq = pu.parse_mq(m.group(1))
                # Il template non riporta indirizzo/comune nel testo: l'unica fonte è lo slug dell'URL
                # (es. ".../attico-su-due-piani-in-via-besenghi,-19,-trieste").
                slug = unquote(url.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ").replace(",", " ")
                slug = re.sub(r"\s+", " ", slug).strip()
                if not L.town:
                    L.town = detect_town(slug)
                if not L.address:
                    L.address = slug.title() or None
                if not L.title or L.title.strip().upper() == "PREDONZANI IMMOBILIARE":
                    L.title = slug.title() or L.title
                ctx.detail_ids.add(L.id)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


# ---------------------------------------------------------------- V Realty (tema WP "prop-*", kv_pairs generico
# inciampa su un filtro di ricerca che confonde l'estrattore: leggiamo direttamente i selettori del tema).
@register("e1_virtualrealty")
class VirtualRealtySource(Source):
    def fetch(self, ctx):
        base = "http://www.virtualrealty.it"
        html = ctx.http.text(base + "/immobili/")
        soup = soup_of(html)
        links = sorted(set(
            abs_url(base, a["href"]) for a in soup.find_all("a", href=True)
            if re.search(r"/properties/[a-z0-9-]+/?$", a["href"])
        ))
        if not links:
            raise ValueError("nessuna scheda immobile trovata in /immobili/ (struttura cambiata?)")
        out = []
        for url in links:
            L = Listing(source=self.id, ref=ref_from_url(url), url=url)
            if not ctx.store.needs_detail(L.id):
                ctx.merge_known(L)
                out.append(L)
                continue
            try:
                html_d = ctx.http.text(url)
            except Exception as e:
                ctx.errors.append(f"{url}: {e}")
                out.append(L)
                continue
            s = soup_of(html_d)
            kv = {}
            for row in s.select(".prop-single-detail .three.columns"):
                label = row.get_text("|", strip=True).split("|")[0].rstrip(":").strip().lower()
                val_tag = row.select_one(".detailvalue")
                if val_tag:
                    kv[label] = val_tag.get_text(strip=True)
            if re.search(r"affitt|locazion", kv.get("contratto", ""), re.I):
                continue  # affitto, non vendita
            title_tag = s.select_one(".prop-title")
            L.title = title_tag.get_text(strip=True) if title_tag else None
            addr_tag = s.select_one(".prop-address")
            if addr_tag:
                L.address = re.sub(r"\s+", " ", addr_tag.get_text(" ", strip=True)).strip() or None
            L.price = pu.parse_price((kv.get("prezzo") or "").replace(",", ""))  # "125,000€" (virgola = migliaia)
            L.mq = pu.parse_mq(kv.get("superficie"))
            L.bedrooms = pu.parse_small_count(kv.get("camere"), ["camere"])
            L.bathrooms = pu.parse_small_count(kv.get("bagni"), ["bagni"])
            L.floor = kv.get("piano") or None
            desc_tag = s.select_one(".prop-single-content")
            if desc_tag:
                L.description = pu.clean_text(desc_tag.get_text(" ", strip=True))
            L.town = detect_town(L.address, L.title, L.description)
            L.images = [abs_url(url, img.get("src")) for img in s.select("img.attachment-full")]
            L.images = [i for i in L.images if i][:12]
            ctx.detail_ids.add(L.id)
            out.append(L)
        return out


# ---------------------------------------------------------------- Il Rifugio Immobiliare (sito 1&1 IONOS MyWebsite)
# Le schede immobile non sono linkate dall'home ma dalla pagina /sitemap/. Alcune schede rimangono online con la
# scritta "VENDUTO" accanto al prezzo: da escludere.
_RIFUGIO_FUORI_PROVINCIA = re.compile(r"\bumago\b|\bcervignano\b", re.I)  # zone note dell'agenzia fuori provincia


@register("e1_rifugio")
class RifugioSource(Source):
    def fetch(self, ctx):
        base = "https://www.ilrifugioimmobiliare.com"
        html = ctx.http.text(base + "/sitemap/")
        links = sorted(set(
            u for u in (abs_url(base, h) for h in re.findall(r'href="([^"]*schede-immobili/[^"]*)"', html))
            if u
        ))
        if not links:
            raise ValueError("nessun link a schede-immobili nella pagina /sitemap/ (struttura cambiata?)")
        out = []
        for url in links:
            L = Listing(source=self.id, ref=ref_from_url(url), url=url)
            if ctx.store.needs_detail(L.id):
                try:
                    html_d = ctx.http.text(url)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    out.append(L)
                    continue
                text = soup_of(html_d).get_text(" ", strip=True)
                if pu.detect_sold(text):
                    continue
                L = parse_detail(html_d, url, self.id, L)
                if L.features.pop("_rent", False):
                    continue
                # L'agenzia copre anche Umago (Croazia) e Cervignano (UD): niente town di default.
                # Ma titolo/descrizione spesso citano solo il rione ("OPICINA", "GRETTA", "BANNE"...) senza mai
                # scrivere "Trieste": usiamo le parole chiave dei rioni (comune di Trieste) per riconoscerli.
                blob = f"{L.title or ''} {L.description or ''}"
                if not L.town and not _RIFUGIO_FUORI_PROVINCIA.search(blob) and detect_zone(L.title, L.description):
                    L.town = "Trieste"
                ctx.detail_ids.add(L.id)
            else:
                ctx.merge_known(L)
            out.append(L)
        if not out:
            raise ValueError("nessun annuncio attivo trovato (tutti venduti o struttura cambiata?)")
        return out
