"""Adattatori specifici per il batch C2 (agenzie di Trieste su gestionali o siti fatti con builder).

Piattaforme coperte in questo modulo (prefisso C2_ per non collidere con gli altri batch):
  - C2_wix          Wix "dynamic pages" legate a una collezione (sitemap.xml -> pagine scheda renderizzate
                     lato server con testo "Etichetta / Valore" in griglia; l'ordine delle etichette e dei
                     valori nel DOM varia da sito a sito, quindi l'accoppiamento è fatto per "gruppi").
  - C2_riksoft      Gestionale Riksoft/"datadomus" (cerco.php?contr=1&cat=N): la pagina risultati contiene
                     già ogni annuncio come div.estates con prezzo/mq/locali in data-* e, nel pannello
                     dettagli espanso incluso nello stesso HTML, foto e descrizione: non serve scaricare
                     le schede singole.
  - C2_onweb        Siti fatti col builder Onweb (elenco -> schede /it/immobili-in-vendita/<slug>-<id>):
                     pagine molto semplici, senza campi prezzo/mq strutturati (tutto nel testo libero
                     della descrizione, es. "Richiesti 46.000 Euro", "circa 90mq"): li ricava dal testo.
  - C2_foxgroup     Gestionale "GestionaleImmobiliare.it" (Fox Group, es. asrealestate.it): il prezzo
                     compare solo nelle card dell'elenco (h3.price-box), NON nella scheda; mq/camere/bagni
                     invece solo nella scheda (icone .info-name) e non nell'elenco: li combina.
  - C2_webnode      Siti Webnode: pagina/e senza campi strutturati, prezzo/mq/descrizione nel testo libero
                     di <main> e foto in un blob JSON incorporato nella pagina. Alcuni siti elencano per
                     errore anche annunci in affitto nella pagina "vendita": tiene solo le schede il cui
                     testo dice "Proponiamo in vendita".
"""
import html as html_lib
import re

from .. import parse_utils as pu
from ..models import Listing
from . import register
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of

RENT_RE = re.compile(r"affitt|locazion", re.I)
SALE_RE = re.compile(r"vend[ei]|in vendita", re.I)
SOLD_RE = re.compile(r"venduto|venduta", re.I)

LABELS_IT = {
    "tipologia": "type", "camere da letto": "bedrooms", "camere": "bedrooms", "bagni": "bathrooms",
    "metratura": "mq", "superficie": "mq", "piano": "floor", "anno di costruzione": "year",
    "classe energetica": "energy",
}
LABELS_EN = {
    "property type": "type", "bedrooms": "bedrooms", "bathrooms": "bathrooms", "size": "mq",
    "floors": "floor", "year built": "year", "energy rating": "energy",
}
LABEL_MAP = {**LABELS_IT, **LABELS_EN}

TYPE_WORDS = re.compile(
    r"^(appartamento|villa|attico|mansarda|casa|rustico|loft|garage|box|monolocale|bilocale|trilocale|"
    r"quadrilocale|duplex|porzione di casa|stabile|capannone|magazzino|deposito|ufficio|negozio|"
    r"locale commerciale|terreno)\b", re.I)
YEAR_RE = re.compile(r"^(1[89]\d{2}|20[0-3]\d)$")
MQ_RE = re.compile(r"^(\d+([.,]\d+)?)\s*mq\.?$", re.I)

DETAIL_HEADINGS = re.compile(r"property details|dettagli", re.I)
STOP_HEADINGS = re.compile(r"property location|localizzazione|contact agent|contatti|lascia un messaggio", re.I)
DESC_HEADING = re.compile(r"property description|descrizione", re.I)


def _lines(html: str) -> list[str]:
    soup = soup_of(html)
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    txt = soup.get_text("\n")
    out = []
    for l in txt.split("\n"):
        l = l.strip().strip("​﻿").strip()
        if l:
            out.append(l)
    return out


