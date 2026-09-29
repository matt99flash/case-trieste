"""Batch D1 - agenzie con piattaforme personalizzate/sconosciute, alcune con annunci caricati via JavaScript.

Tirabora Immobiliare / Tirabora Lusso: gestionale proprietario, elenco caricato da un endpoint AJAX
(GET .../immobili/get_vendita, paginato con ?pagine=n), schede con h1 generico (nome agenzia, da NON
usare come titolo) e sezione "Immobili simili" che inquina l'estrazione automatica delle immagini.

Immobiliare 4B: sito statico che carica gli annunci via JS da un backend proprio
(https://immobiliare4b-backend.onrender.com/proxy) che espone il feed XML Getrix già convertito in JSON
(struttura {"Getrix": {"Immobile": [...]}}) senza autenticazione: lo leggiamo direttamente, niente HTML da
scaricare per l'elenco. `Contratto` = "V" vendita / "A" affitto.

Tam Immobiliare: app Laravel + Livewire; la pagina elenco mostra via SSR solo i primi 16 immobili (il
conteggio vero, es. 27, è in `serverMemo.data.immobiliData.total` incorporato come JSON nell'attributo
`wire:initial-data` del componente). Gli annunci successivi si caricano solo sparando l'evento Livewire
"loadNextPage" via POST a /livewire/message/<nome componente>, col protocollo Livewire v3 (fingerprint +
serverMemo aggiornato a ogni chiamata + updates): lo replichiamo qui invece di usare un browser headless.
I dati (prezzo/mq/vani/bagni/piano/indirizzo) sono già strutturati in JSON, niente parsing HTML per l'elenco.

Diverse piccole agenzie del batch (Norbedo e altre) scrivono nel titolo/testo solo il rione di Trieste
("Roiano", "Barcola", "Ponterosso"...) senza mai nominare il comune "Trieste" per esteso: `detect_town`
(condiviso, in zones.py) non lo riconosce e l'annuncio finirebbe scartato come "fuori provincia".
`GenericTriesteZoneSource` riusa l'adattatore `generic` e aggiunge questa sola deduzione in più.
"""
import html as html_mod
import json
import re
from urllib.parse import urlparse

from .. import parse_utils as pu
from ..models import Listing
from ..zones import detect_town, detect_zone
from . import register
from .base import Source, abs_url, jsonld_objects, parse_detail, ref_from_url, soup_of
from .generic import GenericSource

# Località palesemente fuori dalla provincia di Trieste (estero o regioni lontane), usate per scartare un
# annuncio già dal titolo visibile in elenco, senza scaricarne la scheda (e senza riscaricarla a ogni giro
# solo per scoprire che va buttata via: vedi OsPropertySource più sotto).
_OBVIOUSLY_OUTSIDE_RX = re.compile(
    r"\b(croazia|slovenia|lussino|losinj|istria|umago|rovigno|parenzo|cherso|zagabria|"
    r"udine|pordenone|gorizia|grado|monfalcone|cervignano|codroipo|latisana|portogruaro)\b", re.I)


