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
from .base import Source, parse_detail, ref_from_url
from .generic import GenericSource

OUT_TOWNS = [
    "Tarvisio", "Manzano", "Cividale del Friuli", "Tarcento", "Buttrio", "Premariacco",
    "Cormons", "Cervignano del Friuli", "Palmanova", "Codroipo", "San Daniele del Friuli",
    "Latisana", "Grado", "Monfalcone", "Staranzano", "Ronchi dei Legionari", "Gradisca d'Isonzo",
    "Gorizia", "Pordenone", "Udine", "Lignano", "Longiarù", "Longiaru", "Divača", "Divaca", "Sežana", "Sezana",
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


@register("b1_generic")
class B1GenericSource(GenericSource):
    def fetch(self, ctx):
        out = super().fetch(ctx)
        bogus_addr = self.cfg.get("bogus_address")
        for L in out:
            if bogus_addr and L.address == bogus_addr:
                # alcuni siti incorporano un JSON-LD "RealEstateAgent" (che base.parse_detail processa
                # come se fosse l'annuncio, perché il suo @type contiene la sottostringa "RealEstate"):
                # il suo indirizzo è quello della SEDE dell'agenzia, non dell'immobile in vendita.
                L.address = None
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
    quindi va scartato in base a questo campo e non al testo del titolo."""
    STATUS_RE = re.compile(r'alt="Tipo di contratto"[^>]*>([^<]+)<')

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


@register("b1_nest")
class NestSource(Source):
    """NEST Immobiliare (plugin 'Real Estate Manager'): niente archivio pubblico distinto vendita/affitto,
    l'elenco completo si legge dalla sitemap del post type rem_property; gli annunci di affitto si
    riconoscono dalla classe CSS 'rem_property_tag-*affitto*' sulla pagina scheda (nessun'altra tag
    o campo distingue vendita/affitto su questo sito)."""
    SITEMAP = "https://nestimmobiliare.com/wp-sitemap-posts-rem_property-1.xml"
    RENT_RE = re.compile(r"rem_property_tag-[a-z-]*affitto", re.I)

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
