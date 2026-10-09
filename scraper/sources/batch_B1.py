"""Adattatori per il batch B1 (agenzie WordPress con plugin custom/non riconosciuti, provincia di Trieste).

La maggior parte dei siti di questo lotto usa temi/plugin "fatti in casa" le cui schede non hanno
tabelle etichetta/valore standard (mq, locali...): i dati stanno solo nel testo della descrizione.
`B1GenericSource` riusa l'adattatore `generic` e, quando l'elenco/scheda non fornisce mq o locali,
li ricava dal testo della descrizione con le stesse utility usate altrove nel progetto.

Alcune agenzie (es. Immobiliare Trieste Centro) chiudono ogni scheda con una frase fissa che nomina
la sede ("Ti aspettiamo nel nostro ufficio in Via X, Trieste"): quella è l'unica menzione di "Trieste"
nel testo, quindi va bene come ultima spiaggia per riconoscere gli annunci di Trieste il cui titolo
non lo dice esplicitamente (es. "SERVOLA", un rione) — ma se il titolo/indirizzo della scheda nomina
esplicitamente una località chiaramente fuori provincia (Tarvisio, Manzano...), quella menzione non deve
essere ignorata a favore della frase fissa. `OUT_TOWNS` elenca i comuni del Friuli Venezia Giulia (e
dintorni) più comuni in questi annunci ma fuori dalla provincia di Trieste: se il titolo/indirizzo li cita
esplicitamente, forziamo `L.town` su quel nome (che `zones.in_province` scarterà) invece di lasciare che
la frase fissa lo faccia risultare erroneamente "Trieste".
"""
import re

from .. import parse_utils as pu
from ..models import Listing
from ..zones import detect_town, detect_zone
from . import register
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of
from .generic import GenericSource

OUT_TOWNS = [
    "Tarvisio", "Manzano", "Cividale del Friuli", "Tarcento", "Buttrio", "Premariacco",
    "Cormons", "Cervignano del Friuli", "Palmanova", "Codroipo", "San Daniele del Friuli",
    "Latisana", "Grado", "Monfalcone", "Staranzano", "Ronchi dei Legionari", "Gradisca d'Isonzo",
    "Gorizia", "Pordenone", "Udine", "Lignano", "Longiarù", "Longiaru", "Divača", "Divaca", "Sežana", "Sezana",
    "Bibione", "Gemona del Friuli", "Gemona", "Portogruaro", "Sappada",
]
_OUT_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in OUT_TOWNS) + r")\b", re.I)

# Frasi che rivelano un annuncio sicuramente fuori provincia anche quando il nome citato coincide per
# omonimia con una parola chiave di zones.py (es. "Prepotto" è sia una frazione di Duino-Aurisina sia un
# comune dei Colli Orientali del Friuli, in provincia di Udine, lontano da Trieste).
_AMBIGUOUS_AWAY_RE = re.compile(r"colli orientali|collio|cividale del friuli", re.I)

_MQ_RE = re.compile(r"(\d{2,4})\s*mq\b", re.I)


def _best_mq(text: str | None) -> int | None:
    """Fallback per i siti che spezzano 'NN m²' in 'NN m' + '2' separati (il sto <sup>2</sup> del quadrato
    finisce su una riga propria): `pu.parse_mq` non riconosce più quella forma nella tabella tecnica, quindi
    si ripiega sulla descrizione. Ma la descrizione spesso cita ANCHE metrature più piccole di pertinenze
    (terrazzo, cantina...): per non prendere la prima citata (spesso la più piccola), si tiene la più grande
    fra quelle in formato 'NN mq', che di norma è la superficie complessiva dell'immobile."""
    if not text:
        return None
    vals = [int(m.group(1)) for m in _MQ_RE.finditer(text)]
    vals = [v for v in vals if 10 <= v <= 5000]
    return max(vals) if vals else pu.parse_mq(text)


def _all_leaf_text(soup, min_len=15) -> str:
    """Testo di TUTTI i blocchi "foglia" della pagina (stessa euristica di `_longest_description` in
    base.py, che però ne tiene solo uno): alcuni temi spezzano la descrizione in tanti paragrafi brevi
    (una frase ciascuno) e la frase con la metratura spesso non è la più lunga, quindi va persa se si
    cerca `_best_mq` solo in `L.description`. Unendo tutti i paragrafi si dà a `_best_mq` il testo
    completo su cui cercare (nessun sito di questo lotto mostra "immobili correlati" con una propria
    metratura sulla stessa pagina, quindi il rischio di prendere un numero di un'altra scheda è basso)."""
    parts = []
    for tag in soup.find_all(["p", "div", "section"]):
        if tag.find(["p", "div", "section", "ul", "table"]):
            continue
        t = tag.get_text(" ", strip=True)
        if len(t) >= min_len:
            parts.append(t)
    return " ".join(parts)


