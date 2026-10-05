"""Adattatore configurabile per i siti di agenzia 'classici': pagine elenco con link alle schede.

Parametri in config/sources.yaml:
  start_urls: [url, ...]           pagine elenco vendita (anche più di una: es. appartamenti, ville)
  link_regex: '...'                 regex sull'URL assoluto che identifica una scheda immobile
  page_template: 'https://...{n}'  opzionale: URL della pagina n (n parte da page_start, default 2)
  max_pages: 40                     limite pagine elenco
  exclude_regex: '...'              opzionale: scarta URL (es. affitti)
  require_regex: '...'              opzionale: tieni solo schede il cui testo lo contiene (es. 'vendita')
  detail: true                      scarica le schede (necessario per mq/prezzo se l'elenco non li mostra)
"""
import re

from ..models import Listing
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of

NEXT_TEXT = re.compile(r"^\s*(successiv[ao]|avanti|next|›|»|>|>>)\s*$", re.I)


class GenericSource(Source):
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
                        links.setdefault(u.rstrip("/") if cfg.get("strip_slash") else u)
                if len(links) == before:
                    break  # pagina senza annunci nuovi: fine elenco
                if cfg.get("page_template"):
                    url = cfg["page_template"].format(n=page)
                    page += 1
                else:
                    url = self._next_link(soup, url)
        ctx.log(f"{len(links)} schede trovate")
        out = []
        details = 0
        for u in links:
            L = Listing(source=self.id, ref=self._ref(u), url=u)
            if cfg.get("detail", True) and ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(u)
                    details += 1
                    L = parse_detail(html, u, self.id, L)
                    if cfg.get("require_regex") and not re.search(cfg["require_regex"], html, re.I):
                        continue
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

    def _ref(self, url):
        rx = self.cfg.get("ref_regex")
        if rx:
            m = re.search(rx, url)
            if m:
                return m.group(1)
        return ref_from_url(url)

    @staticmethod
    def _next_link(soup, url):
        tag = soup.find("a", rel="next") or soup.find("link", rel="next")
        if tag and tag.get("href"):
            return abs_url(url, tag["href"])
        for a in soup.find_all("a", href=True):
            if NEXT_TEXT.match(a.get_text(" ", strip=True)) or re.search(r"next|successiv", " ".join(a.get("class", [])) + (a.get("aria-label") or ""), re.I):
                return abs_url(url, a["href"])
        return None
