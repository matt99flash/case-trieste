"""Adattatori specifici per il batch B2 (agenzie WordPress con plugin/temi non standard) a Trieste.

  b2_wp            GenericSource + un-escape delle entità HTML rimaste grezze (bug diffuso in alcuni temi
                   immobiliari); con relaxed_encoding: true tollera anche pagine UTF-8 con qualche byte
                   cp1252 isolato.
  b2_aemmecasa     b2_wp + fallback dedicato per prezzo/mq (il tema li mette in formato non standard).
  b2_sitemap       elenco letto da una o più sitemap XML (WP core o Yoast) invece che da pagine con <a href>:
                   utile quando la pagina "vendita" del sito non contiene i link diretti alle schede
                   (caricati via JS) ma esiste una sitemap con i permalink degli annunci.
  b2_attico        b2_sitemap + scarta le schede già "VENDUTO"/"AFFITTATO" (titolo) o in affitto (prezzo "/Mese")
                   + legge mq/piano/locali/bagni dai campi propri del tema.
  b2_searchfilter  elenco letto dall'endpoint AJAX del plugin WordPress "Search & Filter" (sf_action=get_data).
  b2_dinamica      le schede non hanno <h1>/og:title (l'estrattore generico ripiegherebbe sul <title> della
                   pagina, che aggiunge "Trieste" allo slogan e farebbe risultare in provincia anche gli
                   immobili fuori provincia del sito): titolo/indirizzo/descrizione dai blocchi del tema.
  b2_vidaligruden  pagina singola con più annunci come blocchi di testo (Elementor), senza schede separate.

Ogni classe si registra con prefisso ``b2_`` per non collidere con altri batch.
"""
import html as htmlmod
import re

from .. import parse_utils as pu
from ..models import Listing
from . import register
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of
from .generic import GenericSource


def _unescape(s):
    return htmlmod.unescape(s) if isinstance(s, str) else s


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:80] or "annuncio"


def _relaxed_decode(content: bytes) -> str:
    """Decodifica UTF-8 tollerante ai singoli byte non validi (bug frequente: pagina dichiarata UTF-8
    ma con qualche carattere accentato incollato in cp1252/Latin-1, es. 'Universit\xe0' -> 'Universit�')."""
    text = content.decode("utf-8", errors="surrogateescape")
    if not re.search("[\udc80-\udcff]", text):
        return text
    out = []
    for ch in text:
        cp = ord(ch)
        if 0xDC80 <= cp <= 0xDCFF:
            try:
                out.append(bytes([cp - 0xDC00]).decode("cp1252"))
            except UnicodeDecodeError:
                out.append("?")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------- b2_wp (GenericSource + unescape entità HTML)