_STRONG_SPAN_RE = re.compile(r"<strong>\s*([^<:]+?)\s*:?\s*</strong>\s*<span>\s*([^<]*?)\s*</span>", re.I)


def _strong_span_kv(html: str) -> dict:
    """Tema WordPress in stile RealHomes/WP-Estate: 'Dettagli Proprietà' è una lista <strong>Etichetta:
    </strong> <span>Valore</span>, che `kv_pairs` (pensato per etichetta/valore su righe di testo separate
    o dt/dd) non riconosce con le etichette italiane di questo tema ('Dimensioni Proprietà', 'Stanze da
    letto'...): si estrae quindi direttamente dall'HTML."""
    return {k.strip().lower(): v.strip() for k, v in _STRONG_SPAN_RE.findall(html)}


def _known_active_count(ctx, source_id: str) -> int:
    """Quanti annunci di questa fonte risultano attivi nell'archivio (giro precedente): vedi
    `B1GenericSource.fetch` e `LabImmobiliareSource.fetch` più sotto."""
    return sum(1 for r in ctx.store.listings.values() if r.get("source") == source_id and r.get("status") == "active")


def _resolve_town(L: Listing) -> None:
    """Come sopra: forza il comune quando titolo/indirizzo/zona bastano a deciderlo da soli, invece di
    lasciare che la descrizione (spesso testo promozionale fisso, non affidabile) decida da sola."""
    hint = " ".join(x for x in (L.title, L.address, L.zone) if x)
    if _AMBIGUOUS_AWAY_RE.search(hint) or (L.description and _AMBIGUOUS_AWAY_RE.search(L.description)):
        L.town = "fuori provincia (Colli Orientali/Collio)"
        return
    if L.town:
        return
    if detect_zone(hint):
        L.town = "Trieste"
        return
    if not detect_town(hint):
        m = _OUT_RE.search(hint)
        if m:
            L.town = m.group(1).title()


_META_DESC_RE = re.compile(r'name="description"\s+content="([^"]*)"', re.I)
_ACF_IMMOBILE_RE = re.compile(r'class="[^"]*\bacf_(mq|bagni|notte|piano|garage)_immobile\b[^"]*"[^>]*>([^<]*)<', re.I)


def _extra_detail_fields(html: str) -> dict:
    """Campi letti da pattern comuni a più temi di questo lotto, non riconosciuti da `kv_pairs` perché
    senza etichetta testuale abbinata al valore:
    - tema WPBakery "Total" (es. Antica Trieste): riquadri <div class="... acf_mq_immobile ...">250m²</div>
      (icona CSS, nessun testo "Superficie");
    - plugin/gestionale condiviso da altre agenzie (es. Contatti Immobiliari, Casacoral): il meta tag
      <meta name="description" content="Vendita - 78 mq - 2 Camere - 1 Bagno"> riepiloga mq/camere/bagni
      in un formato fisso, più affidabile del testo libero della descrizione (che spesso cita ANCHE
      metrature di pertinenze come giardino/terrazzo, prese per errore come superficie dell'immobile
      dall'euristica `_best_mq` se non si legge prima questo dato strutturato)."""
    out = {}
    for key, val in _ACF_IMMOBILE_RE.findall(html):
        out.setdefault(key.lower(), val.strip())
    m = _META_DESC_RE.search(html)
    if m:
        d = m.group(1)
        mm = re.search(r"(\d{1,4})\s*mq\b", d, re.I)
        if mm:
            out.setdefault("mq", mm.group(1) + " mq")
        mm = re.search(r"(\d{1,2})\s*camer", d, re.I)
        if mm:
            out.setdefault("notte", mm.group(1))
        mm = re.search(r"(\d{1,2})\s*bagn", d, re.I)
        if mm:
            out.setdefault("bagni", mm.group(1))
    return out


