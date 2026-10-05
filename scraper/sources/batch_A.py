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
from ..zones import detect_town
from . import register
from .base import Source, abs_url, kv_pairs, parse_detail, ref_from_url, soup_of
from .generic import GenericSource


def _unescape(s):
    return htmlmod.unescape(s) if isinstance(s, str) else s


_SOLD_TITLE = re.compile(r"^\s*vendut[oa]\b", re.I)


def _is_sold(L: Listing) -> bool:
    """Alcune agenzie tengono online, come portfolio/referenze, schede di immobili già venduti,
    marcandole nel titolo stesso ('VENDUTO – ...', 'VENDUTA – ...') invece di rimuoverle o segnarle
    con un campo 'contratto'/'stato' che `parse_detail` (base.py) riconoscerebbe: da scartare come le
    schede d'affitto."""
    return bool(_SOLD_TITLE.match(L.title or ""))


_JSONLD_RENT = re.compile(r'"availability"\s*:\s*"[^"]*InLocazione', re.I)


def _is_jsonld_rent(html: str) -> bool:
    """Alcuni plugin (MyHouse Real Estate: un tema Elementor "real estate") scrivono lo stato del
    contratto solo nel JSON-LD, come valore custom (non standard schema.org) del campo 'availability'
    dell'Offer ("https://schema.org/InLocazione" per l'affitto), che `parse_detail` (base.py) non
    controlla: cerca invece un campo 'contratto' testuale (`kv_pairs`) che qui non esiste, quindi la
    scheda d'affitto non viene scartata come le altre (e il canone mensile, troppo basso per essere un
    prezzo di vendita plausibile, fa fallire `parse_price` e scivolare su un fallback che pesca un
    numero a caso altrove in pagina)."""
    return bool(_JSONLD_RENT.search(html))



# Comuni del Friuli Venezia Giulia (e dintorni) FUORI provincia di Trieste, incontrati in questo batch
# nei titoli di agenzie che operano anche altrove: non fanno parte di `zones.TOWNS` (che elenca solo la
# provincia di Trieste), quindi `detect_town` non li riconosce e non potrebbe smentire un campo
# 'comune' della scheda (kv_pairs) che dice erroneamente 'Trieste' (vedi `_fix_town`).
_OUT_OF_PROVINCE_TOWN = re.compile(
    r"\bturriaco\b|\bgorizia\b|\bmonfalcone\b|\bstaranzano\b|\bronchi dei legionari\b|\bcormons\b|"
    r"\bcervignano\b|\bgrado\b|\budine\b|\bpordenone\b|\bcodroipo\b|\bsocchieve\b", re.I)


def _fix_town(L: Listing):
    """Alcuni temi hanno un campo 'comune' nella scheda (letto da `kv_pairs`) che in realtà è
    un'etichetta generica dell'agenzia (es. sempre 'Trieste' anche per immobili fuori città, come
    Sistiana/Duino-Aurisina), quindi meno affidabile del titolo, che di solito nomina la località
    specifica. Se il titolo indica un comune (minore) diverso da quello già impostato, ha la
    precedenza (`detect_town`, zones.py, è di sola lettura: dà priorità ai comuni minori su Trieste)."""
    t = detect_town(L.title)
    if t and t != L.town:
        L.town = t


def _is_out_of_province_title(L: Listing) -> bool:
    """Il titolo nomina esplicitamente un comune del FVG fuori provincia di Trieste, non presente in
    `zones.TOWNS` (quindi `detect_town` non lo riconosce e non può escluderlo da solo): scarta la
    scheda subito, invece di lasciarle attraversare `finalize()` (models.py, condiviso), che senza un
    comune già impostato ripiega su `detect_town(descrizione)` e può 'ritrovare' un falso 'Trieste'
    nel testo standard con cui l'agenzia si presenta (es. "...immobiliare a Trieste e nei dintorni"),
    anche per un immobile altrove."""
    return bool(_OUT_OF_PROVINCE_TOWN.search(L.title or ""))


