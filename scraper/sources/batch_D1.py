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
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of
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
    """Studio 39 di Tommaseo (gestionale ASP proprietario): elenco su un'unica pagina (niente paginazione),
    pagina scheda con un form di ricerca (select comune/zona/fascia prezzo) che inquina l'estrazione generica
    di testo e coppie etichetta/valore (i valori "di default" dei <select> vengono letti come se fossero i
    dati dell'annuncio). I dati veri sono in un div "box_evidenza" con etichette in <strong> e valore adiacente
    (niente dt/dd, niente "label: valore" su una riga sola -> serve un'estrazione dedicata per prezzo/mq)."""

    LINK_RE = re.compile(r"/immobile/\d+/[^/]+/?$")

    def fetch(self, ctx):
        list_url = self.cfg.get("listings_url", "https://www.studio39immobiliare.it/articles.asp?show=vendita")
        html = ctx.http.text(list_url)
        soup = soup_of(html)
        links: dict[str, None] = {}
        for a in soup.find_all("a", href=True):
            u = abs_url(list_url, a["href"])
            if u and self.LINK_RE.search(u):
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
            r = ctx.http.session.post(
                f"{base}/livewire/message/{state['fingerprint']['name']}",
                json=body, headers=headers, timeout=ctx.http.timeout)
            if r.status_code >= 400:
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