def _grid_pairs(lines: list[str]) -> dict:
    """Etichette/valori del riquadro dati (Property Details / Dettagli): prima i valori riconoscibili dal
    contenuto (tipologia, mq, anno), poi accoppiamento posizionale a gruppi per quel che resta (camere/bagni/piano)."""
    out, rest = {}, []
    for l in lines:
        low = l.lower().strip(" :")
        if "type" not in out and TYPE_WORDS.match(l):
            out["type"] = l
            continue
        m = MQ_RE.match(low)
        if m and "mq" not in out:
            out["mq"] = l
            continue
        if YEAR_RE.match(low) and "year" not in out:
            out["year"] = l
            continue
        rest.append(l)
    i, n = 0, len(rest)
    while i < n:
        if rest[i].lower().strip(" :") in LABEL_MAP:
            keys = []
            while i < n and rest[i].lower().strip(" :") in LABEL_MAP:
                keys.append(LABEL_MAP[rest[i].lower().strip(" :")])
                i += 1
            vals = []
            while i < n and rest[i].lower().strip(" :") not in LABEL_MAP and len(vals) < len(keys):
                vals.append(rest[i])
                i += 1
            for k, v in zip(keys, vals):
                out.setdefault(k, v)
        else:
            i += 1
    return out


@register("C2_wix")
class WixDynamicSource(Source):
    """Siti Wix con pagine scheda legate a una collezione dati (sitemap.xml -> dynamic-properties-*-sitemap.xml).
    Config: sitemap (opzionale, default '<website>sitemap.xml')."""

    def fetch(self, ctx):
        base = self.cfg["website"].rstrip("/") + "/"
        sitemap = self.cfg.get("sitemap", base + "sitemap.xml")
        idx = ctx.http.text(sitemap)
        sub_sitemaps = re.findall(r"<loc>\s*([^<]*dynamic-properties[^<]*sitemap\.xml)\s*</loc>", idx, re.I)
        if not sub_sitemaps:
            raise ValueError("nessuna sitemap 'dynamic-properties' trovata")
        urls: dict[str, None] = {}
        for sm in sub_sitemaps:
            xml = ctx.http.text(sm)
            for u in re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml):
                urls.setdefault(u.strip())
        ctx.log(f"{len(urls)} pagine immobile nelle sitemap")
        out, details, skipped = [], 0, 0
        for u in urls:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    details += 1
                    parsed = self._parse(ctx.http.text(u), u, L)
                    if parsed is None:
                        skipped += 1
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        if skipped:
            ctx.log(f"{skipped} pagine scartate (non in vendita o non una scheda)")
        return out

    def _parse(self, html: str, url: str, L: Listing):
        lines = _lines(html)
        try:
            di = next(i for i, l in enumerate(lines) if DETAIL_HEADINGS.match(l))
        except StopIteration:
            return None  # pagina non è una scheda immobile valida (template diverso/rotto)
        end = next((i for i in range(di + 1, len(lines)) if STOP_HEADINGS.match(lines[i])), len(lines))
        grid = _grid_pairs(lines[di + 1:end])

        # stato/prezzo: compaiono poco dopo l'indirizzo, subito prima della descrizione (occorrenza più vicina
        # a "Property Details"; le occorrenze più in alto nella pagina sono voci del menu di navigazione)
        status_i = next((i for i in range(di - 1, -1, -1)
                          if re.fullmatch(r"vendesi|affittasi|affittato|venduto|in vendita|in affitto|"
                                           r"trattativa riservata", lines[i], re.I)), None)
        if status_i is None or not SALE_RE.search(lines[status_i]) or SOLD_RE.search(lines[status_i]):
            return None
        price_line = lines[status_i + 1] if status_i + 1 < len(lines) else None
        L.price = pu.parse_price(price_line) if price_line else None

        title_i = next((i for i, l in enumerate(lines[:status_i]) if l.lower() not in
                         ("top of page", "home", "< back")), None)
        soup = soup_of(html)
        h1 = soup.find("h1")
        L.title = (h1.get_text(" ", strip=True) if h1 else None) or (lines[title_i] if title_i is not None else None)
        if status_i > 0:
            L.address = lines[status_i - 1]

        desc_i = next((i for i in range(status_i, di) if re.match(r"property description|descrizione$",
                                                                    lines[i], re.I)), None)
        if desc_i is not None:
            stop_i = next((i for i in range(desc_i + 1, di) if re.match(r"contact agent|contatti$",
                                                                          lines[i], re.I)), di)
            L.description = " ".join(lines[desc_i + 1:stop_i]) or None

        L.mq = pu.parse_mq(grid.get("mq"))
        L.bedrooms = pu.parse_small_count(grid.get("bedrooms"), ["camere"])
        L.bathrooms = pu.parse_small_count(grid.get("bathrooms"), ["bagni"])
        floor = grid.get("floor")
        L.floor = floor if floor and not re.fullmatch(r"0|​", floor) else L.floor
        L.energy = (re.search(r"\b(A4|A3|A2|A1|A\+|[A-G])\b", grid.get("energy", "")) or [None, None])[1]
        if grid.get("type"):
            L.type = pu.detect_type(grid["type"])

        imgs = re.findall(r"https://static\.wixstatic\.com/media/[A-Za-z0-9_~%.\-]+\.(?:jpe?g|png|webp)", html, re.I)
        L.images = list(dict.fromkeys(imgs))[:12]
        return True