@register("b1_generic")
class B1GenericSource(GenericSource):
    def fetch(self, ctx):
        known_active = _known_active_count(ctx, self.id)
        # alcuni di questi siti, di tanto in tanto o persino fra una pagina e la successiva dello
        # stesso giro, rispondono 200 ma con la pagina elenco vuota o con qualche scheda in meno
        # (nessun errore HTTP da intercettare, a volte un ordinamento non deterministico): si uniscono
        # (per id) più tentativi finché il totale non raggiunge quello del giro precedente, invece di
        # sostituire un tentativo con un altro (ogni tentativo è un campione diverso, va unito non scelto).
        merged: dict[str, Listing] = {L.id: L for L in super().fetch(ctx)}
        attempts = 0
        while len(merged) < known_active and attempts < 2:
            attempts += 1
            for L in super().fetch(ctx):
                merged.setdefault(L.id, L)
        out = list(merged.values())
        if not out and known_active:
            raise ValueError(f"elenco vuoto dopo {1 + attempts} tentativi (prima c'erano {known_active} annunci attivi)")
        bogus_addr = self.cfg.get("bogus_address")
        for L in out:
            if bogus_addr and L.address == bogus_addr:
                # alcuni siti incorporano un JSON-LD "RealEstateAgent" (che base.parse_detail processa
                # come se fosse l'annuncio, perché il suo @type contiene la sottostringa "RealEstate"):
                # il suo indirizzo è quello della SEDE dell'agenzia, non dell'immobile in vendita.
                L.address = None
            if L.id in ctx.detail_ids and (not L.mq or not L.bathrooms or not L.bedrooms):
                try:
                    raw = ctx.http.text(L.url)
                    extra = _extra_detail_fields(raw)
                    if not L.mq:
                        L.mq = pu.parse_mq(extra.get("mq"))
                    if not L.bathrooms:
                        L.bathrooms = pu.parse_small_count(extra.get("bagni"), ["bagni", "bagno"])
                    if not L.bedrooms:
                        L.bedrooms = pu.parse_small_count(extra.get("notte"), ["camere", "camera"])
                    if not L.mq:
                        L.mq = _best_mq(_all_leaf_text(soup_of(raw)))
                except Exception:
                    pass
            _resolve_town(L)
            if not L.mq:
                L.mq = _best_mq(L.description)
            if not L.rooms:
                L.rooms = pu.parse_rooms(L.description)
        return out


@register("b1_arcasa")
class ArcasaSource(Source):
    """Plugin WordPress 'Arcasa' (agenzia web Arcube): l'archivio pubblico è renderizzato via JS (0 link
    nell'HTML statico), quindi l'elenco si legge dalla sitemap dedicata delle schede immobile (parametro
    YAML `sitemap`). Lo stato dell'annuncio (Vendita/Affitto/Venduto/Affittato) si legge dal badge
    alt="Tipo di contratto" nella pagina scheda: il titolo NON cambia quando un immobile viene venduto,
    quindi va scartato in base a questo campo e non al testo del titolo. L'indirizzo completo (via, rione,
    comune) è in un tag dedicato (class "address" o "arcasa-title-address" a seconda del sito/versione del
    tema) che `kv_pairs` non riconosce come coppia etichetta/valore: senza leggerlo a parte, gli annunci il
    cui titolo non nomina esplicitamente Trieste risulterebbero fuori provincia per mancanza di indizi."""
    STATUS_RE = re.compile(r'alt="Tipo di contratto"[^>]*>([^<]+)<')
    ADDRESS_RE = re.compile(r'class=(?:"address"|arcasa-title-address)[^>]*>(?:\s*<[^>]*>\s*)*([^<]+)<')

    def fetch(self, ctx):
        xml = ctx.http.text(self.cfg["sitemap"])
        urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", xml) if "/immobile/" in u]
        if not urls:
            raise ValueError("sitemap Arcasa vuota")
        ctx.log(f"{len(urls)} schede nella sitemap")
        out = []
        for u in urls:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id):
                try:
                    html = ctx.http.text(u)
                    m = self.STATUS_RE.search(html)
                    if m and m.group(1).strip().lower() != "vendita":
                        continue
                    L = parse_detail(html, u, self.id, L)
                    if not L.address:
                        m = self.ADDRESS_RE.search(html)
                        if m:
                            L.address = m.group(1).strip()
                    _resolve_town(L)
                    if not L.mq:
                        L.mq = _best_mq(L.description)
                    if not L.rooms:
                        L.rooms = pu.parse_rooms(L.description)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("b1_carso")
