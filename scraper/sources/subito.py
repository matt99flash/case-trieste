"""Subito.it – solo venditori PRIVATI, provincia di Trieste, tramite l'API di ricerca usata dal sito."""
import json
import re

from .. import parse_utils as pu
from ..models import Listing
from . import register
from .base import Source

API = "https://hades.subito.it/v1/search/items"
CATEGORIES = {"7": "appartamento", "29": "villa", "32": "altro"}   # appartamenti, ville/schiera, loft/mansarde
CONDITION = {"nuovo": "nuovo", "ottimo": "ristrutturato", "ristrutturato": "ristrutturato", "buono": "buono", "da ristrutturare": "da_ristrutturare"}
TOWNS = {"trieste": "Trieste", "muggia": "Muggia", "duino-aurisina": "Duino-Aurisina", "duino aurisina": "Duino-Aurisina",
         "san dorligo della valle": "San Dorligo della Valle", "san dorligo della valle-dolina": "San Dorligo della Valle",
         "sgonico": "Sgonico", "monrupino": "Monrupino"}


@register("subito")
class SubitoSource(Source):
    def fetch(self, ctx):
        out = []
        for cat, default_type in CATEGORIES.items():
            start, total = 0, None
            for _ in range(30):
                params = {"c": cat, "r": "7", "ci": "3", "t": "s", "advt": self.cfg.get("advt", "0"), "qso": "false",
                          "lim": "100", "start": str(start)}
                r = ctx.http.get(API, params=params, headers={"Accept": "application/json", "X-Subito-Channel": "web"})
                j = json.loads(r.content.decode("utf-8"))
                total = j.get("count_all", 0)
                ads = j.get("ads", [])
                out.extend(self._parse(a, default_type) for a in ads)
                start = j.get("start", start + len(ads))
                if not ads or start >= total:
                    break
            ctx.log(f"categoria {cat}: {total} annunci privati")
        # scarta affitti pubblicati per errore nella categoria vendita
        rent = re.compile(r"affitt|in locazione|monthly rent|for rent|al mese|canone mensile|/mese", re.I)
        return [x for x in out if x and not (rent.search(f"{x.title} {x.description}") and (x.price or 0) < 60000)]

    def _parse(self, ad, default_type):
        feats = {f.get("uri"): (f.get("values") or [{}])[0] for f in ad.get("features", [])}
        key = lambda u: feats.get(u, {}).get("key")
        val = lambda u: feats.get(u, {}).get("value")
        urn = ad.get("urn", "")
        parts = urn.split(":")
        ref = parts[4] if len(parts) > 4 else urn
        geo = ad.get("geo") or {}
        town = (geo.get("town") or {}).get("value") or ""
        mp = geo.get("map") or {}
        price = pu.parse_price(key("/price"))
        mq = pu.parse_mq(key("/size"))
        title = ad.get("subject")
        L = Listing(source=self.id, ref=ref, url=(ad.get("urls") or {}).get("default") or "", title=title,
                    price=price, mq=mq, rooms=pu.parse_rooms(val("/room")),
                    bathrooms=pu.parse_small_count(val("/bathrooms"), ["bagni"]), floor=val("/floor"),
                    energy=val("/energy_class") if (val("/energy_class") or "").strip() not in ("", "Non disponibile") else None,
                    description=ad.get("body"), private=True,
                    town=TOWNS.get(town.lower().strip()), zone=(geo.get("zone") or {}).get("value"),
                    address=mp.get("address"))
        if mp.get("latitude"):
            try:
                L.lat, L.lon = float(mp["latitude"]), float(mp["longitude"])
            except (TypeError, ValueError):
                pass
        cond = (val("/building_condition") or "").lower()
        L.condition = next((v for k, v in CONDITION.items() if cond.startswith(k)), None)
        L.type = pu.detect_type(title, ad.get("body"))
        if L.type in ("altro", "box", "commerciale", "terreno"):
            L.type = default_type if default_type != "altro" else L.type
        yes = lambda u: None if key(u) is None else key(u) == "1"
        L.features = {"elevator": yes("/elevator"), "garden": yes("/garden"), "terrace": yes("/balcony"),
                      "garage": None if key("/parking") is None else key("/parking") not in ("0", "")}
        L.features = {k: v for k, v in L.features.items() if v is not None}
        L.images = [i["cdn_base_url"] + "?rule=gallery-desktop-1x-auto" for i in ad.get("images", []) if i.get("cdn_base_url")]
        return L