# Sottoinsieme delle parole chiave di ZONES_TRIESTE (zones.py, condiviso) abbastanza distintive da
# usare come prova che il comune è Trieste: `ZONES_TRIESTE` include anche nomi di vie/piazze comuni a
# moltissime città italiane (es. "corso italia", "piazza garibaldi", "via mazzini", "san giacomo",
# "san vito"), che hanno causato falsi positivi su immobili fuori provincia (es. un "Corso Italia" a
# Gorizia proposto dalla stessa agenzia). Qui si tengono solo i rioni/microtoponimi che non hanno
# quasi omonimi altrove.
_TRIESTE_SAFE_ZONE = re.compile(
    r"\b(?:roiano|chiarbola|gretta|altura|rozzol|chiadino|melara|scorcola|cologna|opicina|villa opicina|"
    r"banne|trebiciano|padriciano|basovizza|contovello|servola|valmaura|borgo san sergio|guardiella|"
    r"barcola|grignano|miramare|ponziana|settefontane|borgo teresiano|ponterosso|citt[aà] vecchia|"
    r"san giusto|borgo giuseppino|cavana|montebello|barriera vecchia|cattinara|longera|gropada|"
    r"conconello|borgo grotta gigante|prosecco|santa croce di trieste)\b", re.I)


def _infer_town(L: Listing):
    """Molte schede citano solo il rione (es. 'Roiano', 'Chiarbola', 'Altura') senza mai scrivere
    'Trieste' esplicitamente, quindi `detect_town` (zones.py, condiviso) non lo trova e l'annuncio
    finirebbe escluso come fuori provincia. Se il testo nomina uno dei rioni di `_TRIESTE_SAFE_ZONE`,
    il comune è Trieste (elenco più ristretto di `ZONES_TRIESTE`/`detect_zone`, che include anche nomi
    generici comuni ad altre città: vedi il commento sopra)."""
    if L.town:
        return
    blob = " ".join(x for x in (L.title, L.address, L.zone, L.description) if x)
    if _TRIESTE_SAFE_ZONE.search(blob):
        L.town = "Trieste"


_MQ_STRAY = re.compile(r"(\d{1,4})\s*m\s*(?:<sup>\s*2\s*</sup>|²|2\b)", re.I)
# Houzez IT: "Superficie/Dimensione proprietà:</strong> 55 " (a volte senza unità di misura), che
# `kv_pairs` (base.py, condiviso) salta perché l'etichetta non è l'esatto 'superficie' previsto da
# LABELS. Quando la troviamo è un valore affidabile: sovrascrive anche un L.mq già valorizzato, perché
# capita che kv_pairs abbia abbinato per sbaglio etichetta e valore sbagliati (es. l'anno di
# costruzione letto come superficie, cfr. Immobiliare MET[R]ICA).
_MQ_LABELED = re.compile(
    r"(?:(?:Superficie|Dimensione)\s+propriet\S*|Superficie)\s*:?\s*</strong>\s*(?:\n\s*)?(?:<span>)?\s*(\d{1,4})\b"
    r'|class="h-area"[^>]*>\s*<span[^>]*>\s*(\d{1,4})\b'
    r'|<strong>\s*(\d{1,4})\s*</strong></li>\s*<li[^>]*class="hz-meta-label h-area"',
    re.I)
# Divi Machine (Minimal Re): valore e unità sono in due <span> distinti e adiacenti, es.
# '<span>87</span></div><div><span>mq</span>' (nessun testo li unisce, kv_pairs non li abbina).
_MQ_ADJACENT_SPAN = re.compile(r"<span[^>]*>\s*(\d{1,4})\s*</span></div>\s*<div[^>]*>\s*<span[^>]*>\s*mq\s*</span>", re.I)
# Divi Builder (Armony Immobiliare): l'etichetta è sulla stessa riga del valore ma separata da un PUNTO
# invece dei due punti, es. "DUINO n. 75/O Prezzo € 199.000 Mq. 81 Camere 1 Bagni 1": `kv_pairs` (base.py)
# riconosce "Mq"/"Superficie" come etichetta solo con ":" o su una riga a parte seguita dal valore sulla
# riga successiva, quindi qui non trova nulla (il meta tag og:description, generato dallo stesso tema, ha
# anche uno shortcode ACF ("[acf field=\"mq\"]") rimasto non processato: per questo si cerca nell'HTML
# il solo pattern "Mq. NN" col punto, mai nel testo libero della descrizione dove "mq" compare spesso
# anche per pertinenze).
_MQ_PERIOD_LABEL = re.compile(r"\bMq\.?\s*(\d{1,4})\b", re.I)