@register("C2_riksoft")
class RiksoftSource(Source):
    """Gestionale Riksoft 'datadomus' (es. immobiliarefm.com): risultati vendita su cerco.php?contr=1&cat=N
    (0=Residenziale, 1=Commerciale). Config: cats (opzionale, default [0, 1])."""

    def fetch(self, ctx):
        base = self.cfg["website"].rstrip("/") + "/"
        cats = self.cfg.get("cats", [0, 1])
        out, seen = [], set()
        for cat in cats:
            url = f"{base}cerco.php?contr=1&cat={cat}"
            soup = soup_of(ctx.http.text(url))
            for div in soup.select("div.estates"):
                ref = div.get("id") or div.get("data-rifshw")
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                a = div.select_one(".permalink a")
                page_url = abs_url(base, a["href"]) if a and a.get("href") else url
                L = Listing(source=self.id, ref=ref, url=page_url)
                L.price = pu.parse_price(div.get("data-prc"))
                L.mq = pu.parse_mq(div.get("data-mq"))
                if div.get("data-type"):
                    L.type = pu.detect_type(div["data-type"])

                loc = div.select_one(".lssum1")
                loc_txt = loc.get_text(" ", strip=True) if loc else ""
                addr = re.sub(r"^.*?cod\.\s*\d+\s*", "", loc_txt).strip()
                L.address = addr or div.get("data-city")
                L.title = f"{div.get('data-type', '').strip()} - {L.address}".strip(" -") or None

                txt = div.select_one(".lssum2")
                txt = txt.get_text(" ", strip=True) if txt else ""
                m = re.search(r"Locali\s*(\d+)", txt)
                L.rooms = int(m.group(1)) if m else None
                m = re.search(r"Camere\s*(\d+)", txt)
                L.bedrooms = int(m.group(1)) if m else None
                m = re.search(r"Bagni\s*(\d+)", txt)
                L.bathrooms = int(m.group(1)) if m else None

                desc = div.select_one(".dtldesc")
                if desc:
                    for btn in desc.find_all("button"):
                        btn.decompose()
                    dtxt = desc.get_text(" ", strip=True)
                    L.description = dtxt or None

                L.images = list(dict.fromkeys(
                    abs_url(base, im.get("data-src") or im.get("src"))
                    for im in div.select(".rtkgaltn img") if im.get("data-src") or im.get("src")))

                ctx.detail_ids.add(L.id)
                out.append(L)
        ctx.log(f"{len(out)} annunci in vendita")
        return out


@register("C2_onweb")
class OnwebSource(Source):
    """Siti col builder Onweb: pagina elenco con link alle schede (config: start_urls, link_regex).
    Le schede sono testo libero senza campi prezzo/mq strutturati: li ricava dalla descrizione con
    parse_price/parse_mq (gestiscono sia "€ 250.000" sia "250.000 €uro", "circa 90mq" ecc.)."""

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            html = ctx.http.text(start)
            soup = soup_of(html)
            for a in soup.find_all("a", href=True):
                u = abs_url(start, a["href"])
                if u and link_rx.search(u) and not (excl and excl.search(u)):
                    links.setdefault(u)
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    details += 1
                    html = ctx.http.text(u)
                    L = parse_detail(html, u, self.id, L)
                    if not L.price:
                        L.price = pu.parse_price(L.description or "")
                    if not L.mq:
                        L.mq = pu.parse_mq(L.description or "")
                    if L.features.pop("_rent", False) or RENT_RE.search(L.title or ""):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("C2_foxgroup")
