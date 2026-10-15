"""Batch F — Trieste: Greif Group, Agenzia Elle B, Omnia Costruzioni, Agenzia La Julia.

Adattatori (config/sources.d/F.yaml):
  F_greif           Greif Group (Barcola/Trieste) — Next.js, elenco+schede come 'generic' ma con il fix
                    del fallback prezzo (vedi sotto).
  F_bancadellecase  Agenzia Elle B (Gorizia) — gestionale custom "Banca delle Case".
  F_omnia           Omnia Costruzioni S.r.l. (Muggia) — costruttore, pagina portfolio statica (site123).
  F_lajulia         Agenzia La Julia (Gorizia) — CMS "WEBS": elenco reso via JS ma la pagina friendly
                    filtrata per comune imposta nel cookie di risposta i criteri di ricerca, che rilanciamo
                    in POST all'API usata dal sito stesso.
"""
import html as htmlmod
import json
import re
import urllib.parse

from .. import parse_utils as pu
from ..models import Listing
from ..zones import detect_town
from . import register
from .base import Source, abs_url, kv_pairs, parse_detail, ref_from_url, soup_of


# ---------------------------------------------------------------- Greif Group (Barcola/Trieste)
# Next.js (SSR): due pagine elenco (/it/real-estate/trieste e /it/real-estate/muggia), schede
# /it/real-estate/<localita>/<slug>. Il generico 'generic' basterebbe per elenco+schede, ma il fallback
# generico di base.py per il prezzo (_price_from_page) prende per sbaglio il primo "€ NNN.NNN" che trova
# in pagina quando il prezzo dell'immobile è "Trattativa riservata" — e qui capita quasi sempre che la
# pagina citi altrove il costo di un extra opzionale (box auto, posto auto): va ignorato.

