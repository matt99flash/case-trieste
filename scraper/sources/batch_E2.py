"""Adattatori specifici per il batch E2 (agenzie di Trieste senza pagina annunci nota).

Ogni classe si registra con prefisso ``e2_`` per non collidere con altri batch.
"""
import re

from .. import parse_utils as pu
from ..models import Listing
from . import register
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of
from .generic import GenericSource

# ---------------------------------------------------------------- RN Immobiliare (Morgan srl)
# Pagina /compra: sito Wix "a mano" (non un dataset/repeater), ogni scheda e' un blocco di
# rich-text libero (h3 con nome+numero civico+mq+locali, seguito da un h3 col solo prezzo).
# Un annuncio "in evidenza" ha nome e dettagli spezzati/riordinati; un annuncio e' in
# collaborazione con un'altra agenzia (link esterno) e va escluso; "venduto" va escluso.

_DETAIL_RE = re.compile(r"^(?:(?P<name>.+?)\s+)?(?P<mq>\d{2,4})\s*m2\b\s*(?P<rest>.*)$", re.S)
_PRICE_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_SKIP_SUBSTR = (
    "i nostri agenti immobiliari", "novità dal nord est", "compra casa a trieste",
    "in tendenza", "i nostri immobili", "vuoi investire con noi", "scopri di più",
    "in collaborazione", "p.iva", "rea n.", "developed by", "cookie policy", "privacy policy",
)


@register("e2_rn_immobiliare")
class RNImmobiliareSource(Source):
    def fetch(self, ctx):
        base = self.cfg["website"].rstrip("/")
        url = self.cfg.get("start_url", base + "/compra")
        html = ctx.http.text(url)
        soup = soup_of(html)
        out = []
        pending = None  # {name, mq, rest, sold, ext}
        for el in soup.find_all(["h3", "p", "a"]):
            if el.name == "a":
                href = (el.get("href") or "").strip()
                if pending is not None and href.startswith("http") and "rnimmobiliare.com" not in href:
                    pending["ext"] = True
                continue
            txt = el.get_text(" ", strip=True)
            if not txt:
                continue
            low = txt.lower()
            if el.name == "p":
                if "venduto" in low and pending is not None:
                    pending["sold"] = True
                continue
            if any(s in low for s in _SKIP_SUBSTR):
                continue
            if _PRICE_RE.match(txt.replace(" ", "")):
                if pending is not None:
                    L = self._flush(pending, txt, url)
                    if L:
                        out.append(L)
                    pending = None
                continue
            m = _DETAIL_RE.match(txt)
            if m:
                if pending is not None:  # scheda precedente senza prezzo indicato sul sito
                    L = self._flush(pending, None, url)
                    if L:
                        out.append(L)
                pending = {"name": m.group("name"), "mq": m.group("mq"), "rest": m.group("rest"),
                           "sold": False, "ext": False}
                continue
            if pending is not None and pending["name"] is None and len(txt) < 40:
                pending["name"] = txt
        if pending is not None:
            L = self._flush(pending, None, url)
            if L:
                out.append(L)
        if not out:
            raise ValueError("nessun annuncio in vendita trovato su rnimmobiliare.com/compra")
        return out

    def _flush(self, p, price_txt, url):
        if p["sold"] or p["ext"] or not p["name"]:
            return None
        name = p["name"].strip()
        ref = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "listing"
        rest = p["rest"] or ""
        rooms_m = re.search(r"(\d+)\s*stanz", rest, re.I)
        bath_m = re.search(r"(\d+)\s*bagn", rest, re.I)
        L = Listing(source=self.id, ref=ref, url=url, title=name,
                    price=pu.parse_price(price_txt) if price_txt else None,
                    mq=pu.parse_mq(p["mq"] + " mq"),
                    rooms=pu.parse_rooms(rooms_m.group(1)) if rooms_m else None,
                    bathrooms=pu.parse_small_count(bath_m.group(1), ["bagni"]) if bath_m else None,
                    description=rest or None, town="Trieste")
        L.type = "appartamento"
        return L


# ---------------------------------------------------------------- Universalcasa (gestionale Gestim)
# Il prezzo/mq compaiono solo come testo libero "€ 560.000 - Mq 300" in .sottotitolo:
# kv_pairs() generico non lo riconosce, quindi lo recuperiamo qui con una regex mirata.

_SOTTOTITOLO_MQ_RE = re.compile(r"sottotitolo[^>]*>\s*[^<]*?\bMq\s*([\d.]+)", re.I)


@register("e2_universalcasa")
class UniversalcasaSource(GenericSource):
    def fetch(self, ctx):
        out = super().fetch(ctx)
        for L in out:
            if L.mq is None and L.id in ctx.detail_ids:
                try:
                    html = ctx.http.text(L.url)
                except Exception:
                    continue
                m = _SOTTOTITOLO_MQ_RE.search(html)
                if m:
                    L.mq = pu.parse_mq(m.group(1) + " mq")
        return out
