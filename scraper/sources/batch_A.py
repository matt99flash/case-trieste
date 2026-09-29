"""Adattatori per il batch A: agenzie WordPress (Houzez, RealHomes, WP Residence, Estatik, ecc.) a Trieste.

`a_wp` = GenericSource + pulizia delle entità HTML lasciate grezze nel titolo/descrizione da alcuni temi
(Houzez in particolare mette nel JSON-LD "name" il titolo già HTML-escaped, es. "&#8211;" invece di "–").
Usato per la maggior parte delle fonti del batch; quando serve leggere un elenco non raggiungibile con
pagine+link statici (endpoint AJAX/REST, sitemap) si aggiunge qui un adattatore dedicato.
"""
import html as htmlmod
import json
import re

from .. import parse_utils as pu
from ..models import Listing
from ..zones import detect_town, detect_zone
from . import register
from .base import Source, abs_url, kv_pairs, parse_detail, ref_from_url, soup_of
from .generic import GenericSource


def _unescape(s):
    return htmlmod.unescape(s) if isinstance(s, str) else s


def _fix_town(L: Listing):
    """Alcuni temi hanno un campo 'comune' nella scheda (letto da `kv_pairs`) che in realtà è
    un'etichetta generica dell'agenzia (es. sempre 'Trieste' anche per immobili fuori città, come
    Sistiana/Duino-Aurisina), quindi meno affidabile del titolo, che di solito nomina la località
    specifica. Se il titolo indica un comune (minore) diverso da quello già impostato, ha la
    precedenza (`detect_town`, zones.py, è di sola lettura: dà priorità ai comuni minori su Trieste)."""
    t = detect_town(L.title)
    if t and t != L.town:
        L.town = t


def _infer_town(L: Listing):
    """Molte schede citano solo il rione (es. 'Roiano', 'Chiarbola', 'Altura') senza mai scrivere
    'Trieste' esplicitamente, quindi `detect_town` (zones.py, condiviso) non lo trova e l'annuncio
    finirebbe escluso come fuori provincia. `ZONES_TRIESTE` (stesso modulo) elenca però solo i rioni
    del comune capoluogo: se `detect_zone` (funzione di sola lettura, non modifica zones.py) ne
    riconosce uno nel testo, il comune è Trieste."""
    if L.town:
        return
    if detect_zone(L.title, L.address, L.zone, L.description):
        L.town = "Trieste"


_MQ_STRAY = re.compile(r"(\d{1,4})\s*m\s*(?:<sup>\s*2\s*</sup>|²|2\b)", re.I)
# Houzez IT: "Superficie/Dimensione proprietà:</strong> 55 " (a volte senza unità di misura), che
# `kv_pairs` (base.py, condiviso) salta perché l'etichetta non è l'esatto 'superficie' previsto da
# LABELS. Quando la troviamo è un valore affidabile: sovrascrive anche un L.mq già valorizzato, perché
# capita che kv_pairs abbia abbinato per sbaglio etichetta e valore sbagliati (es. l'anno di
# costruzione letto come superficie, cfr. Immobiliare MET[R]ICA).
_MQ_LABELED = re.compile(r"(?:Superficie|Dimensione)\s+propriet\S*\s*:?\s*</strong>\s*(?:\n\s*)?(?:<span>)?\s*(\d{1,4})\b", re.I)


def _fix_mq(L: Listing, html: str):
    """Recupera la superficie quando il tema spezza 'm²' in 'm' + '<sup>2</sup>' o quando l'etichetta
    non è quella esatta riconosciuta da `kv_pairs` (in base.py/parse_utils.py, condivisi, da non
    toccare): `parse_mq` non riconosce '133 m' (suffisso 'mq'/'m²'/'m2' non attaccato al numero), e
    `kv_pairs` salta 'Superficie/Dimensione proprietà:' perché l'etichetta non è l'esatto 'superficie'.
    Cerca entrambi i pattern nell'HTML grezzo, dove il tag <sup> e l'etichetta originale sono ancora
    presenti; il pattern con etichetta esplicita (`_MQ_LABELED`) ha la priorità e può correggere anche
    un valore già presente ma sbagliato."""
    m = _MQ_LABELED.search(html)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 2000:
            L.mq = v
            return
    if L.mq:
        return
    m = _MQ_STRAY.search(html)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 5000:
            L.mq = v


_LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+?)\s*(?:\]\]>)?\s*</loc>", re.I)


def _extract_locs(xml: str) -> list[str]:
    """URL da una sitemap XML: alcuni plugin SEO (es. All in One SEO) avvolgono l'URL in
    '<![CDATA[...]]>' dentro <loc>, che una regex semplice su '<loc>url</loc>' non riconosce."""
    return _LOC_RE.findall(xml)


_PRICE_COMMA = re.compile(r"(\d{1,3}(?:,\d{3})+)(?!\d)")


def _fix_price(L: Listing, html: str):
    """Alcuni temi (Houzez in versione EN, Essential Real Estate) scrivono il prezzo con la virgola
    come separatore delle migliaia ('€55,000' invece di '€55.000'): `parse_price` (parse_utils.py,
    condiviso) si aspetta il formato italiano e scarta il numero letto come '55' (sotto soglia), quindi
    `parse_detail` ripiega sulla ricerca generica di '€ 1.234' nel testo della pagina, che a volte trova
    un importo del tutto estraneo al prezzo (es. un canone di affitto citato in descrizione per un
    immobile locato). `kv_pairs` estrae comunque la stringa grezza con la virgola dal blocco prezzo:
    quando la troviamo è un segnale più affidabile del fallback generico, quindi sovrascrive L.price
    anche se già valorizzato."""
    kv = kv_pairs(soup_of(html))
    raw = kv.get("price")
    if not raw:
        return
    m = _PRICE_COMMA.search(raw)
    if m:
        v = pu.parse_price(m.group(1).replace(",", "."))
        if v:
            L.price = v


@register("a_wp")
class WPCleanSource(GenericSource):
    """GenericSource + un-escape delle entità HTML rimaste grezze (bug frequente nel JSON-LD di Houzez)
    + recupero superficie quando 'm²' arriva spezzata dal tag <sup> (vedi `_fix_mq`). Rifà il giro
    schede->dettaglio di GenericSource (invece di richiamare super().fetch) perché deve riguardare
    l'HTML grezzo della scheda dopo `parse_detail`, che GenericSource non restituisce."""

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        max_pages = cfg.get("max_pages", 40)
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            url, page, visited = start, cfg.get("page_start", 2), set()
            for _ in range(max_pages):
                if not url or url in visited:
                    break
                visited.add(url)
                html = ctx.http.text(url)
                soup = soup_of(html)
                before = len(links)
                for a in soup.find_all("a", href=True):
                    u = abs_url(url, a["href"])
                    if u and link_rx.search(u) and not (excl and excl.search(u)):
                        links.setdefault(u)
                if len(links) == before:
                    break
                if cfg.get("page_template"):
                    url = cfg["page_template"].format(n=page)
                    page += 1
                else:
                    url = self._next_link(soup, url)
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=self._ref(u), url=u)
            if cfg.get("detail", True) and ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(u)
                    details += 1
                    L = parse_detail(html, u, self.id, L)
                    if cfg.get("require_regex") and not re.search(cfg["require_regex"], html, re.I):
                        continue
                    L.title = _unescape(L.title)
                    L.description = _unescape(L.description)
                    _fix_mq(L, html)
                    _fix_price(L, html)
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            _fix_town(L)
            _infer_town(L)
            out.append(L)
        return out