class FoxGroupSource(Source):
    """Gestionale GestionaleImmobiliare.it / Fox Group: elenco con card .listing-box (link + h3.price-box),
    scheda con mq/camere/bagni nelle .icone-immobile (nessuna delle due pagine ha entrambi i dati)."""

    def fetch(self, ctx):
        cfg = self.cfg
        max_pages = cfg.get("max_pages", 10)
        cards: dict[str, int | None] = {}
        for start in cfg["start_urls"]:
            url, page = start, cfg.get("page_start", 2)
            for _ in range(max_pages):
                soup = soup_of(ctx.http.text(url))
                before = len(cards)
                for box in soup.select(".listing-box"):
                    a = box.find("a", href=True)
                    u = abs_url(start, a["href"]) if a else None
                    if not u:
                        continue
                    price_tag = box.select_one(".price-box")
                    cards.setdefault(u, pu.parse_price(price_tag.get_text(strip=True)) if price_tag else None)
                if len(cards) == before or not cfg.get("page_template"):
                    break
                url = cfg["page_template"].format(n=page)
                page += 1
        ctx.log(f"{len(cards)} schede trovate")
        out, details = [], 0
        for u, price in cards.items():
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            L.price = price
            if ctx.store.needs_detail(L.id, price) and details < self.max_details:
                try:
                    details += 1
                    html = ctx.http.text(u)
                    L = parse_detail(html, u, self.id, L)
                    L.price = price if price is not None else L.price
                    soup = soup_of(html)
                    for icona in soup.select(".icone-immobile .icona"):
                        label = icona.select_one(".info-name")
                        span = icona.select_one("span")
                        if not label or not span:
                            continue
                        key, val = label.get_text(strip=True).lower(), span.get_text(strip=True)
                        if key == "mq":
                            L.mq = L.mq or pu.parse_mq(val)
                        elif key == "camere":
                            L.bedrooms = L.bedrooms or pu.parse_small_count(val, ["camere"])
                        elif key == "bagni":
                            L.bathrooms = L.bathrooms or pu.parse_small_count(val, ["bagni"])
                    # la scheda ripete spesso in coda i contatti dell'agenzia (indirizzo della sede,
                    # es. "AS Real Estate Srl - Via ... Trieste"): li toglie, altrimenti "inquinano"
                    # il rilevamento del comune (via testo descrizione) di annunci fuori provincia
                    # (Slovenia/Croazia). Pattern configurabile per agenzia (footer_regex in YAML).
                    footer_rx = cfg.get("footer_regex")
                    if footer_rx and L.description:
                        L.description = re.sub(footer_rx, "", L.description, flags=re.I | re.S).strip() or None
                    imgs = [a.get("data-rsBigImg") or a.get("href") for a in soup.select("a.rsImg")]
                    imgs = [i for i in imgs if i and re.search(r"\.(jpe?g|webp)(\?|$)", i, re.I)]
                    if imgs:
                        L.images = list(dict.fromkeys(imgs))[:12]
                    if L.features.pop("_rent", False) or RENT_RE.search(L.title or ""):
                        continue
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("C2_webnode")
class WebnodeSource(Source):
    """Siti Webnode (config: start_urls, link_regex). Prezzo/mq/descrizione non sono in campi strutturati:
    li ricava dal testo di <main>; le foto da un blob JSON incorporato nella pagina (gallery). Tiene solo
    le schede il cui testo dice esplicitamente "proponiamo in vendita" (alcuni siti elencano per errore
    anche annunci in affitto nella pagina "vendita")."""

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            soup = soup_of(ctx.http.text(start))
            for a in soup.find_all("a", href=True):
                u = abs_url(start, a["href"])
                if u and link_rx.search(u):
                    links.setdefault(u)
        ctx.log(f"{len(links)} schede trovate")
        out, details, skipped = [], 0, 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    details += 1
                    raw = ctx.http.text(u)
                    L = parse_detail(raw, u, self.id, L)
                    soup = soup_of(raw)
                    main = soup.select_one("main") or soup
                    text = main.get_text(" ", strip=True)
                    if not re.search(r"proponiamo in vendita", text, re.I):
                        skipped += 1
                        continue
                    L.price = L.price or pu.parse_price(text)
                    L.mq = L.mq or pu.parse_mq(text)
                    if len(text) > len(L.description or ""):
                        L.description = pu.clean_text(text, 3000)
                    unesc = html_lib.unescape(raw)
                    imgs = re.findall(r'"src":"(https://[^"]+?\.jpe?g)(?:\?[^"]*)?"', unesc)
                    if imgs:
                        L.images = list(dict.fromkeys(imgs))[:12]
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        if skipped:
            ctx.log(f"{skipped} schede scartate (non in vendita)")
        return out