@register("d1_tirabora")
class TiraboraSource(Source):
    """Tirabora Immobiliare e Tirabora Lusso (stesso gestionale, endpoint AJAX diverso per dominio)."""

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.tirabora.it").rstrip("/")
        endpoint = f"{base}/immobili/get_vendita"
        link_rx = re.compile(r"^" + re.escape(base) + r"/i/\d+/")
        links: dict[str, None] = {}
        for p in range(1, self.cfg.get("max_pages", 20)):
            html = ctx.http.text(endpoint, params={"tipologia": "", "pagine": str(p), "ordine": "", "elenco": "", "q": ""})
            soup = soup_of(html)
            before = len(links)
            for a in soup.find_all("a", href=True):
                u = abs_url(endpoint, a["href"])
                if u and link_rx.match(u):
                    links.setdefault(u)
            if len(links) == before:
                break
        if not links:
            raise ValueError("nessuna scheda nell'elenco vendita (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    html = ctx.http.text(u)
                    details += 1
                    L = self._detail(html, u, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    @staticmethod
    def _detail(html, url, L):
        soup = soup_of(html)
        # il titolo vero è in h3.h3 (l'h1 di pagina è il nome dell'agenzia, uguale su ogni scheda)
        title_tag = soup.select_one("h3.h3")
        sub = None
        if title_tag:
            small = title_tag.find("small")
            if small:
                sub = small.get_text(" ", strip=True) or None
                small.extract()
            L.title = title_tag.get_text(" ", strip=True) or None
        addr_tag = soup.select_one("h4.text-muted")
        if addr_tag:
            addr = addr_tag.get_text(" ", strip=True)
            if addr:
                L.address = addr
        # immagini proprie: solo il carosello della scheda (la sezione "Immobili simili" ne mostra altre)
        imgs = [abs_url(url, img.get("src")) for img in soup.select('#carousel-datails-screen img[id^="screen-img"]')]
        L.images = [i for i in imgs if i][:12]
        L = parse_detail(html, url, L.source, L)
        if sub and sub not in (L.description or ""):
            L.description = f"{sub}. {L.description}" if L.description else sub
        return L


@register("d1_getrix_feed")
class GetrixFeedSource(Source):
    """Agenzie il cui sito carica il catalogo da un feed Getrix esposto in JSON (es. Immobiliare 4B)."""

    def fetch(self, ctx):
        feed_url = self.cfg["feed_url"]
        detail_template = self.cfg.get("detail_template")  # es. "https://.../immobile.html?id={id}"
        data = ctx.http.json(feed_url)
        items = (data.get("Getrix") or {}).get("Immobile") or []
        if isinstance(items, dict):
            items = [items]
        if not items:
            raise ValueError("feed Getrix vuoto o struttura cambiata")
        out = []
        for item in items:
            if (item.get("Contratto") or "").upper() != "V":
                continue
            iid = str((item.get("$") or {}).get("IDImmobile") or "")
            if not iid:
                continue
            url = detail_template.format(id=iid) if detail_template else feed_url + f"#{iid}"
            L = Listing(source=self.id, ref=iid, url=url)
            L.price = pu.parse_price(item.get("Prezzo"))
            L.mq = pu.parse_mq(item.get("MQSuperficie") or item.get("MQCommerciale"))
            L.rooms = pu.parse_rooms(item.get("NrLocali")) if item.get("NrLocali") not in (None, "0") else None
            tipologia = item.get("Tipologia")
            tipo_testo = tipologia.get("_") if isinstance(tipologia, dict) else tipologia
            L.type = pu.detect_type(tipo_testo, None)
            L.town = detect_town(item.get("Comune")) or self.default_town
            L.zone = item.get("Zona") or None
            if (item.get("PubblicaIndirizzo") or "").lower() == "true":
                L.address = item.get("Indirizzo") or None
            try:
                L.lat = float(item["Latitudine"])
                L.lon = float(item["Longitudine"])
            except (KeyError, TypeError, ValueError):
                pass
            descrizioni = ((item.get("Descrizioni") or {}).get("Descrizione")) or []
            if isinstance(descrizioni, dict):
                descrizioni = [descrizioni]
            testo_it = next((d.get("Testo") for d in descrizioni if (d.get("$") or {}).get("Lingua") == "IT" and d.get("Testo")), None)
            titolo_it = next((d.get("Titolo") for d in descrizioni if (d.get("$") or {}).get("Lingua") == "IT" and d.get("Titolo")), None)
            L.description = pu.clean_text(testo_it)
            L.title = titolo_it or tipo_testo or None
            imgs = ((item.get("Immagini") or {}).get("Immagine")) or []
            if isinstance(imgs, dict):
                imgs = [imgs]
            L.images = [i.get("URL") for i in imgs if (i.get("$") or {}).get("Tipo") == "F" and i.get("URL")][:12]
            out.append(L)
        ctx.log(f"{len(out)} annunci di vendita nel feed ({len(items)} totali)")
        ctx.detail_ids.update(L.id for L in out)
        return out


@register("d1_studio39")
class Studio39Source(Source):
    """Gestionale ASP condiviso da più agenzie (Studio 39, Solaris real estate: stesso markup, stesso
    software, solo dominio/URL diversi): elenco su un'unica pagina (niente paginazione), pagina scheda con
    un form di ricerca (select comune/zona/fascia prezzo) che inquina l'estrazione generica di testo e
    coppie etichetta/valore (i valori "di default" dei <select> vengono letti come se fossero i dati
    dell'annuncio). I dati veri sono in un div "box_evidenza" con etichette in <strong> e valore adiacente
    (niente dt/dd, niente "label: valore" su una riga sola -> serve un'estrazione dedicata per prezzo/mq).
    `link_regex` in config sceglie il pattern URL delle schede (varia da sito a sito)."""

    LINK_RE = re.compile(r"/immobile/\d+/[^/]+/?$")

    def fetch(self, ctx):
        list_url = self.cfg.get("listings_url", "https://www.studio39immobiliare.it/articles.asp?show=vendita")
        link_rx = re.compile(self.cfg["link_regex"]) if self.cfg.get("link_regex") else self.LINK_RE
        html = ctx.http.text(list_url)
        soup = soup_of(html)
        links: dict[str, None] = {}
        for a in soup.find_all("a", href=True):
            u = abs_url(list_url, a["href"])
            if u and link_rx.search(u):
                links.setdefault(u)
        if not links:
            raise ValueError("nessuna scheda nell'elenco vendita (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(u)
                    details += 1
                    L = self._detail(dhtml, u, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    @staticmethod
    def _detail(html, url, L):
        soup = soup_of(html)
        sf = soup.find(id="searchForm")
        if sf:
            sf.decompose()
        for sel in soup.find_all("select"):
            sel.decompose()
        # il vero titolo è quello accanto al box prezzo (colonna "col-md-5"): pagine come Solaris hanno
        # anche un <h1> decorativo altrove nel layout (es. il nome dell'agenzia) che altrimenti parse_detail
        # pescherebbe per primo.
        title_h1 = soup.select_one(".col-md-5 h1")
        if title_h1:
            L.title = title_h1.get_text(" ", strip=True) or None
        box = soup.select_one(".box_evidenza")
        if box:
            text = box.get_text(" ", strip=True)
            m = re.search(r"Prezzo\s*:?\s*([\d.,]+)\s*€", text)
            if m:
                L.price = pu.parse_price(m.group(1) + " €")
            m = re.search(r"Metri quadri\s*:?\s*([\d.,]+)", text)
            if m:
                L.mq = pu.parse_mq(m.group(1) + " mq")
            m = re.search(r"Piano\s*:?\s*(\d+\s*di\s*\d+)", text)
            if m:
                L.floor = m.group(1)
            if re.search(r"\bVenduto\b|\bSotto\s*[Oo]fferta\b", text):
                L.sold = True
        # galleria completa: i link dentro #links (i soli <img> visibili in pagina sono 1-2 anteprime)
        gallery = soup.select_one("#links")
        if gallery:
            imgs = [abs_url(url, a["href"]) for a in gallery.find_all("a", href=True)]
            L.images = [i for i in imgs if i][:12]
        # parse_detail ri-analizza l'HTML da zero: gli passiamo la versione ripulita (senza il form di
        # ricerca né i <select>), altrimenti ripeschiamo di nuovo le opzioni "Seleziona..." come dati veri.
        L = parse_detail(str(soup), url, L.source, L)
        # niente comune/indirizzo strutturato in pagina: il testo spesso nomina solo il rione (es. "Roiano",
        # "Rive"), non "Trieste" -> se troviamo un rione noto di Trieste, il comune è Trieste per definizione.
        if not L.town:
            zone = detect_zone(L.title, L.description)
            if zone:
                L.town, L.zone = "Trieste", zone
        return L


@register("d1_tam")
class TamSource(Source):
    """Tam Immobiliare (Laravel + Livewire): vedi nota d'intestazione del modulo per il protocollo di
    paginazione via evento Livewire "loadNextPage"."""

    def fetch(self, ctx):
        list_url = self.cfg.get("listings_url", "https://www.tamimmobiliare.it/annunci/immobili/vendita/trieste")
        base = self._base(list_url)
        html_txt = ctx.http.text(list_url)
        m = re.search(r'wire:initial-data="([^"]+)"', html_txt)
        if not m:
            raise ValueError("componente Livewire non trovato in pagina (struttura cambiata?)")
        state = json.loads(html_mod.unescape(m.group(1)))
        items = list(state["serverMemo"]["data"].get("immobili") or [])
        total = (state["serverMemo"]["data"].get("immobiliData") or {}).get("total", len(items))
        seen = {it["id"] for it in items}
        # Laravel protegge la rotta Livewire con CSRF: serve il cookie XSRF-TOKEN (impostato dalla GET
        # appena fatta) ripetuto nell'header X-XSRF-TOKEN, altrimenti risponde 419 "CSRF token mismatch".
        from urllib.parse import unquote
        xsrf = ctx.http.session.cookies.get("XSRF-TOKEN")
        headers = {"X-Livewire": "true", "Accept": "application/json"}
        if xsrf:
            headers["X-XSRF-TOKEN"] = unquote(xsrf)
        guard = 0
        while len(items) < total and guard < 20:
            guard += 1
            body = {
                "fingerprint": state["fingerprint"],
                "serverMemo": state["serverMemo"],
                "updates": [{"type": "fireEvent", "payload": {"id": "d1tam", "event": "loadNextPage", "params": []}}],
            }
            try:
                r = ctx.http.post(f"{base}/livewire/message/{state['fingerprint']['name']}",
                                   json=body, headers=headers)
            except Exception:
                break
            data = r.json()
            state["serverMemo"] = data["serverMemo"]
            added = 0
            for it in state["serverMemo"]["data"].get("immobili") or []:
                if it["id"] not in seen:
                    seen.add(it["id"])
                    items.append(it)
                    added += 1
            if added == 0:
                break
        if not items:
            raise ValueError("nessun immobile nel componente Livewire (struttura cambiata?)")
        if len(items) < total:
            ctx.errors.append(f"paginazione Livewire incompleta: {len(items)}/{total} immobili letti")
        ctx.log(f"{len(items)} immobili nel componente Livewire (dichiarati: {total})")
        out, details = [], 0
        for it in items:
            if (it.get("contratto") or "").lower() != "vendita":
                continue
            iid = str(it["id"])
            url = f"{base}/annuncio/immobile/vendita/trieste/{it.get('slug') or 'immobile'}/{iid}"
            L = Listing(source=self.id, ref=iid, url=url)
            L.title = (it.get("nome") or "").strip() or None
            L.address = it.get("indirizzo") or None
            L.price = pu.parse_price(it.get("prezzo"))
            L.mq = pu.parse_mq(it.get("superficie"))
            try:
                L.rooms = int(float(it["vani"])) if it.get("vani") not in (None, "") else None
            except (TypeError, ValueError):
                pass
            if it.get("bagni") not in (None, ""):
                try:
                    L.bathrooms = int(it["bagni"])
                except (TypeError, ValueError):
                    pass
            if it.get("piano") not in (None, ""):
                L.floor = str(it["piano"])
            if L.address:
                L.town = detect_town(L.address) or L.town
                L.zone = detect_zone(L.title, L.address) or L.zone
            thumb = abs_url(url, it.get("image")) if it.get("image") else None
            if ctx.store.needs_detail(L.id, L.price) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(url)
                    details += 1
                    L = parse_detail(dhtml, url, L.source, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            if not L.images and thumb:
                L.images = [thumb]
            out.append(L)
        return out

    @staticmethod
    def _base(url):
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"


@register("d1_generic_tszone")
class GenericTriesteZoneSource(GenericSource):
    """Come `generic`, ma se il comune non è mai citato esplicitamente prova a dedurlo da un rione di
    Trieste nominato in titolo/indirizzo/descrizione (agenzie che scrivono solo "Roiano"/"Barcola"/ecc,
    mai la parola "Trieste"). Vedi nota d'intestazione del modulo."""

    def fetch(self, ctx):
        out = super().fetch(ctx)
        for L in out:
            if not L.town:
                zone = detect_zone(L.title, L.address, L.description)
                if zone:
                    L.town, L.zone = "Trieste", zone
        return out


@register("d1_osproperty")
class OsPropertySource(Source):
    """Piattaforma Joomla "com_osproperty" (es. Norbedo). Nella pagina scheda la sidebar "immobili in
    evidenza" usa la stessa classe CSS generica ".price" dei box prezzo del listato: l'estrattore generico
    (che cerca ".price"/[class*=price] nell'ordine del documento) pesca quindi il prezzo di un ALTRO
    annuncio nella sidebar invece di quello vero. Il prezzo vero è in ".price-ribbon-price" nell'intestazione
    (h1); mq/camere/bagni/energia sono righe "Etichetta: valore" nella sezione #propertydetails.
    Accetta gli stessi parametri di `generic` (start_urls, link_regex, exclude_regex, max_pages)."""

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        max_pages = cfg.get("max_pages", 10)
        links: dict[str, None] = {}
        titles: dict[str, str] = {}
        for start in cfg["start_urls"]:
            url, page, visited = start, cfg.get("page_start", 2), set()
            for _ in range(max_pages):
                if not url or url in visited:
                    break
                visited.add(url)
                html_txt = ctx.http.text(url)
                soup = soup_of(html_txt)
                before = len(links)
                for a in soup.find_all("a", href=True):
                    u = abs_url(url, a["href"])
                    if u and link_rx.search(u) and not (excl and excl.search(u)):
                        links.setdefault(u)
                # titolo della scheda già visibile nel listato (es. "558, Largo Pestalozzi (zona)"):
                # ci basta per scartare senza scaricare la scheda i pochi annunci palesemente fuori zona
                # (terreni all'estero ecc.), evitando di riscaricarli a ogni giro solo per buttarli via.
                for a in soup.select("#osPropertyName a[href]"):
                    u = abs_url(url, a["href"])
                    if u:
                        titles[u] = a.get_text(" ", strip=True)
                if len(links) == before:
                    break
                nxt = soup.find("a", rel="next")
                url = abs_url(url, nxt["href"]) if nxt and nxt.get("href") else None
        if not links:
            raise ValueError("nessuna scheda nell'elenco vendita (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details, skipped_foreign = [], 0, 0
        for u in links:
            if _OBVIOUSLY_OUTSIDE_RX.search(titles.get(u, "")):
                skipped_foreign += 1
                continue
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(u)
                    details += 1
                    L = self._detail(dhtml, u, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            if not L.town:
                zone = detect_zone(L.title, L.address, L.description)
                if zone:
                    L.town, L.zone = "Trieste", zone
            out.append(L)
        if skipped_foreign:
            ctx.log(f"{skipped_foreign} schede scartate dal titolo (palesemente fuori zona)")
        return out

    @staticmethod
    def _detail(html, url, L):
        soup = soup_of(html)
        h1 = soup.select_one("h1.property-header-info-name-text, #propertydetails h1")
        if h1:
            name = h1.select_one(".propertyname")
            if name:
                L.title = name.get_text(" ", strip=True) or None
            price_tag = h1.select_one(".price-ribbon-price")
            if price_tag:
                L.price = pu.parse_price(price_tag.get_text(" ", strip=True))
            if re.search(r"\bvenduto\b", h1.get_text(" ", strip=True), re.I):
                L.sold = True
        info = soup.select_one("#propertydetailspage") or soup
        text = info.get_text("\n", strip=True)
        for label, rx in {
            "rooms": r"Camere\s*:\s*(\d+)",
            "bathrooms": r"Bagni\s*:\s*(\d+)",
            "mq": r"Superficie(?: lorda immobile)?\s*:\s*([\d.,]+)",
            "energy": r"Classe energetica\s*:\s*([A-G][0-9+]?)",
        }.items():
            m = re.search(rx, text, re.I)
            if not m:
                continue
            if label == "rooms":
                L.rooms = pu.parse_rooms(m.group(1))
            elif label == "bathrooms":
                L.bathrooms = pu.parse_small_count(m.group(1), ["bagni"])
            elif label == "mq":
                L.mq = pu.parse_mq(m.group(1) + " mq")
            elif label == "energy":
                L.energy = m.group(1).upper()
        images = [abs_url(url, img.get("src")) for img in soup.select(".uk-slideshow-items img")]
        if images:
            L.images = [i for i in images if i][:12]
        L = parse_detail(html, url, L.source, L)
        L.features.pop("_rent", None)
        return L


@register("d1_rizza")
class RizzaSource(Source):
    """Rizza Immobiliare: il tema genera link assoluti con schema "http://" fisso nell'HTML anche se il
    sito serve solo https (la porta 80 risponde 404, non fa redirect) -> le pagine dettaglio non si
    scaricherebbero mai con l'adattatore generico. Stessa logica di `generic`, ma forzando "https://"."""

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        max_pages = cfg.get("max_pages", 5)
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            html_txt = ctx.http.text(start)
            soup = soup_of(html_txt)
            for a in soup.find_all("a", href=True):
                u = abs_url(start, a["href"])
                if u:
                    u = re.sub(r"^http://", "https://", u)
                if u and link_rx.search(u):
                    links.setdefault(u)
        if not links:
            raise ValueError("nessuna scheda nell'elenco vendita (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(u)
                    details += 1
                    L = parse_detail(dhtml, u, L.source, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            if not L.town:
                zone = detect_zone(L.title, L.description)
                if zone:
                    L.town, L.zone = "Trieste", zone
            out.append(L)
        return out


@register("d1_casaimmedia")
class CasaimmediaSource(Source):
    """Casaimmedia Immobiliare: elenco caricato via AJAX (POST /methods/stampaProprieta.php?f=1&t=1&
    rowid=<offset>&rowperpage=<n>), risposta JSON [{"cont": "<totale>"}, {...annuncio...}, ...] paginata
    con `rowid` come offset. `t=1` = vendita (t=2 = affitto)."""

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.casaimmedia.it").rstrip("/")
        endpoint = f"{base}/methods/stampaProprieta.php"
        page_size = 10
        items, offset, total = [], 0, None
        for _ in range(30):
            r = ctx.http.post(endpoint, params={"f": 1, "t": 1, "rowid": offset, "rowperpage": page_size})
            data = r.json()
            if not data:
                break
            total = int(data[0].get("cont", total or 0))
            batch = data[1:]
            if not batch:
                break
            items.extend(batch)
            offset += page_size
            if offset >= total:
                break
        if not items:
            raise ValueError("nessun annuncio dall'endpoint AJAX (struttura cambiata?)")
        ctx.log(f"{len(items)} annunci letti (dichiarati: {total})")
        out = []
        for it in items:
            iid = str(it.get("id") or "")
            if not iid:
                continue
            url = f"{base}/immobili.php?t=1#{iid}"
            L = Listing(source=self.id, ref=iid, url=url)
            L.title = (it.get("titolo") or "").strip() or None
            L.description = pu.clean_text(it.get("descrizione"))
            L.price = pu.parse_price(it.get("prezzo"))
            try:
                L.rooms = int(it["nCamere"]) or None
            except (TypeError, ValueError, KeyError):
                pass
            try:
                L.bathrooms = int(it["nBagni"]) or None
            except (TypeError, ValueError, KeyError):
                pass
            if it.get("urlImg"):
                L.images = [f"{base}/{it['urlImg']}"]
            if it.get("isVenduto") == "1":
                L.sold = True
            L.town = detect_town(L.title, L.description) or L.town
            if not L.town:
                zone = detect_zone(L.title, L.description)
                if zone:
                    L.town, L.zone = "Trieste", zone
            out.append(L)
        return out


@register("d1_mazzini")
class MazziniSource(Source):
    """Mazzini Immobiliare: sito vetusto (tabelle HTML, jQuery 1.4.2) della web agency Arcube, niente
    endpoint REST. L'elenco vendita non ha una pagina fissa: si ottiene con una POST a
    immobili-trieste.php (modalita_id=1 vendita, sottocategorie_id_form=<id categoria>), una per categoria
    (1 appartamento, 2 casa/villa, 4 ufficio, 7 magazzino, 9 terreno, 12 box/posto auto). La pagina scheda
    non usa dt/dd né "Label: valore" su una riga sola: prezzo/mq sono in un <h3>, l'indirizzo in un <h2>, e
    gli altri dati sono coppie <font>Label:</font> valore<br> in un unico paragrafo."""

    CATEGORIES = (1, 2, 4, 7, 9, 12)
    _FONT_RX = re.compile(r"<font[^>]*>([^:<]+):</font>\s*([^<]*?)\s*<br", re.I)

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.mazzini-immobiliare.it").rstrip("/")
        search_url = f"{base}/immobili-trieste.php"
        links: dict[str, None] = {}
        for cat in self.CATEGORIES:
            r = ctx.http.post(search_url, data={"modalita_id": "1", "sottocategorie_id_form": str(cat)})
            for m in re.finditer(r"scheda_\w+\.php\?id=\d+", r.text):
                links.setdefault(abs_url(search_url, m.group(0)))
        if not links:
            raise ValueError("nessuna scheda nell'elenco vendita (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(u)
                    details += 1
                    L = self._detail(dhtml, u, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    @classmethod
    def _detail(cls, html, url, L):
        soup = soup_of(html)
        h1 = soup.select_one(".box-main h1")
        h2 = soup.select_one(".box-main h2")
        h3 = soup.select_one(".box-main h3")
        loc = h2.get_text(" ", strip=True) if h2 else None
        L.title = " - ".join(x for x in (h1.get_text(" ", strip=True) if h1 else None, loc) if x) or None
        L.address = loc
        if loc:
            L.town = detect_town(loc) or L.town
            L.zone = detect_zone(loc) or L.zone
        if h3:
            t = h3.get_text(" ", strip=True)
            m = re.search(r"([\d.,]+)\s*mq", t, re.I)
            if m:
                L.mq = pu.parse_mq(m.group(1) + " mq")
            L.price = pu.parse_price(t)
        fields = dict(cls._FONT_RX.findall(html))
        fields = {k.strip().lower(): v.strip() for k, v in fields.items()}
        if fields.get("piano"):
            L.floor = fields["piano"]
        m_en = re.match(r"^(A4|A3|A2|A1|A\+|[A-G])$", fields.get("classe energetica", "").upper())
        if m_en:
            L.energy = m_en.group(1)
        if fields.get("composizione"):
            L.rooms = pu.parse_rooms(fields["composizione"])
        if fields.get("ascensore"):
            L.features["elevator"] = fields["ascensore"].lower().startswith(("s", "y"))
        if fields.get("posto auto"):
            L.features["garage"] = fields["posto auto"].lower().startswith(("s", "y"))
        m = re.search(r"descrizione:</font>\s*<br\s*/?>\s*(.*?)</p>", html, re.I | re.S)
        if m:
            desc_html = m.group(1)
            L.description = pu.clean_text(soup_of(desc_html).get_text(" ", strip=True))
        L = parse_detail(html, url, L.source, L)
        return L


@register("d1_altipiano")
class AltipianoSource(Source):
    """Altipiano Immobiliare di Candotti: sito PHP proprietario, elenco intero in homepage
    (?casa=<id>). Scheda con markup minimale: titolo in <h4>, descrizione in un <div class="decription">
    (sic, refuso del sito) spesso troppo corta per l'estrattore generico (soglia minima 150 caratteri),
    prezzo in <div class="price">, foto tramite getImage.php?id=<id>&size=<n> (usiamo size=800)."""

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.altipianoimmobiliare.it").rstrip("/")
        home = ctx.http.text(base + "/")
        ids = sorted(set(re.findall(r"[Cc]asa=(\d+)", home)), key=int)
        if not ids:
            raise ValueError("nessun annuncio in homepage (struttura cambiata?)")
        ctx.log(f"{len(ids)} schede trovate")
        out, details = [], 0
        for cid in ids:
            url = f"{base}/Le_Nostre_Offerte.php?casa={cid}"
            L = Listing(source=self.id, ref=cid, url=url)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(url)
                    details += 1
                    L = self._detail(dhtml, url, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            if not L.town:
                zone = detect_zone(L.title, L.description)
                if zone:
                    L.town, L.zone = "Trieste", zone
            out.append(L)
        return out

    @staticmethod
    def _detail(html, url, L):
        soup = soup_of(html)
        h4 = soup.select_one(".centertitle h4")
        if h4:
            L.title = h4.get_text(" ", strip=True) or None
        desc = soup.select_one(".decription")
        if desc:
            L.description = pu.clean_text(desc.get_text(" ", strip=True))
        price = soup.select_one(".price")
        if price:
            L.price = pu.parse_price(price.get_text(" ", strip=True))
        ids = re.findall(r"getImage\.php\?id=(\d+)", html)
        L.images = [abs_url(url, f"getImage.php?id={i}&size=800") for i in dict.fromkeys(ids)][:12]
        L = parse_detail(html, url, L.source, L)
        return L


@register("d1_grm")
class GrmSource(Source):
    """Gestionale Miogest (GRM Immobiliare): il listato si carica via POST a /ajax.html?azi=Archivio
    (serve Src_Li_Tip=V per la vendita, altrimenti l'endpoint risponde con un errore .NET). La pagina
    scheda ha però dati più affidabili e completi del riquadro nel listato: coppie etichetta/valore in
    .col-cars .s-car (Locali/Camere/Mq/Classe energetica...), indirizzo vero in .col-mappa .indirizzo
    (l'estrattore generico prenderebbe invece l'indirizzo dell'agenzia dal JSON-LD, uguale su ogni pagina)."""

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.grmimmobiliare.it").rstrip("/")
        list_page = f"{base}/it/vendite/"
        ctx.http.text(list_page)  # instaura i cookie di sessione richiesti dall'endpoint ajax
        form = {
            "H_Url": list_page, "Src_Li_Tip": "V", "Src_Li_Cat": "", "Src_Li_Cit": "", "Src_Li_Zon": "",
            "Src_T_Pr1": "", "Src_T_Pr2": "", "Src_T_Mq1": "", "Src_T_Mq2": "", "Src_T_Cod": "", "Src_Li_Ord": "",
        }
        links: dict[str, None] = {}
        for page in range(1, 10):
            r = ctx.http.post(f"{base}/ajax.html", params={"azi": "Archivio", "lin": "it", "n": str(page)},
                               data=form, headers={"X-Requested-With": "XMLHttpRequest", "Referer": list_page})
            hrefs = re.findall(r'<a href="([^"]+)"[^>]*class="annuncio', r.text)
            before = len(links)
            for h in hrefs:
                links.setdefault(abs_url(base, h))
            if len(links) == before:
                break
        if not links:
            raise ValueError("nessun annuncio dall'endpoint ajax (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            # l'URL finisce con "-<id_agenzia>-<id_annuncio>" (es. "...-5173-41"): ref_from_url prenderebbe
            # solo numeri di 3+ cifre e confonderebbe ogni annuncio con lo stesso "5173" (uguale per tutti,
            # è l'id dell'agenzia) -> usiamo l'ultimo segmento numerico dopo il trattino, quale che sia.
            m = re.search(r"-(\d+)$", urlparse(u).path.rstrip("/"))
            ref = m.group(1) if m else ref_from_url(u)
            L = Listing(source=self.id, ref=ref, url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(u)
                    details += 1
                    L = self._detail(dhtml, u, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    @staticmethod
    def _detail(html, url, L):
        soup = soup_of(html)
        h1 = soup.select_one("h1")
        title = None
        if h1:
            title = h1.get_text(" ", strip=True)
            if "immobiliare" in title.lower() or "grm" in title.lower():
                title = None
        og = soup.find("meta", property="og:title")
        if not title and og and og.get("content"):
            title = og["content"].split("•")[0].strip() or None
        L.title = title
        desc = soup.select_one(".col-desc .cb-con")
        if desc:
            L.description = pu.clean_text(desc.get_text(" ", strip=True))
        fields = {}
        for car in soup.select(".col-cars .s-car"):
            val = car.select_one(".val")
            if not val:
                continue
            label = car.get_text(" ", strip=True).replace(val.get_text(" ", strip=True), "").strip(" :")
            fields[label.lower()] = val.get_text(" ", strip=True)
        if fields.get("mq"):
            L.mq = pu.parse_mq(fields["mq"] + " mq")
        if fields.get("locali"):
            L.rooms = pu.parse_rooms(fields["locali"])
        if fields.get("camere"):
            L.bedrooms = pu.parse_small_count(fields["camere"], ["camere"])
        if fields.get("bagni"):
            L.bathrooms = pu.parse_small_count(fields["bagni"], ["bagni"])
        m_en = re.match(r"^(A4|A3|A2|A1|A\+|[A-G])$", fields.get("classe energetica", "").upper())
        if m_en:
            L.energy = m_en.group(1)
        addr_tag = soup.select_one(".col-mappa .indirizzo")
        if addr_tag:
            addr = addr_tag.get_text(" ", strip=True)
            L.address = addr
            L.town = detect_town(addr) or L.town
            L.zone = detect_zone(addr) or L.zone
        elif fields.get("città"):
            L.town = detect_town(fields["città"]) or L.town
            L.zone = detect_zone(fields["città"]) or L.zone
        imgs = [abs_url(url, img.get("src")) for img in soup.select("#carousel-photo img")]
        L.images = [i for i in imgs if i][:12]
        if not L.price:
            for obj in jsonld_objects(soup):
                offers = obj.get("offers")
                if isinstance(offers, dict) and offers.get("price"):
                    L.price = pu.parse_price(offers["price"])
                    break
        return L


@register("d1_vesta")
class VestaSource(Source):
    """Immobiliare Vesta: gestionale AgestaNET (agestanet.risorseimmobiliari.it), pagine .aspx localizzate
    (serve &lang=it altrimenti risponde in inglese). Elenco filtrabile con /immobili.aspx?contratto=V
    (V=vendita, A=affitto, S=stagionale). La scheda non ha coppie "Label: valore" su una riga sola (sono
    due <span> separati da un ":" testuale, che l'estrattore generico associa male) e l'indirizzo/JSON-LD
    di pagina è quello della SEDE dell'agenzia, non dell'immobile: l'indirizzo vero è nel tag <p> con
    l'icona "map-marker" accanto al titolo."""

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.vestaimmobiliare.it").rstrip("/")
        r = ctx.http.text(f"{base}/immobili.aspx", params={"contratto": "V"})
        ids = sorted(set(re.findall(r"imm_detail\.aspx\?id=(\d+)", r)), key=int)
        if not ids:
            raise ValueError("nessun annuncio in vendita (struttura cambiata?)")
        ctx.log(f"{len(ids)} schede trovate")
        out, details = [], 0
        for iid in ids:
            url = f"{base}/imm_detail.aspx?id={iid}&lang=it"
            L = Listing(source=self.id, ref=iid, url=url)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(url)
                    details += 1
                    L = self._detail(dhtml, url, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return [L for L in out if not L.features.pop("_skip", False)]

    @staticmethod
    def _detail(html, url, L):
        soup = soup_of(html)
        contratto = soup.select_one(".detail-inline-item.bg-theme")
        if contratto and "affitto" in contratto.get_text(" ", strip=True).lower():
            L.features["_skip"] = True
        h2 = soup.select_one(".detail-maintitle-in h2")
        if h2:
            L.title = h2.get_text(" ", strip=True) or None
        addr_p = soup.select_one(".detail-maintitle-in p")
        if addr_p:
            addr = addr_p.get_text(" ", strip=True)
            L.address = addr
            L.town = detect_town(addr) or L.town
            L.zone = detect_zone(addr, L.title) or L.zone
        fields = {}
        for li in soup.select(".property-detail li"):
            spans = li.find_all("span")
            if len(spans) >= 2:
                fields[spans[0].get("id", "").lower()] = spans[-1].get_text(" ", strip=True)
        if fields.get("divprezzo"):
            L.price = pu.parse_price(fields["divprezzo"])
        if fields.get("divsuperficie"):
            L.mq = pu.parse_mq(fields["divsuperficie"])
        if fields.get("divcamere"):
            L.rooms = pu.parse_rooms(fields["divcamere"])
        if fields.get("divbagni"):
            L.bathrooms = pu.parse_small_count(fields["divbagni"], ["bagni"])
        desc = soup.select_one(".detail-content, .property-description")
        if desc:
            L.description = pu.clean_text(desc.get_text(" ", strip=True))
        imgs = [img.get("src") for img in soup.select(".slider-store img")]
        L.images = [abs_url(url, i) for i in imgs if i][:12]
        return L


@register("d1_bgrealestate")
class BgRealEstateSource(Source):
    """BG Real Estate: SPA React, ma il catalogo completo è esposto senza autenticazione in
    /properties.json (niente scraping HTML). `tipo` = vendita/affitto/nuova-costruzione, `stato` =
    disponibile/venduto/affittato. Le pagine sono client-side ("/proprieta/<id>"): l'URL è valido ma va
    aperto in un browser vero (il fetch diretto restituisce solo lo shell dell'app)."""

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.bgrealestate.it").rstrip("/")
        items = ctx.http.json(f"{base}/properties.json")
        if not items:
            raise ValueError("feed properties.json vuoto o struttura cambiata")
        out = []
        for it in items:
            if it.get("tipo") not in ("vendita", "nuova-costruzione"):
                continue
            iid = str(it.get("id") or it.get("riferimento") or "")
            if not iid:
                continue
            L = Listing(source=self.id, ref=str(it.get("riferimento") or iid), url=f"{base}/proprieta/{iid}")
            L.title = it.get("titolo") or None
            L.description = pu.clean_text(it.get("descrizione"))
            L.price = pu.parse_price(it.get("prezzo"))
            L.mq = pu.parse_mq(it.get("superficie"))
            loc = " ".join(x for x in (it.get("zona"), it.get("sede")) if x)
            L.town = detect_town(loc) or detect_town(it.get("indirizzo"))
            L.zone = detect_zone(loc)
            if (it.get("indirizzo") or "").strip():
                L.address = it["indirizzo"].strip()
            cat = (it.get("categoria") or "").lower()
            L.type = pu.detect_type(cat, L.title)
            if it.get("stato") in ("venduto", "affittato"):
                L.sold = True
            for feat in ("garage", "balcone", "giardino", "ascensore"):
                if it.get(feat) is not None:
                    key = "elevator" if feat == "ascensore" else ("terrace" if feat == "balcone" else feat)
                    L.features[key] = bool(it[feat])
            imgs = it.get("immagini") or []
            L.images = [abs_url(base, i) for i in dict.fromkeys(imgs)][:12]
            out.append(L)
        ctx.log(f"{len(out)} annunci di vendita nel feed ({len(items)} totali)")
        ctx.detail_ids.update(L.id for L in out)
        return out


@register("d1_bonavia")
class BonaviaSource(Source):
    """Bonavia Real Estate Advisory: sito statico ben fatto, l'estrattore generico già funziona bene
    (prezzo/mq/camere/bagni/foto), manca solo il comune: il testo della pagina nomina il rione (es. "San
    Nicolò", "Rive", "Carso") ma non sempre la parola "Trieste" nell'h1/descrizione, mentre il <title> di
    ogni pagina la riporta sempre esplicitamente ("... in vendita a Trieste, ...")."""

    def fetch(self, ctx):
        list_url = self.cfg.get("listings_url", "https://bonaviaadvisory.com/it/immobili/")
        html = ctx.http.text(list_url)
        soup = soup_of(html)
        link_rx = re.compile(r"/it/immobili/[a-z0-9-]+/$")
        links: dict[str, None] = {}
        for a in soup.find_all("a", href=True):
            u = abs_url(list_url, a["href"])
            if u and link_rx.search(u):
                links.setdefault(u)
        if not links:
            raise ValueError("nessuna scheda nell'elenco vendita (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(u)
                    details += 1
                    dsoup = soup_of(dhtml)
                    L = parse_detail(dhtml, u, L.source, L)
                    # la pagina ha anche un blocco "altri immobili" più sotto con il proprio ".price":
                    # l'estrattore generico pesca il primo prezzo "buono" che trova, quindi a volte quello di
                    # un ALTRO annuncio lì sotto invece di questo (che magari è "prezzo su richiesta"). Il
                    # prezzo vero è sempre il PRIMO ".price" in pagina: sovrascriviamo (anche a None).
                    price_tag = dsoup.select_one(".price")
                    if price_tag:
                        L.price = pu.parse_price(price_tag.get_text(" ", strip=True))
                    if not L.town and dsoup.title and dsoup.title.string:
                        L.town = detect_town(dsoup.title.string)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{u}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out


@register("d1_fabris")
class FabrisSource(Source):
    """fabris immobiliare (gestionale "Paginesi"): il catalogo copre tutta Italia (agenzia specializzata in
    capannoni/industriale), l'h1 e il <title> di pagina sono la CATEGORIA (es. "Lombardia Capannoni") o
    riportano sempre "Trieste" in coda come branding del sito, non la località del singolo annuncio ->
    userebbero detect_town in modo fuorviante. Il titolo/prezzo veri sono in ".product-info" (h2
    "product-title" + ".item-catalogue-price"), non sempre presente (prezzo su richiesta): la pagina ha
    anche un blocco "altri prodotti" più sotto con la stessa classe ".item-catalogue-price", per questo lo
    cerchiamo solo dentro ".product-info"."""

    def fetch(self, ctx):
        base = self.cfg.get("base_url", "https://www.fabrisimmobiliaretrieste.it").rstrip("/")
        html = ctx.http.text(base + "/")
        pairs = sorted(set(re.findall(r"catprodottidett\.php\?chiave=prodotto&valore=([\d;]+)", html)))
        if not pairs:
            raise ValueError("nessun annuncio in homepage (struttura cambiata?)")
        ctx.log(f"{len(pairs)} schede trovate")
        out, details = [], 0
        for valore in pairs:
            ref = valore.split(";")[-1]
            url = f"{base}/catprodottidett.php?chiave=prodotto&valore={valore}"
            L = Listing(source=self.id, ref=ref, url=url)
            if ctx.store.needs_detail(L.id) and details < self.max_details:
                try:
                    dhtml = ctx.http.text(url)
                    details += 1
                    L = self._detail(dhtml, url, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    @staticmethod
    def _detail(html, url, L):
        soup = soup_of(html)
        info = soup.select_one(".product-info")
        title = info.select_one(".product-title") if info else None
        if title:
            L.title = title.get_text(" ", strip=True) or None
        desc = soup.select_one('[itemprop="description"]')
        if desc:
            L.description = pu.clean_text(desc.get_text(" ", strip=True))
        L = parse_detail(html, url, L.source, L)
        # il prezzo vero (se pubblicato) è solo dentro ".product-info": la pagina ha anche un blocco "altri
        # prodotti" più sotto con la stessa classe ".item-catalogue-price" di un ALTRO annuncio, che
        # l'estrattore generico potrebbe aver pescato per sbaglio -> sovrascriviamo (anche a None).
        price = info.select_one(".item-catalogue-price") if info else None
        L.price = pu.parse_price(price.get_text(" ", strip=True)) if price else None
        if L.title:
            L.mq = L.mq or pu.parse_mq(L.title)
            L.town = detect_town(L.title) or L.town
            L.zone = detect_zone(L.title) or L.zone
        if not L.town and L.description:
            L.town = detect_town(L.description) or L.town
            L.zone = detect_zone(L.description) or L.zone
        return L