@register("a_sitemap")
class SitemapSource(Source):
    """Elenco letto da una sitemap XML di WordPress/Yoast (es. 'property-sitemap.xml'), per siti la cui
    pagina elenco carica le schede via JavaScript e non lascia link statici da seguire con `generic`.

    Parametri cfg:
      sitemap: url della sitemap XML (property-sitemap.xml, o l'indice se contiene più <sitemap> figli)
      link_regex: filtro sugli URL <loc> che sono schede immobile (scarta l'eventuale URL di archivio)
      exclude_regex: opzionale, scarta URL (es. affitti) in base al link
      exclude_content_regex: opzionale, scarta la scheda se l'HTML della pagina dettaglio la soddisfa
        (es. tema che marca "Status della proprietà: Venduto" invece di rimuovere l'annuncio)
    """

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        excl_content = re.compile(cfg["exclude_content_regex"], re.I | re.S) if cfg.get("exclude_content_regex") else None
        xml = ctx.http.text(cfg["sitemap"])
        locs = _extract_locs(xml)
        if not locs:
            raise ValueError(f"sitemap vuota: {cfg['sitemap']}")
        # sitemap indice: altre sitemap invece di URL di schede -> le scarico e unisco
        if all(re.search(r"sitemap.*\.xml$", u, re.I) for u in locs):
            merged = []
            for u in locs:
                sub = ctx.http.text(u)
                merged += _extract_locs(sub)
            locs = merged
        links = [u for u in locs if link_rx.search(u) and not (excl and excl.search(u))]
        if not links:
            raise ValueError(f"nessuna scheda nella sitemap: {cfg['sitemap']}")
        ctx.log(f"{len(links)} schede in sitemap")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(u)
                    details += 1
                    if excl_content and excl_content.search(html):
                        continue
                    L = parse_detail(html, u, self.id, L)
                    L.title = _unescape(L.title)
                    L.description = _unescape(L.description)
                    _fix_mq(L, html)
                    _fix_price(L, html)
                    self._augment(L, html)
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            _fix_town(L)
            _infer_town(L)
            out.append(L)
        return out

    def _augment(self, L: Listing, html: str):
        """Hook per correzioni specifiche di un tema, sovrascritto dalle sottoclassi (es. `EstatikSource`).
        Non fa nulla di default."""


@register("a_estatik")
class EstatikSource(SitemapSource):
    """SitemapSource per il tema/plugin WordPress 'Estatik': le pagine scheda sono quasi interamente
    renderizzate via JS, quindi `kv_pairs` (base.py, condiviso) pesca solo testo di scarto (nomi di
    classi CSS, voci di menu) e spesso abbina per sbaglio il prezzo alla chiave 'mq' o viceversa.
    Il prezzo vero è però nello span '<span class="es-price">€145,000</span>' (sempre presente, sempre
    affidabile: lo si preferisce a qualunque valore letto da `kv_pairs`), e la superficie abitativa è
    di solito il primo numero seguito da 'mq' nella descrizione JSON-LD (es. 'appartamento di 85 mq
    commerciali con cortile privato di 35 mq' -> 85, il cortile è la seconda occorrenza)."""

    _PRICE = re.compile(r'es-price">\s*(?:&euro;|€)\s*([\d,.]+)', re.I)

    def _augment(self, L: Listing, html: str):
        m = self._PRICE.search(html)
        if m:
            raw = m.group(1)
            # "145,000" (virgola migliaia) vs "145.000,00" (formato IT con decimali): normalizza le due
            # forme allo stesso modo di _fix_price, poi affida il parsing a parse_price.
            if re.search(r",\d{3}(?!\d)", raw):
                raw = raw.replace(",", ".")
            v = pu.parse_price(raw)
            if v:
                L.price = v
        # bug tipico di questo tema: kv_pairs abbina il prezzo (letto con la virgola, es. "€310,000")
        # alla chiave 'mq'; il fallback di parse_detail (base.py) interpreta poi "310,000 mq" come 310
        # (la ",000" viene presa per una parte decimale e scartata): il risultato è quasi sempre
        # mq*1000 == price. Qualunque valore oltre gli 800 mq è comunque quasi certamente sbagliato.
        bogus = L.mq and L.price and L.mq * 1000 == L.price
        if not L.mq or bogus or L.mq > 800:
            L.mq = None
            m = re.search(r"(\d{1,4})\s*mq\b", L.description or "", re.I)
            if m:
                v = int(m.group(1))
                if 10 <= v <= 2000:
                    L.mq = v