class CarsoSource(Source):
    """Carso Immobiliare: gli immobili venduti restano pubblicati in /immobili-in-vendita/ con lo stesso
    titolo, segnati solo da <div class="alert alert-danger">Venduto</div> nella scheda; e la località
    ("Dove: <comune o rione>") non è in un formato etichetta/valore che `kv_pairs` riconosce (niente
    dt/dd né 'Dove: ...' sulla stessa riga), quindi va letta a parte con una regex sulla pagina — senza
    la quale molti annunci (es. "Padriciano", "Chiadino", frazioni non citate nel titolo) risulterebbero
    fuori provincia per mancanza di indizi."""
    LIST_URL = "https://carsoimmobiliare.it/immobili-in-vendita/"
    LINK_RE = re.compile(r"^https://carsoimmobiliare\.it/immobile/[^/]+/?$", re.I)
    SOLD_RE = re.compile(r'alert-danger">\s*Venduto', re.I)
    DOVE_RE = re.compile(r">Dove<.*?<[^>]*>\s*([^<]+?)\s*<", re.S)

    def fetch(self, ctx):
        html = ctx.http.text(self.LIST_URL)
        soup = soup_of(html)
        links: dict[str, None] = {}
        for a in soup.find_all("a", href=True):
            u = abs_url(self.LIST_URL, a["href"])
            if u and self.LINK_RE.search(u):
                links.setdefault(u)
        if not links:
            raise ValueError("nessuna scheda trovata in /immobili-in-vendita/")
        ctx.log(f"{len(links)} schede trovate")
        out = []
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id):
                try:
                    h = ctx.http.text(u)
                    if self.SOLD_RE.search(h):
                        continue
                    L = parse_detail(h, u, self.id, L)
                    m = self.DOVE_RE.search(h)
                    if m and not L.zone:
                        L.zone = m.group(1).strip()
                    _resolve_town(L)
                    if not L.mq:
                        L.mq = _best_mq(_all_leaf_text(soup_of(h))) or _best_mq(L.description)
                    if not L.rooms:
                        L.rooms = pu.parse_rooms(L.description)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("b1_audace")
class AudaceSource(Source):
    """Audace Immobiliare: l'archivio '/vendita-immobili-trieste/' è renderizzato via JS (0 link statici),
    quindi l'elenco si legge dalla sitemap del CPT proprietà; la maggior parte delle schede lì elencate
    sono in realtà già vendute (scritto in testa alla descrizione: 'VENDUTO E NON PIÙ DISPONIBILE'), quindi
    vanno scartate in base a quel testo (il resto della pagina/i campi restano quelli dell'ultimo annuncio
    pubblicato, quindi non è un semplice 404 o un redirect da intercettare)."""
    SITEMAP = "https://www.audaceimmobiliare.it/property-sitemap.xml"
    SOLD_RE = re.compile(r"VENDUTO\s+E\s+NON\s+PI[UÙ]\s+DISPONIBILE", re.I)

    def fetch(self, ctx):
        xml = ctx.http.text(self.SITEMAP)
        urls = re.findall(r"<loc>([^<]+)</loc>", xml)
        if not urls:
            raise ValueError("sitemap proprietà vuota")
        ctx.log(f"{len(urls)} schede nella sitemap")
        out = []
        for u in urls:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id):
                try:
                    html = ctx.http.text(u)
                    if self.SOLD_RE.search(html):
                        continue
                    L = parse_detail(html, u, self.id, L)
                    fields = _strong_span_kv(html)
                    if not L.mq:
                        L.mq = pu.parse_mq(fields.get("dimensioni proprietà")) or _best_mq(L.description)
                    if not L.bedrooms:
                        L.bedrooms = pu.parse_small_count(fields.get("stanze da letto"), ["camere"])
                    _resolve_town(L)
                    if not L.rooms:
                        L.rooms = pu.parse_rooms(L.description)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("b1_lab")