@register("F_greif")
class GreifSource(Source):
    START = ["https://www.greif-group.com/it/real-estate/trieste", "https://www.greif-group.com/it/real-estate/muggia"]
    LINK_RX = re.compile(r'^https://www\.greif-group\.com/it/real-estate/(?:trieste|muggia)/[a-z0-9-]+$', re.I)
    ON_REQUEST_RX = re.compile(r"trattativa|riservat|su richiesta", re.I)

    def fetch(self, ctx):
        links: dict[str, None] = {}
        for start in self.START:
            html = ctx.http.text(start)
            soup = soup_of(html)
            for a in soup.find_all("a", href=True):
                u = abs_url(start, a["href"])
                if u and self.LINK_RX.match(u):
                    links.setdefault(u)
        if not links:
            raise ValueError("nessuna scheda trovata nelle pagine Trieste/Muggia (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            # lo slug intero (non solo l'ultimo numero di ref_from_url) evita collisioni fra immobili
            # i cui URL finiscono per coincidenza con lo stesso numero (es. "...-120" e "...-130")
            ref = u.rstrip("/").split("/")[-1]
            # il comune è già nella sezione dell'URL che abbiamo scelto noi (/real-estate/trieste|muggia/...):
            # non possiamo lasciarlo dedurre dal testo, alcune schede (es. gli showroom di Noghere, gli
            # attici "Fronte Mare") non citano affatto "Trieste"/"Muggia" per esteso nella descrizione.
            m = re.search(r"/real-estate/(trieste|muggia)/", u)
            town = m.group(1).capitalize() if m else None
            L = Listing(source=self.id, ref=ref, url=u, town=town)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(u)
                    details += 1
                    soup = soup_of(html)
                    on_request = bool(self.ON_REQUEST_RX.search(kv_pairs(soup).get("price") or ""))
                    L = parse_detail(html, u, self.id, L)
                    if on_request:
                        L.price = None   # prezzo "trovato" dal fallback generico: in realtà è un extra opzionale
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


# ---------------------------------------------------------------- Agenzia Immobiliare Elle B (Gorizia)
# Gestionale custom "Banca delle Case" (PHP): elenco /lista/?s_archivio=vendita&submit=CERCA, paginato con
# il parametro limit=da-X-a-Y (X cresce di 16 in 16); scheda /scheda/id_annuncio=N&vals=tipologia-Comune-camere.
# L'agenzia ha sede a Gorizia e la maggior parte degli annunci è fuori provincia di Trieste: il comune va
# letto dal campo 'vals' dell'URL (più affidabile del testo libero della scheda) e NON impostiamo un comune
# di default, così il filtro automatico di run.py scarta correttamente gli annunci fuori provincia.

@register("F_bancadellecase")
class BancaDelleCaseSource(Source):
    LIST = "https://www.agenziaelleb.it/lista/"
    SCHEDA_RX = re.compile(r"/scheda/id_annuncio=(\d+)&vals=([^\"'&]*)", re.I)

    def fetch(self, ctx):
        links: dict[str, tuple[str, str]] = {}
        step = 16
        for i in range(40):
            url = self.LIST + "?s_archivio=vendita&submit=CERCA" + (f"&limit=da-{i * step}-a-{(i + 1) * step}" if i else "")
            html = ctx.http.text(url)
            before = len(links)
            for m in self.SCHEDA_RX.finditer(html):
                ref, vals = m.group(1), m.group(2)
                links.setdefault(ref, (abs_url(url, m.group(0)), vals))
            if len(links) == before:
                break
        if not links:
            raise ValueError("nessun annuncio nell'elenco vendita (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for ref, (url, vals) in links.items():
            town = self._town_from_vals(vals)
            L = Listing(source=self.id, ref=ref, url=url, town=town)
            # niente di utile da leggere: quasi tutti gli annunci (sede a Gorizia) sono fuori provincia, e il
            # comune si legge già dall'URL. Evitiamo di scaricare la scheda (e di richiamarla a ogni giro,
            # visto che un annuncio fuori provincia non finisce mai in archivio) quando non serve.
            if not town:
                out.append(L)
                continue
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(url)
                    details += 1
                    L = parse_detail(html, url, self.id, L)
                    if L.features.pop("_rent", False):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            L.town = town or L.town
            # "Località" della scheda finisce per sbaglio in 'zone' (la label corrisponde al comune, non a
            # un rione): lo scartiamo se coincide con un comune riconosciuto, per non sporcare la zona.
            if L.zone and detect_town(L.zone):
                L.zone = None
            if not L.title or "Agenzia Immobiliare" in L.title or "Immobili a Gorizia" in L.title:
                tipo = vals.split("-")[0].strip() if vals else ""
                comune = town or (vals.split("-")[1].strip() if vals.count("-") >= 1 else "")
                L.title = f"{tipo.capitalize() or 'Immobile'} in vendita" + (f" a {comune}" if comune else "")
            out.append(L)
        return out

    @staticmethod
    def _town_from_vals(vals: str) -> str | None:
        vals = urllib.parse.unquote(vals or "")
        parts = vals.split("-")
        if len(parts) < 2:
            return None
        return detect_town(parts[1].strip())


# ---------------------------------------------------------------- Omnia Costruzioni S.r.l. (Muggia)
# Costruttore triestino (CMS site123): la pagina "Portfolio e proposte di vendita" elenca insieme il
# cantiere in vendita e due lavori non residenziali (studio dentistico, progetto DPS): teniamo solo le
# voci il cui titolo/URL parla di "vendita".

@register("F_omnia")
class OmniaCostruzioniSource(Source):
    LIST = "https://www.omniacostruzionisrl.it/portfolio-e-proposte-di-vendita"

    def fetch(self, ctx):
        html = ctx.http.text(self.LIST)
        soup = soup_of(html)
        links: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            title = a.get("title") or a.get_text(" ", strip=True) or ""
            slug = a["href"].rstrip("/").split("/")[-1]
            # la cartella stessa si chiama ".../portfolio-e-proposte-di-vendita/": guardiamo solo l'ultimo
            # pezzo di URL (lo slug della singola voce), non l'intero href, altrimenti "vendita" la matcha
            # sempre, comprese le voci non residenziali (studio dentistico, progetto DPS).
            if re.search(r"vendita", slug, re.I) or re.match(r"\s*in\s+vendita", title, re.I):
                u = abs_url(self.LIST, a["href"])
                if u and u.rstrip("/") != self.LIST.rstrip("/"):
                    links.setdefault(u, title)
        if not links:
            # portfolio letto ma nessuna voce "in vendita": il cantiere può essere esaurito (l'archivio
            # tratta comunque un elenco vuoto improvviso come sospetto per ~1 giorno e mezzo)
            entries = [a for a in soup.find_all("a", href=True) if "/portfolio-e-proposte-di-vendita/" in a["href"]]
            if not entries:
                raise ValueError("nessuna voce nel portfolio (struttura cambiata?)")
            ctx.log("nessuna proposta in vendita nel portfolio")
            return []
        out, details = [], 0
        for url, title in links.items():
            L = Listing(source=self.id, ref=ref_from_url(url), url=url, town="Muggia",
                        type="appartamento", title=title or None)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dh = ctx.http.text(url)
                    details += 1
                    L = parse_detail(dh, url, self.id, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            L.town = L.town or "Muggia"
            out.append(L)
        return out


# ---------------------------------------------------------------- Agenzia Immobiliare La Julia (Gorizia)
# CMS "WEBS" (non Wix, nonostante la nota iniziale): l'elenco è reso via JavaScript, ma la pagina friendly
# filtrata per comune (.../ads/vendita/immobile/trieste-trieste) imposta, nella risposta, il cookie
# 'search_buscador_pro' con i criteri di ricerca già ristretti al comune (campo locationId). Rilanciamo
# quel JSON in POST verso l'API che il sito stesso usa via jQuery (/api/AdsSearch/PostSearch): la risposta
# è un XML con le schede sintetiche incapsulate come HTML escapato. Per ogni annuncio scarichiamo poi
# /ad/<id> per la galleria completa di foto.

@register("F_lajulia")
class LaJuliaSource(Source):
    LIST = "https://www.lajulia.it/ads/vendita/immobile/trieste-trieste"
    API = "https://www.lajulia.it/api/AdsSearch/PostSearch"
    FRAG_RX = re.compile(r"<d2p1:string>(.*?)</d2p1:string>", re.S)

    def fetch(self, ctx):
        r = ctx.http.get(self.LIST)
        raw = r.cookies.get("search_buscador_pro")
        if not raw:
            raise ValueError("cookie di ricerca 'search_buscador_pro' non trovato (struttura cambiata?)")
        body = json.loads(urllib.parse.unquote(raw))
        # non ci fidiamo "a occhio" che il cookie sia filtrato per Trieste: lo verifichiamo, così un
        # annuncio fuori provincia non può mai finire tra i risultati con town="Trieste" assegnato di default.
        locs = body.get("locationId") or []
        if len(locs) != 1 or "trieste" not in (locs[0].get("label") or "").lower():
            raise ValueError(f"filtro comune inatteso nel cookie di ricerca: {locs!r} (struttura cambiata?)")
        if body.get("IsSale") is not True or body.get("IsRent"):
            raise ValueError("il cookie di ricerca non è filtrato per sola vendita (struttura cambiata?)")
        cards, page = [], 0
        body["itemsPerPage"] = 50
        while page < 10:
            body["currentPage"] = page
            resp = ctx.http.post(self.API, json=body,
                                 headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"})
            frags = self.FRAG_RX.findall(resp.text)
            if not frags:
                break
            cards += frags
            if len(frags) < body["itemsPerPage"]:
                break
            page += 1
        if not cards:
            # filtro verificato sopra: un elenco vuoto vuol dire che a Trieste ora non hanno annunci
            # (l'archivio tratta comunque un elenco vuoto improvviso come sospetto per ~1 giorno e mezzo)
            ctx.log("nessun annuncio a Trieste")
            return []
        ctx.log(f"{len(cards)} annunci nell'elenco (comune di Trieste)")
        out, details = [], 0
        for frag in cards:
            try:
                L = self._parse_card(htmlmod.unescape(frag))
            except Exception as e:
                ctx.errors.append(f"scheda: {e}")
                continue
            if L is None:
                continue
            if details < self.max_details and ctx.store.needs_detail(L.id, L.price):
                details += 1
                try:
                    self._detail(ctx, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{L.url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    def _parse_card(self, frag_html: str) -> Listing | None:
        soup = soup_of(frag_html)
        sec = soup.find("div", class_="result-details")
        if not sec or not sec.get("id"):
            return None
        ref = sec["id"]
        h1 = soup.find("h1")
        a = h1.find("a") if h1 else None
        title = a.get_text(" ", strip=True) if a else None
        href = ((a.get("href") if a else None) or f"/ad/{ref}").split("?")[0]
        url = abs_url(self.LIST, href)
        price_tag = soup.find("span", class_="price")
        price = pu.parse_price(price_tag.get_text(strip=True)) if price_tag else None
        p = soup.find("p")
        desc = p.get_text(" ", strip=True) if p else None
        L = Listing(source=self.id, ref=ref, url=url, title=title, price=price, description=desc, town="Trieste")
        for li in soup.find_all("li"):
            t = li.get_text(" ", strip=True)
            m = re.match(r"(\d+)\s*Loc", t, re.I)
            if m:
                L.rooms = int(m.group(1))
                continue
            m = re.match(r"(\d+)\s*Bagn", t, re.I)
            if m:
                L.bathrooms = int(m.group(1))
                continue
            m = re.match(r"(\d+)\s*m", t, re.I)
            if m:
                L.mq = int(m.group(1))
        img = soup.find("img")
        if img and img.get("src"):
            L.images = [img["src"]]
        L.type = pu.detect_type(title, desc)
        return L

    def _detail(self, ctx, L: Listing) -> None:
        html = ctx.http.text(L.url)
        imgs = list(dict.fromkeys(re.findall(r"https://img\d*\.idealista\.it/[^\"'\s)]+", html)))
        big = [u for u in imgs if "HOME_WI_1500" in u] or imgs
        if big:
            L.images = big[:12]
        soup = soup_of(html)
        p = soup.find("p")
        if p:
            txt = p.get_text(" ", strip=True)
            if len(txt) > len(L.description or ""):
                L.description = txt