@register("b2_wp")
class WPCleanSource(GenericSource):
    """GenericSource + un-escape delle entità HTML rimaste grezze nel titolo/descrizione (bug frequente
    in alcuni temi WordPress per il settore immobiliare, es. "&#8217;" al posto dell'apostrofo).
    Con `relaxed_encoding: true` in config tollera anche singoli byte non validi in pagine dichiarate
    UTF-8 (es. simbolo "€" incollato in cp1252, che altrimenti fa fallire il riconoscimento del prezzo).

    Fallback mq: alcuni temi (es. Borea, AureaHome) mettono la superficie in un elenco di "icone" dove
    valore ed etichetta non sono una coppia label:value riconoscibile dall'estrattore generico, es.
    '<li>370 m<sup>2</sup><span class="tooltip">Superficie</span></li>' (valore PRIMA dell'etichetta,
    in tooltip) oppure un box Elementor con un solo valore senza etichetta affatto ("185 m²"): quando
    L.mq resta vuoto si cercano questi pattern nella pagina.

    Fallback prezzo: il tema usato da Immobiliare Art (classe "property-header") non ha un'etichetta
    "Prezzo"/"€" abbinabile dall'estrattore generico: il prezzo sta in un <div class="meta"> subito sotto
    il titolo insieme a tipologia e contratto, senza separatore riconoscibile ("170.000 € · Appartamento
    · Vendita"). Quando L.price resta vuoto si rilegge quel primo div. Altre schede (Il Faro) scrivono il
    prezzo come frase libera "Euro 129.000" in un paragrafo a parte, senza alcuna etichetta "Prezzo" né il
    simbolo "€" (che l'estrattore generico riconosce): si cerca anche questo pattern in tutto il testo."""

    _EURO_WORD_RE = re.compile(r"\bEuro\s*([\d][\d.,]{2,})\b")

    def _detail_fallback(self, ctx, L):
        try:
            soup = soup_of(ctx.http.text(L.url))
        except Exception:
            return
        if not L.price:
            meta = soup.select_one(".property-header .meta")
            if meta:
                L.price = pu.parse_price(meta.get_text(" ", strip=True))
        if not L.price:
            m = self._EURO_WORD_RE.search(soup.get_text(" ", strip=True))
            if m:
                L.price = pu.parse_price(m.group(1))
        if not L.mq:
            for li in soup.select(".meta-box-list li"):
                tip = li.select_one(".tooltip")
                if tip and re.search(r"superfic", tip.get_text(strip=True), re.I):
                    L.mq = pu.parse_mq(li.get_text(" ", strip=True))
                    break
            if not L.mq:
                for box in soup.select(".elementor-icon-box-description, .elementor-icon-box-content"):
                    mq = pu.parse_mq(box.get_text(" ", strip=True))
                    if mq:
                        L.mq = mq
                        break

    def fetch(self, ctx):
        if self.cfg.get("relaxed_encoding"):
            orig_get = ctx.http.get
            ctx.http.text = lambda url, **kw: _relaxed_decode(orig_get(url, **kw).content)
        items = super().fetch(ctx)
        for L in items:
            L.title = _unescape(L.title)
            L.description = _unescape(L.description)
            if not L.mq or not L.price:
                self._detail_fallback(ctx, L)
        return items


# ---------------------------------------------------------------- b2_aemmecasa

@register("b2_aemmecasa")
class AemmecasaSource(GenericSource):
    """aemmecasa: il prezzo è in '<div class="meta">NUM€</div>' (numero PRIMA del simbolo, mentre
    l'estrattore generico riconosce solo '€ NUM') e non viene letto; qui si aggiunge un fallback dedicato
    per prezzo e mq quando l'estrattore generico non li trova. Il tema svuota il prezzo quando l'immobile
    risulta "VENDUTO" (badge '.property-header .status-update'): in quel caso niente prezzo da trovare,
    ma segnaliamo comunque L.sold così la dashboard non lo tratta come annuncio attivo senza prezzo."""

    def fetch(self, ctx):
        items = super().fetch(ctx)
        for L in items:
            L.title = _unescape(L.title)
            L.description = _unescape(L.description)
            if L.price and L.mq:
                continue
            try:
                soup = soup_of(ctx.http.text(L.url))
            except Exception:
                continue
            badge = soup.select_one(".property-header .status-update")
            if badge and pu.detect_sold(badge.get_text(strip=True)):
                L.sold = True
            if not L.price:
                for div in soup.select(".property-header .meta"):
                    m = re.search(r"([\d.,]+)\s*€", div.get_text(strip=True))
                    if m:
                        L.price = pu.parse_price(m.group(0))
                        break
            if not L.mq:
                pm = soup.select_one(".property-meta")
                if pm:
                    L.mq = pu.parse_mq(pm.get_text(" ", strip=True))
        return items


# ---------------------------------------------------------------- b2_sitemap