@register("C2_agestanet")
class AgestaNetSource(Source):
    """Gestionale AgestaNET: l'elenco annunci è caricato via JS con una POST JSON a
    /web/include/aj-ricerca.asp (serve prima una GET alla pagina immobili.asp per i cookie di sessione,
    più gli header Referer/X-Requested-With). Config: list_url (opzionale, default <website>immobili.asp?
    tipo_contratto=V), group_cod_agenzia (opzionale: se assente lo ricava dal form nascosto della pagina).
    I campi numerici (prezzo/mq/vani/camere/bagni/comune) vengono dalla risposta JSON, che è affidabile;
    la scheda HTML viene comunque scaricata per la descrizione completa e le foto (la griglia label/valore
    della pagina non è affidabile per camere/bagni/mq con l'estrattore generico)."""

    def fetch(self, ctx):
        cfg = self.cfg
        base = cfg["website"].rstrip("/") + "/"
        list_url = cfg.get("list_url", base + "immobili.asp?tipo_contratto=V")
        ajax_url = base + "include/aj-ricerca.asp"
        soup = soup_of(ctx.http.text(list_url))
        group = cfg.get("group_cod_agenzia")
        if not group:
            inp = soup.find("input", {"name": "group_cod_agenzia"})
            group = inp.get("value") if inp else ""

        out, details, page = [], 0, 1
        while True:
            data = {
                "showkind": "", "num_page": str(page), "group_cod_agenzia": group, "cod_sede": "0",
                "cod_sede_aw": "0", "cod_gruppo": "0", "pagref": "", "ref": "", "language": "ita",
                "maxann": "200", "shid": "0", "estero": "0", "cod_campi": "", "cod_nazione": "",
                "cod_regione": "", "tipo_contratto": "V", "cod_categoria": "%", "cod_tipologia": "%",
                "cod_provincia": cfg.get("cod_provincia", ""), "cod_comune": cfg.get("cod_comune", ""),
                "localita": "", "prezzo_min": "", "prezzo_max": "", "vani_min": "", "camere_min": "",
                "mq_min": "", "mq_max": "", "giardino": "", "riferimento": "", "cod_ordine": "",
            }
            r = ctx.http.post(ajax_url, data=data,
                               headers={"Referer": list_url, "X-Requested-With": "XMLHttpRequest"})
            payload = r.json()
            if payload.get("status") != "ok":
                raise ValueError(f"aj-ricerca.asp: {payload}")
            ann = payload.get("AN") or []
            for item in ann:
                url = abs_url(base, item.get("LINK"))
                if not url:
                    continue
                ref = str(item.get("IDAN") or ref_from_url(url))
                L = Listing(source=self.id, ref=ref, url=url)
                L.price = pu.parse_price(item.get("PREZ"))
                L.mq = pu.parse_mq(item.get("SUPE"))
                L.rooms = pu.parse_rooms(item.get("VANI"))
                L.bedrooms = pu.parse_small_count(item.get("CAME"), ["camere"])
                L.bathrooms = pu.parse_small_count(item.get("BAGN"), ["bagni"])
                if item.get("TIPO"):
                    L.type = pu.detect_type(item["TIPO"])
                L.zone = item.get("ZONA") or None
                L.address = item.get("ZONA") or item.get("COMU") or None
                if item.get("COMU"):
                    from ..zones import detect_town
                    L.town = detect_town(item["COMU"])
                if ctx.store.needs_detail(L.id, L.price) and details < self.max_details:
                    try:
                        details += 1
                        html = ctx.http.text(url)
                        L = parse_detail(html, url, self.id, L)
                        if L.features.pop("_rent", False):
                            continue
                        ctx.detail_ids.add(L.id)
                    except Exception as e:
                        ctx.errors.append(f"{url}: {e}")
                        ctx.merge_known(L)
                else:
                    ctx.merge_known(L)
                out.append(L)
            try:
                np_, p_ = int(payload.get("NP", 1)), int(payload.get("P", page))
            except (TypeError, ValueError):
                break
            if p_ >= np_:
                break
            page += 1
        ctx.log(f"{len(out)} annunci in vendita")
        return out