@register("a_ere")
class ERESource(GenericSource):
    """GenericSource per il tema WordPress 'Essential Real Estate' (ere__...): il markup delle schede
    mostra valore e poi etichetta (es. <span class="ere__lpi-value">138 m2</span><span
    class="ere__lpi-label">Superficie</span>), che `kv_pairs` non riconosce (si aspetta etichetta poi
    valore), e i prezzi sono a volte scritti con la virgola come separatore delle migliaia
    (es. "470,000" invece di "470.000"), che `parse_price` non riconosce. Rifà il giro schede->dettaglio
    di GenericSource aggiungendo l'estrazione dai blocchi ere__lpi-* per prezzo/superficie/camere/bagni.
    """

    _LPI = re.compile(
        r'ere__loop-property-info-item\s+property-(area|bedrooms|bathrooms)"[^>]*>.*?'
        r'ere__lpi-value">\s*([^<]+?)\s*<', re.S)
    _PRICE = re.compile(r'ere__(?:loop|single)-property-price">\s*[^\d]*([\d,.]+)', re.S)

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        max_pages = cfg.get("max_pages", 40)
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            url, page, visited = start, cfg.get("page_start", 2), set()
            for _ in range(max_pages):
                if not url or url in visited:
                    break
                visited.add(url)
                html = ctx.http.text(url)
                soup = soup_of(html)
                before = len(links)
                for a in soup.find_all("a", href=True):
                    u = abs_url(url, a["href"])
                    if u and link_rx.search(u) and not (excl and excl.search(u)):
                        links.setdefault(u)
                if len(links) == before:
                    break
                if cfg.get("page_template"):
                    url = cfg["page_template"].format(n=page)
                    page += 1
                else:
                    url = self._next_link(soup, url)
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=self._ref(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(u)
                    details += 1
                    L = parse_detail(html, u, self.id, L)
                    L.title = _unescape(L.title)
                    L.description = _unescape(L.description)
                    self._augment(L, html)
                    _fix_mq(L, html)
                    _fix_price(L, html)
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            _fix_town(L)
            _infer_town(L)
            out.append(L)
        return out

    def _augment(self, L: Listing, html: str):
        from .. import parse_utils as pu
        for field, raw in self._LPI.findall(html):
            v = _unescape(raw).strip()
            if field == "area" and not L.mq:
                digits = re.sub(r"[^\d.,]", "", v)
                L.mq = pu.parse_mq(digits + " mq")
            elif field == "bedrooms" and not L.bedrooms:
                L.bedrooms = pu.parse_small_count(v, ["camere"])
            elif field == "bathrooms" and not L.bathrooms:
                L.bathrooms = pu.parse_small_count(v, ["bagni"])
        if not L.price:
            m = self._PRICE.search(html)
            if m:
                L.price = pu.parse_price(m.group(1).replace(",", "."))


@register("a_wpjson")
class WPRestSource(Source):
    """Elenco letto dall'API REST di WordPress (wp-json/wp/v2/<post_type>), per temi le cui pagine
    elenco caricano le schede via JavaScript (es. Estatik, Real Estate Manager) e per cui l'HTML statico
    non contiene i link agli annunci.

    Parametri cfg:
      api: url base 'https://sito/wp-json/wp/v2/<post_type>' (senza querystring)
      per_page: default 50
      status_param / status_value: filtro querystring per tenere solo la vendita (se il post_type mischia
        vendita/affitto), es. status_param='property_status', status_value='vendita' (usa la tassonomia
        con slug fornito, cfr. self._term_id)
      link_field: campo della risposta JSON da cui ricavare l'URL della scheda (default 'link')
      exclude_regex: opzionale, scarta URL (es. affitti) dal campo link
    """

    def fetch(self, ctx):
        cfg = self.cfg
        api = cfg["api"].rstrip("/")
        per_page = cfg.get("per_page", 50)
        params = dict(cfg.get("params") or {})
        params.setdefault("per_page", per_page)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        link_field = cfg.get("link_field", "link")
        rows = []
        page = 1
        while page <= cfg.get("max_pages", 30):
            p = {**params, "page": page}
            r = ctx.http.get(api, params=p)
            if r.status_code == 400:  # WP risponde 400 quando si supera l'ultima pagina
                break
            data = r.json()
            if not isinstance(data, list):
                raise ValueError(f"risposta REST inattesa da {api}")
            if not data:
                break
            rows += data
            if len(data) < per_page:
                break
            page += 1
        if not rows:
            raise ValueError(f"elenco vuoto da {api}")
        ctx.log(f"{len(rows)} voci REST trovate")
        out, details = [], 0
        for row in rows:
            url = row.get(link_field)
            if not url or (excl and excl.search(url)):
                continue
            ref = str(row.get("id")) or ref_from_url(url)
            L = Listing(source=self.id, ref=ref, url=url)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(url)
                    details += 1
                    L = parse_detail(html, url, self.id, L)
                    L.title = _unescape(L.title)
                    L.description = _unescape(L.description)
                    _fix_mq(L, html)
                    _fix_price(L, html)
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            _fix_town(L)
            _infer_town(L)
            out.append(L)
        return out