class LabImmobiliareSource(Source):
    """Laboratorio Immobiliare: solo 3 schede, tutte linkate da /vendite/. La 'Scheda tecnica' (Oxygen
    Builder) è una sequenza di <span> senza etichette testuali (icone Font Awesome per camere/bagni, "mq"
    come span separato dal numero), quindi `kv_pairs` non la riconosce: prezzo, mq, camere e bagni si
    estraggono con una regex sull'HTML."""
    LIST_URL = "https://www.labimmobiliare.it/vendite/"
    LINK_RE = re.compile(r"^https://www\.labimmobiliare\.it/vendite/[^/]+/?$", re.I)
    PRICE_RE = re.compile(r'ct-span"\s*>\s*([\d.,]+)\s*€', re.S)
    MQ_RE = re.compile(r'ct-span"\s*>\s*(\d+)\s*<[^>]*></div>\s*<div[^>]*>\s*<span[^>]*class="ct-span"\s*>\s*mq\s*<', re.S)
    BED_RE = re.compile(r'ct-span"\s*>\s*(\d+)\s*<.{0,80}?icon-bed', re.S)
    BATH_RE = re.compile(r'ct-span"\s*>\s*(\d+)\s*<.{0,80}?icon-bath', re.S)

    def _list_links(self, ctx):
        html = ctx.http.text(self.LIST_URL)
        soup = soup_of(html)
        links: dict[str, None] = {}
        for a in soup.find_all("a", href=True):
            u = abs_url(self.LIST_URL, a["href"])
            if u and self.LINK_RE.search(u):
                links.setdefault(u)
        if not links:
            # diagnostica per il messaggio d'errore finale: se capita di nuovo (es. solo sui runner
            # GitHub Actions) si vede subito cos'ha risposto il sito invece di doverlo riprodurre a mano.
            self._last_empty_hint = html[:200].replace("\n", " ").strip()
        return links

    def fetch(self, ctx):
        links = self._list_links(ctx)
        attempts = 0
        # a volte la pagina risponde 200 ma senza schede (hiccup del sito, non un errore HTTP):
        # un nuovo tentativo quasi sempre la recupera per intero.
        while not links and attempts < 2:
            attempts += 1
            links = self._list_links(ctx)
        if not links:
            hint = getattr(self, "_last_empty_hint", "")
            raise ValueError(f"nessuna scheda trovata in /vendite/ dopo {1 + attempts} tentativi" + (f" [{hint}]" if hint else ""))
        ctx.log(f"{len(links)} schede trovate")
        out = []
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id):
                try:
                    h = ctx.http.text(u)
                    L = parse_detail(h, u, self.id, L)
                    if not L.price:
                        m = self.PRICE_RE.search(h)
                        if m:
                            L.price = pu.parse_price(m.group(1))
                    if not L.mq:
                        m = self.MQ_RE.search(h)
                        L.mq = pu.parse_mq(m.group(1)) if m else _best_mq(L.description)
                    if not L.bedrooms:
                        m = self.BED_RE.search(h)
                        if m:
                            L.bedrooms = int(m.group(1))
                    if not L.bathrooms:
                        m = self.BATH_RE.search(h)
                        if m:
                            L.bathrooms = int(m.group(1))
                    _resolve_town(L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("b1_nest")
class NestSource(Source):
    """NEST Immobiliare (plugin 'Real Estate Manager'): niente archivio pubblico distinto vendita/affitto,
    l'elenco completo si legge dalla sitemap del post type rem_property; gli annunci di affitto si
    riconoscono dalla classe CSS 'rem_property_tag-*affitto*' sulla pagina scheda (nessun'altra tag
    o campo distingue vendita/affitto su questo sito).

    La "Scheda tecnica" (tema Elementor) mostra mq/camere/bagni come riquadri icona+numero senza
    etichetta testuale (un'icona SVG Font Awesome seguita da uno <span class="rem-field-value"> col solo
    numero, es. l'icona "bed" seguita da "2"): `kv_pairs` non li riconosce perché non c'è alcuna etichetta
    da abbinare al valore, quindi si leggono appaiando ogni icona (bed/bath/square) al valore immediatamente
    successivo nell'HTML."""
    SITEMAP = "https://nestimmobiliare.com/wp-sitemap-posts-rem_property-1.xml"
    RENT_RE = re.compile(r"rem_property_tag-[a-z-]*affitto", re.I)
    ICON_FIELD_RE = re.compile(r'e-fa[rs]-(bed|bath|square)"[\s\S]*?rem-field-value">([^<]*)</span>')

    def fetch(self, ctx):
        xml = ctx.http.text(self.SITEMAP)
        urls = re.findall(r"<loc>([^<]+)</loc>", xml)
        if not urls:
            raise ValueError("sitemap rem_property vuota")
        ctx.log(f"{len(urls)} schede nella sitemap")
        out = []
        for u in urls:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id):
                try:
                    html = ctx.http.text(u)
                    if self.RENT_RE.search(html):
                        continue
                    L = parse_detail(html, u, self.id, L)
                    icons = dict(self.ICON_FIELD_RE.findall(html))
                    if not L.mq:
                        L.mq = pu.parse_mq(icons.get("square"))
                    if not L.bedrooms:
                        L.bedrooms = pu.parse_small_count(icons.get("bed"), ["camere", "camera"])
                    if not L.bathrooms:
                        L.bathrooms = pu.parse_small_count(icons.get("bath"), ["bagni", "bagno"])
                    _resolve_town(L)
                    if not L.mq:
                        L.mq = _best_mq(L.description)
                    if not L.rooms:
                        L.rooms = pu.parse_rooms(L.description)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out