def _fix_mq(L: Listing, html: str):
    """Recupera la superficie quando il tema spezza 'm²' in 'm' + '<sup>2</sup>' o quando l'etichetta
    non è quella esatta riconosciuta da `kv_pairs` (in base.py/parse_utils.py, condivisi, da non
    toccare): `parse_mq` non riconosce '133 m' (suffisso 'mq'/'m²'/'m2' non attaccato al numero), e
    `kv_pairs` salta 'Superficie/Dimensione proprietà:' perché l'etichetta non è l'esatto 'superficie'.
    Cerca entrambi i pattern nell'HTML grezzo, dove il tag <sup> e l'etichetta originale sono ancora
    presenti; il pattern con etichetta esplicita (`_MQ_LABELED`) ha la priorità e può correggere anche
    un valore già presente ma sbagliato.

    Corregge anche un bug ricorrente in più temi di questo batch (Estatik, RealHomes-like...): quando
    il prezzo è scritto con la virgola come separatore delle migliaia (es. '€229,000'), `kv_pairs` a
    volte lo abbina per sbaglio alla chiave 'mq' invece che 'price' (a volte pescando persino il prezzo
    di un ANNUNCIO CORRELATO mostrato altrove nella stessa pagina, quindi nemmeno riconducibile al
    prezzo giusto), e il fallback di `parse_detail` (base.py) legge "229,000 mq" come 229 (la ",000"
    viene presa per una parte decimale e scartata). Il segno di questo bug è che la stringa originale
    (ancora nell'HTML) contiene un simbolo di valuta o la stessa virgola delle migliaia: va richiamata
    DOPO `_fix_price`, quando L.price è già stato corretto, così da poter confrontare anche mq*1000."""
    if L.mq and L.price and L.mq * 1000 == L.price:
        L.mq = None
    if L.mq:
        raw_mq = kv_pairs(soup_of(html)).get("mq") or ""
        if re.search(r"[€$]|,\d{3}\b", raw_mq):
            L.mq = None
    m = _MQ_LABELED.search(html)
    if m:
        v = int(next(g for g in m.groups() if g))
        if 5 <= v <= 2000:
            L.mq = v
            return
    m = _MQ_ADJACENT_SPAN.search(html)
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
            return
    # plugin "real estate" su Elementor (MyHouse): JSON-LD con un array 'additionalProperty' invece del
    # campo standard 'floorSize' che base.py sa leggere, es. {"name":"Area Size","value":"83","unitText":"Mq"}
    m = re.search(r'"Area\s*Size"\s*,\s*"value"\s*:\s*"?(\d{1,4})', html, re.I)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 2000:
            L.mq = v
            return
    # Houzez, variante "Superficie <br> 133 + 2" (superficie principale + accessori, es. terrazza):
    # kv_pairs prende l'intera stringa "133 + 2" ma parse_mq non la riconosce (nessuna unità attaccata
    # al numero); il primo numero è la superficie abitabile.
    raw_mq = kv_pairs(soup_of(html)).get("mq") or ""
    m = re.match(r"\s*(\d{1,4})\s*\+\s*\d", raw_mq)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 2000:
            L.mq = v
            return
    m = _MQ_PERIOD_LABEL.search(html)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 2000:
            L.mq = v
            return
    # ultima spiaggia: 'NN mq' scritto in chiaro nella descrizione (parse_detail/finalize provano solo
    # kv_pairs e il titolo, mai la descrizione).
    L.mq = pu.parse_mq(L.description or "")


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


_WPR_PRICE = re.compile(r'price-single-listing-text">\s*([^<]*?)\s*<', re.I)


