"""Costruttori: pagine di un cantiere con tabella/elenco delle unità (mq, prezzo, venduto/disponibile).

adapter: unit_table
  pages: [{url, project, town?, zone?}]            pagine di cantiere note
  discover: {url, link_regex, exclude_regex?}      opzionale: trova nuove pagine di cantiere da un indice

adapter: project_page
  Una scheda per cantiere quando il sito non ha un listino per unità ("a partire da € ...").
  pages: [{url, project, town?, zone?, type?}]
"""
import hashlib
import re

from .. import parse_utils as pu
from ..models import Listing
from . import register
from .base import Source, abs_url, meta, soup_of, _longest_description

SOLD = re.compile(r"vendut|sold|compromesso|opzionat|prenotat|riservat", re.I)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60]


def discover_pages(ctx, cfg):
    """Pagine di cantiere configurate + quelle trovate automaticamente da una pagina indice."""
    pages = list(cfg.get("pages", []))
    disc = cfg.get("discover")
    if disc:
        known = {p["url"].rstrip("/") for p in pages}
        rx = re.compile(disc["link_regex"], re.I)
        ex = re.compile(disc["exclude_regex"], re.I) if disc.get("exclude_regex") else None
        for idx in disc["url"] if isinstance(disc["url"], list) else [disc["url"]]:
            soup = soup_of(ctx.http.text(idx))
            for a in soup.find_all("a", href=True):
                u = abs_url(idx, a["href"])
                if u and rx.search(u) and not (ex and ex.search(u)) and u.rstrip("/") not in known:
                    known.add(u.rstrip("/"))
                    pages.append({"url": u, "project": None, **disc.get("defaults", {})})
    return pages


@register("unit_table")
class UnitTableSource(Source):
    def fetch(self, ctx):
        pages = discover_pages(ctx, self.cfg)
        out = []
        for p in pages:
            try:
                html = ctx.http.text(p["url"])
            except Exception as e:
                ctx.errors.append(f"{p['url']}: {e}")
                continue
            out += self._units(ctx, p, html)
        return out

    def _units(self, ctx, p, html):
        soup = soup_of(html)
        project = p.get("project") or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else meta(soup, "og:title"))
        img = meta(soup, "og:image")
        desc = _longest_description(soup)
        out = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            header = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            if not any("mq" in h or "superficie" in h for h in header) or not any("prezzo" in h or "€" in h for h in header):
                continue
            col = lambda *names: next((i for i, h in enumerate(header) if any(n in h for n in names)), None)
            c_unit, c_mq, c_rooms = col("ente", "unità", "unita", "app", "alloggio", "interno"), col("mq", "superficie"), col("locali", "vani")
            c_floor, c_price, c_state = col("piano"), col("prezzo", "€"), col("stato", "disponib")
            for tr in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) < len(header) - 1:
                    continue
                g = lambda i: cells[i] if i is not None and i < len(cells) else None
                unit = g(c_unit) or str(len(out) + 1)
                state = " ".join(x for x in (g(c_state), g(c_price)) if x)
                sold = bool(SOLD.search(state)) and not re.search(r"disponibil", g(c_state) or "", re.I)
                L = Listing(source=self.id, ref=f"{_slug(project)}-{_slug(unit)}", url=p["url"],
                            title=f"{project} – unità {unit}", price=pu.parse_price(g(c_price)),
                            mq=pu.parse_mq((g(c_mq) or "") + (" mq" if "mq" not in (g(c_mq) or "") else "")),
                            rooms=pu.parse_rooms(g(c_rooms)), floor=g(c_floor), condition="nuovo",
                            town=p.get("town"), zone=p.get("zone"), description=desc, images=[img] if img else [],
                            sold=sold)
                L.type = p.get("type") or "appartamento"
                if L.price is None and L.mq is None:
                    continue  # riga senza dati (tabelle duplicate per mobile, note)
                if sold and not ctx.store.known(L.id):
                    continue  # già venduto quando l'abbiamo visto la prima volta: non interessa
                out.append(L)
        # elenco testuale tipo "App. 3 – piano 2 – 85 mq – € 460.000" (+ VENDUTO)
        if not out and self.cfg.get("text_units"):
            text = soup.get_text("\n")
            for m in re.finditer(self.cfg["text_units"], text, re.I | re.S):
                gd = m.groupdict()
                unit = gd.get("unit") or str(len(out) + 1)
                sold = bool(gd.get("sold")) or pu.parse_price(gd.get("price")) is None
                L = Listing(source=self.id, ref=f"{_slug(project)}-{_slug(unit)}-{gd.get('mq') or ''}", url=p["url"],
                            title=f"{project} – {unit}", price=pu.parse_price(gd.get("price")), mq=pu.parse_mq(gd.get("mq")),
                            floor=gd.get("floor"), condition="nuovo", town=p.get("town"), zone=p.get("zone"),
                            description=desc, images=[img] if img else [], sold=sold)
                L.type = p.get("type") or "appartamento"
                if sold and not ctx.store.known(L.id):
                    continue
                out.append(L)
        return out


@register("project_page")
class ProjectPageSource(Source):
    def fetch(self, ctx):
        out = []
        for p in discover_pages(ctx, self.cfg):
            try:
                html = ctx.http.text(p["url"])
            except Exception as e:
                ctx.errors.append(f"{p['url']}: {e}")
                continue
            soup = soup_of(html)
            text = soup.get_text(" ", strip=True)
            price_rx = self.cfg.get("price_regex", r"€\s*\d{1,3}(?:\.\d{3})+|\d{1,3}(?:\.\d{3})+\s*€")
            prices = [v for v in (pu.parse_price(m.group(m.lastindex or 0)) for m in re.finditer(price_rx, text, re.I)) if v and v >= 50_000]
            h1 = soup.find("h1")
            name = p.get("project")
            if not name and self.cfg.get("name_from_url"):
                m = re.search(self.cfg["name_from_url"], p["url"])
                name = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", m.group(1)) if m else None
            name = name or (h1.get_text(" ", strip=True) if h1 else None) or meta(soup, "og:title")
            address = None
            if self.cfg.get("address_regex"):
                m = re.search(self.cfg["address_regex"], text, re.I)
                address = m.group(1).strip() if m else None
            L = Listing(source=self.id, ref=_slug(p.get("ref") or name or p["url"]) or hashlib.md5(p["url"].encode()).hexdigest()[:10],
                        url=p["url"], title=f"Nuova costruzione: {name or ''}", address=address,
                        price=min(prices) if prices else None, condition="nuovo", town=p.get("town"), zone=p.get("zone"),
                        description=meta(soup, "og:description", "description") or _longest_description(soup) or text[:600])
            if self.cfg.get("description_regex"):
                m = re.search(self.cfg["description_regex"], text, re.I | re.S)
                L.description = m.group(0) if m else L.description
            L.type = p.get("type") or "appartamento"
            og = meta(soup, "og:image")
            if og:
                L.images = [abs_url(p["url"], og)]
            elif self.cfg.get("image_regex"):
                L.images = [abs_url(p["url"], i["src"]) for i in soup.find_all("img", src=True) if re.search(self.cfg["image_regex"], i["src"])][:6]
            L.features["_project"] = True
            out.append(L)
        return out