@register("b2_sitemap")
class SitemapSource(Source):
    """Scarica una o più sitemap XML e ne estrae i <loc> che combaciano con link_regex.

    Parametri cfg:
      sitemaps: [url, ...]
      link_regex: regex sull'URL della scheda
      exclude_regex: opzionale, scarta URL (es. affitti riconoscibili dallo slug)
    """

    def _links(self, ctx) -> dict:
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        links: dict[str, None] = {}
        for sm in cfg["sitemaps"]:
            xml = ctx.http.text(sm)
            for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
                u = m.group(1)
                if link_rx.search(u) and not (excl and excl.search(u)):
                    links.setdefault(u)
        if not links:
            raise ValueError(f"nessuna scheda trovata nelle sitemap {cfg['sitemaps']}")
        return links

    def _post_filter(self, html: str, L: Listing) -> bool:
        """True per tenere la scheda. Override nelle sottoclassi per filtri aggiuntivi."""
        return True

    relaxed_encoding = False  # True: tollera byte cp1252 isolati in pagine dichiarate UTF-8

    def _text(self, ctx, u):
        if self.relaxed_encoding:
            return _relaxed_decode(ctx.http.get(u).content)
        return ctx.http.text(u)

    def fetch(self, ctx):
        cfg = self.cfg
        links = self._links(ctx)
        ctx.log(f"{len(links)} schede trovate (sitemap)")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=self._ref(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    raw = self._text(ctx, u)
                    details += 1
                    L = parse_detail(raw, u, self.id, L)
                    if cfg.get("require_regex") and not re.search(cfg["require_regex"], raw, re.I):
                        continue
                    if L.features.pop("_rent", False):
                        continue
                    if not self._post_filter(raw, L):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    def _ref(self, url):
        rx = self.cfg.get("ref_regex")
        if rx:
            m = re.search(rx, url)
            if m:
                return m.group(1)
        return ref_from_url(url)


# ---------------------------------------------------------------- b2_rivi

_ASIDE_RE = re.compile(r"<aside\b.*?</aside>", re.S | re.I)
_RIVI_FIELD_RE = re.compile(
    r"\b(Price|Square Feet|Bedrooms|Bathrooms|Address|City)\s*:\s*([^\n]*?)"
    r"(?=\s*(?:Price|Square Feet|Bedrooms|Bathrooms|Address|City|State|ZIP|MLS\s*#|Basement|"
    r"Additional Features)\s*:|$)", re.I)
_RIVI_MQ_RE = re.compile(r"\b(\d{2,4})\s*(?:mq|m²)\b", re.I)


@register("b2_rivi")
class RiviSource(SitemapSource):
    """Rivi Immobiliare (tema 'AgentPress Pro' su Genesis Framework): la sidebar di OGNI scheda ha un
    filtro di ricerca con <select name='prezzo'>/<select name='tipologia'> le cui prime opzioni reali
    (dopo il placeholder) sono sempre le stesse su ogni pagina — `kv_pairs` le abbina per errore
    all'etichetta "Prezzo"/"Tipologia" come se fossero il valore dell'annuncio (lo stesso prezzo "a
    fascia" e la stessa tipologia "Appartamento" su OGNI scheda, capannone commerciale incluso): la
    sidebar va eliminata PRIMA di `parse_detail`, altrimenti inquina anche `L.type` (corretto poi da
    `finalize()`, ma solo se `kv_pairs` non ha già scritto un valore non vuoto).

    Il prezzo vero (quando pubblicato) sta in un blocco dati con etichette in INGLESE (tema MLS):
    "Price: 350000 Address: ... City: ... Square Feet: 100 Bedrooms: 2 Bathrooms: 2" — non riconosciuto
    da `kv_pairs` (etichette solo italiane). 'Square Feet' è in realtà già in metri quadri (i numeri
    coincidono con gli 'm² commerciali' citati nel testo libero, errore di etichetta del tema, non di
    unità). Quando il blocco manca (annunci senza prezzo pubblicato, es. "Trattativa riservata" implicita)
    la superficie è comunque spesso citata nel testo libero ("appartamento di 220 mq")."""

    def _text(self, ctx, u):
        return _ASIDE_RE.sub(" ", super()._text(ctx, u))

    def _post_filter(self, html: str, L: Listing) -> bool:
        text = soup_of(html).get_text(" ", strip=True)
        fields = {m.group(1).lower(): m.group(2).strip() for m in _RIVI_FIELD_RE.finditer(text)}
        if fields.get("price"):
            L.price = pu.parse_price(fields["price"])
        if fields.get("square feet"):
            L.mq = pu.parse_mq(fields["square feet"] + " mq")
        if fields.get("bedrooms"):
            L.bedrooms = pu.parse_small_count(fields["bedrooms"], ["camere"])
        if fields.get("bathrooms"):
            L.bathrooms = pu.parse_small_count(fields["bathrooms"], ["bagni"])
        if fields.get("address") and not L.address:
            L.address = fields["address"].title() or None
        if not L.mq:
            # non tutte le schede hanno il blocco dati (prezzo/mq strutturati): quando manca, la
            # superficie è spesso citata nel testo libero ("attico di 230 mq"), ma non sempre nel
            # paragrafo scelto da `parse_detail` come L.description, quindi si cerca in tutta la pagina.
            m = _RIVI_MQ_RE.search(text)
            if m:
                L.mq = pu.parse_mq(m.group(0))
        return True


# ---------------------------------------------------------------- b2_attico

_SOLD_TITLE = re.compile(r"^\s*(venduto|affittato|affittata|venduta)\b", re.I)


@register("b2_attico")
class AtticoSource(SitemapSource):
    """Attico Immobiliare: il tema pubblica in sitemap anche annunci già venduti/affittati (prefisso nel
    titolo "VENDUTO -"/"AFFITTATO -") e in affitto (prezzo con suffisso "/Mese"): li scarta. Il sito ha
    anche qualche titolo con un carattere accentato incollato in cp1252 su una pagina dichiarata UTF-8."""

    relaxed_encoding = True

    _ALT_MAP = {"metratura": "mq", "il piano a cui si trova": "floor", "numero camere": "rooms", "bagni": "bathrooms"}

    def _post_filter(self, html: str, L: Listing) -> bool:
        title = _unescape(L.title) or ""
        if _SOLD_TITLE.match(title):
            return False
        m = re.search(r'class="price"[^>]*>\s*([^<]*)<span[^>]*>\s*/\s*mese', html, re.I)
        if m:
            return False
        L.title = title
        L.description = _unescape(L.description)
        # il tema mette mq/piano/locali/bagni in <p class="info" alt="Etichetta"><span class="value">...
        # (più affidabile dell'estrattore generico a etichette, che su questo tema a volte becca numeri
        # sbagliati nel testo libero): qui sovrascrive sempre quando il valore è presente.
        for p in soup_of(html).select("p.info[alt]"):
            key = self._ALT_MAP.get((p.get("alt") or "").strip().lower())
            if not key:
                continue
            val = p.select_one(".value")
            val = val.get_text(strip=True) if val else None
            if not val:
                continue
            if key == "mq":
                L.mq = pu.parse_mq(val)
            elif key == "floor":
                L.floor = val
            elif key == "rooms":
                L.rooms = pu.parse_rooms(val)
            elif key == "bathrooms":
                L.bathrooms = pu.parse_small_count(val, ["bagni"])
        return True


# ---------------------------------------------------------------- b2_searchfilter (plugin "Search & Filter")

@register("b2_searchfilter")
class SearchFilterSource(Source):
    """Elenco caricato via AJAX dal plugin WordPress 'Search & Filter' (sfid): l'HTML statico della
    pagina non contiene le schede, che arrivano solo con GET ?sfid=<id>&sf_action=get_data&sf_data=all.

    Parametri cfg:
      ajax_urls: [url, ...]   uno o più endpoint (es. per categorie diverse)
      link_regex: regex sull'URL della scheda, applicata al campo 'results' della risposta JSON
      exclude_regex: opzionale
    """

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        links: dict[str, None] = {}
        for au in cfg["ajax_urls"]:
            j = ctx.http.json(au, headers={"X-Requested-With": "XMLHttpRequest"})
            if "results" not in j:
                raise ValueError(f"risposta search-filter senza 'results' da {au}")
            for a in soup_of(j["results"]).find_all("a", href=True):
                u = abs_url(au, a["href"])
                if u:
                    u = u.split("?")[0]  # il plugin aggiunge ?sf_action=get_data ai link delle schede
                if u and link_rx.search(u) and not (excl and excl.search(u)):
                    links.setdefault(u)
        if not links:
            raise ValueError("nessuna scheda trovata (search-filter): 0 annunci in vendita?")
        ctx.log(f"{len(links)} schede trovate (search-filter)")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    raw = ctx.http.text(u)
                    details += 1
                    L = parse_detail(raw, u, self.id, L)
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


# ---------------------------------------------------------------- b2_dinamica

_CTA_RE = re.compile(r"\s*DEVI VENDERE.*$", re.I | re.S)


@register("b2_dinamica")
class DinamicaSource(Source):
    """Le schede non hanno <h1> né meta og:title: l'estrattore generico ripiega sul <title> della pagina,
    che aggiunge lo slogan '| Dinamica Immobiliare Trieste' e farebbe risultare 'Trieste' qualunque comune
    (il sito propone anche immobili fuori provincia, es. Latisana/San Giorgio di Nogaro). Titolo, indirizzo
    (con il comune, formato 'Comune - via, civico') e descrizione si riprendono dai blocchi propri del tema.

    Il tema scrive letteralmente 'Prezzo : VENDUTO' al posto del numero per gli immobili già venduti ma
    tenuti online come portfolio (parse_price scarta giustamente "VENDUTO", quindi niente prezzo da
    trovare): qui si marca L.sold così la dashboard non li tratta come annunci attivi senza prezzo."""

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            html = ctx.http.text(start)
            for a in soup_of(html).find_all("a", href=True):
                u = abs_url(start, a["href"])
                if u and link_rx.search(u):
                    links.setdefault(u)
        if not links:
            raise ValueError("nessuna scheda trovata")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(u)
                    details += 1
                    L = parse_detail(html, u, self.id, L)
                    soup = soup_of(html)
                    h2 = soup.select_one(".single_property_title h2")
                    if h2:
                        L.title = _unescape(h2.get_text(" ", strip=True))
                    addr_a = soup.select_one(".single_property_title p a")
                    if addr_a:
                        L.address = _unescape(addr_a.get_text(" ", strip=True))
                    paras = [p.get_text(" ", strip=True) for p in soup.select("p.wp-block-paragraph")]
                    if paras:
                        L.description = pu.clean_text(_CTA_RE.sub("", _unescape(" ".join(paras))))
                    if not L.price and pu.detect_sold(soup.get_text(" ", strip=True)):
                        L.sold = True
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


# ---------------------------------------------------------------- b2_vidaligruden (pagina unica, no schede separate)

_VG_EXCLUDE_TITLE = re.compile(r"locazione|affitt|venduto|venduta", re.I)
_VG_PRICE = re.compile(r"prezzo\s*[:.]?\s*([^\n]{1,40})", re.I)


@register("b2_vidaligruden")
class VidaliGrudenSource(Source):
    """Pagina singola (Elementor) con più annunci come blocchi di testo separati da intestazioni
    (h1/h3/h4 'elementor-heading-title'), senza schede/URL dedicati. Ogni blocco diventa un annuncio;
    ref = slug del titolo (stabile finché l'agenzia non lo riformula)."""

    def fetch(self, ctx):
        url = self.cfg.get("page_url", self.cfg["website"])
        html = ctx.http.text(url)
        soup = soup_of(html)
        headings = [h for h in soup.find_all(["h1", "h3"], class_="elementor-heading-title")]
        if not headings:
            raise ValueError("nessuna intestazione annuncio trovata nella pagina")
        out = []
        for i, h in enumerate(headings):
            title = h.get_text(" ", strip=True)
            if i == 0 and len(headings) > 1 and not re.search(r"\d|via |vendita|villa|apparta", title, re.I):
                continue  # intestazione della pagina ("Immobili disponibili"), non un annuncio
            if _VG_EXCLUDE_TITLE.search(title):
                continue
            stop = headings[i + 1] if i + 1 < len(headings) else None
            texts, imgs = [], []
            for el in h.find_all_next(True):
                if stop is not None and el is stop:
                    break
                if el.name == "img":
                    src = abs_url(url, el.get("data-src") or el.get("src"))
                    if src:
                        imgs.append(src)
                elif el.name in ("p", "li", "h4", "h5", "h6") and not el.find(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
                    t = el.get_text(" ", strip=True)
                    if t:
                        texts.append(t)
            body = "\n".join(texts)
            ref = _slugify(title)
            L = Listing(source=self.id, ref=ref, url=f"{url}#{ref}", title=title)
            m = _VG_PRICE.search(body)
            L.price = pu.parse_price(m.group(1)) if m else pu.parse_price(body)
            # la superficie è citata solo nel testo libero ("Circa 110mq calpestabili"): L.mq non veniva
            # mai valorizzato perché questo adattatore non costruisce L. tramite parse_detail/kv_pairs
            # (niente scheda/URL dedicati da cui leggerla).
            L.mq = pu.parse_mq(body)
            L.description = pu.clean_text(body)
            L.images = imgs[:12]
            out.append(L)
        if not out:
            raise ValueError("nessun annuncio in vendita trovato nella pagina (struttura cambiata?)")
        return out