def _fix_related_widget_price(L: Listing, html: str):
    """Tema WP Residence: quando l'unico elemento con classe 'price-single-listing-text' (il prezzo
    VERO della scheda, es. '<li class="item-price item-price-text
    price-single-listing-text">275.000</li>') è vuoto o '0' (prezzo su richiesta), lo si azzera: altre
    classi CSS simili ('item-price', 'item-price-wrap') usate nella stessa pagina sono condivise anche
    da un widget "immobili correlati/visti di recente" che mostra prezzi di TUTT'ALTRI annunci, quindi
    non affidabili come alternativa. Non fa nulla sui temi che non hanno questo elemento (es. Houzez):
    lì il segno del bug è invece che LO STESSO prezzo si ripete su più schede diverse, corretto a parte
    da `_dedupe_suspicious_prices` dopo aver raccolto tutti gli annunci della fonte."""
    m = _WPR_PRICE.search(html)
    if not m:
        return
    raw = m.group(1).strip()
    if not raw or raw == "0":
        L.price = None
    else:
        v = pu.parse_price(raw) or pu.parse_price(raw.replace(",", "."))
        if v:
            L.price = v


def _dedupe_suspicious_prices(items: list[Listing]):
    """Alcuni temi (Houzez, variante vista in NoiDonneImmobiliare.eu) mostrano, quando il prezzo della
    scheda non è impostato, il prezzo di un annuncio a caso pescato da un widget "immobili
    correlati/visti di recente" presente sulla stessa pagina: il segno distintivo è che lo STESSO
    prezzo compare identico su annunci altrimenti scorrelati (titoli/indirizzi diversi) della stessa
    fonte. Un prezzo genuino condiviso da 3+ annunci della stessa agenzia nello stesso giro è
    estremamente improbabile (i prezzi non sono mai arrotondati in modo identico per caso), quindi lo si
    azzera. Va chiamata sull'intero elenco `out` prima di restituirlo da `fetch`."""
    from collections import Counter
    counts = Counter(L.price for L in items if L.price)
    for L in items:
        if L.price and counts[L.price] >= 3:
            L.price = None


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
        excl_content = re.compile(cfg["exclude_content_regex"], re.I | re.S) if cfg.get("exclude_content_regex") else None
        max_pages = cfg.get("max_pages", 40)
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            url, page, visited = start, cfg.get("page_start", 2), set()
            for _ in range(max_pages):
                if not url or url in visited:
                    break
                visited.add(url)
                try:
                    html = ctx.http.text(url)
                except Exception as e:
                    # pagina successiva inesistente (404): l'elenco è finito. Se è la prima pagina, è un errore vero.
                    if len(visited) > 1 and "404" in str(e):
                        break
                    raise
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
                    if excl_content and excl_content.search(html):
                        continue
                    L = parse_detail(html, u, self.id, L)
                    if cfg.get("require_regex") and not re.search(cfg["require_regex"], html, re.I):
                        continue
                    L.title = _unescape(L.title)
                    L.description = _unescape(L.description)
                    _fix_price(L, html)
                    _fix_related_widget_price(L, html)
                    _fix_mq(L, html)
                    if L.features.pop("_rent", False) or _is_sold(L) or _is_jsonld_rent(html) or _is_out_of_province_title(L):
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
        _dedupe_suspicious_prices(out)
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
                    _fix_price(L, html)
                    _fix_related_widget_price(L, html)
                    _fix_mq(L, html)
                    self._augment(L, html)
                    if L.features.pop("_rent", False) or _is_sold(L) or _is_jsonld_rent(html) or _is_out_of_province_title(L):
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
        _dedupe_suspicious_prices(out)
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
                try:
                    html = ctx.http.text(url)
                except Exception as e:
                    # pagina successiva inesistente (404): l'elenco è finito. Se è la prima pagina, è un errore vero.
                    if len(visited) > 1 and "404" in str(e):
                        break
                    raise
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
                    _fix_price(L, html)
                    _fix_related_widget_price(L, html)
                    _fix_mq(L, html)
                    if L.features.pop("_rent", False) or _is_sold(L) or _is_jsonld_rent(html) or _is_out_of_province_title(L):
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
        _dedupe_suspicious_prices(out)
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
                    _fix_price(L, html)
                    _fix_related_widget_price(L, html)
                    _fix_mq(L, html)
                    if L.features.pop("_rent", False) or _is_sold(L) or _is_jsonld_rent(html) or _is_out_of_province_title(L):
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
        _dedupe_suspicious_prices(out)
        return out
