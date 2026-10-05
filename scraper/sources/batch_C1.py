"""Batch C1 - agenzie di Trieste su gestionali (Getrix, AgestaNET, GestionaleImmobiliare.it, Gestim,
Miogest) o siti realizzati con CMS/builder (Wix/Aruba/Jimdo/Joomla/Drupal/Squarespace/Next.js/Swanet...).

Adattatori specifici (parametrici, riusati da più agenzie quando la piattaforma è la stessa):
- C1_gestim: agenzie sul gestionale Gestim (cdn*.gestim.biz). L'elenco si legge dal sitemap-immobili.xml
  (contiene sia /i-<id>-vendita-<slug>/ che /i-<id>-affitto-<slug>/): teniamo solo i primi. La scheda ha
  etichette in coppie "<div class='row g-0'><div><strong>Etichetta</strong></div><div>Valore</div></div>".
- C1_getrix_asp: siti "storici" in ASP con /web/immobili.asp?tipo_contratto=V e
  /web/immobile_dettaglio.asp?cod_annuncio=N (etichettati "Getrix" o "AgestaNET" nella ricerca, ma stesso
  template). La scheda ripete due volte le caratteristiche: una prima riga di icone col valore PRIMA
  dell'etichetta (es. "4 Vani") che il kv_pairs generico legge male abbinandolo all'etichetta successiva,
  e una seconda tabella pulita etichetta->valore. Qui teniamo l'ULTIMA occorrenza di ogni etichetta, che è
  sempre quella della tabella pulita.
- C1_gestionaleimmobiliare: piattaforma Fox Group "GestionaleImmobiliare.it" (Zenith Real Estate). Elenco
  con paginazione ?=&page=N; scheda con icone Font Awesome (fa-bed/fa-bath/fa-home) per camere/bagni/mq,
  tabella per il piano, classe energetica nell'immagine APE (commento HTML "sigla classe"), lat/lon in
  <span id="latitude_hidden"/"longitude_hidden">.
- C1_arcasa: plugin WordPress "Arcasa" (agenzia web Arcube) usato da Equipe Immobiliare. Categorie
  /immobili/<tipo>/?acl-contract=0 = vendita. Card e scheda hanno gli stessi elementi <p class="arcasa-*">.
- C1_gallery_sanity: Gallery Immobiliare Trieste espone il catalogo (dati Getrix) tramite un progetto
  Sanity.io pubblico (CDN GROQ, nessuna autenticazione): interroghiamo direttamente l'API invece di
  scaricare le pagine React lato client.
- C1_rigatti: CMS Swanet ("earth_tng"). La pagina elenco (per comune) mostra card "listing-item" con
  badge Vendita/Affitto e già mq/camere/bagni/prezzo/indirizzo: li leggiamo da lì, la scheda serve solo
  per la descrizione e le foto.
- C1_larue: sito Next.js "custom" senza dati incorporati nella pagina elenco: l'elenco si legge dal
  sitemap.xml (URL con id CUID), la scheda è renderizzata lato server per testo/prezzo/indirizzo ma i
  "Dettagli" strutturati (mq/locali/ecc.) sono caricati lato client (placeholder "animate-pulse"): mq e
  locali si ricavano quindi dal testo della descrizione.
- C1_generic_ts: come l'adattatore 'generic' condiviso, ma con qualche aiuto per riconoscere il comune
  quando la scheda cita solo via/rione (frequente nei siti di piccole agenzie): comune dall'URL, indirizzo
  completo da un tag dedicato, comune dedotto dal rione riconosciuto nel testo, uno scarto dell'indirizzo
  quando è in realtà quello della SEDE dell'agenzia (non dell'immobile), e un default a Trieste quando non
  si riconosce nessun comune né un indizio di luogo fuori provincia. Usato per TIQUADRO, Studio Immobiliare
  84, Casaffari, Immobiliare Rossetti, imobilia e Trieste Villas.
"""
import re

from .. import parse_utils as pu
from ..models import Listing
from ..zones import detect_town
from . import register
from .base import Source, abs_url, parse_detail, ref_from_url, soup_of

ENERGY_RE = re.compile(r"\b(A4|A3|A2|A1|A\+|[A-G])\b")


def _geo_bounds(lat, lon) -> tuple[float, float] | None:
    """Posizione plausibile per l'area di Trieste e dintorni (Triveneto per Rigatti escluso)."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return (lat, lon) if 45.2 <= lat <= 46.2 and 13.0 <= lon <= 14.3 else None


# ================================================================== Gestim (Andrea Oliva, Di Casa in Casa)

@register("C1_gestim")
class GestimSource(Source):
    """Agenzie sul gestionale Gestim: sitemap-immobili.xml con /i-<id>-vendita-<slug>/ o -affitto-."""

    def fetch(self, ctx):
        sitemap = self.cfg["sitemap_url"]
        xml = ctx.http.text(sitemap)
        urls = sorted(set(m.strip() for m in re.findall(r"<loc>\s*([^<]*?/i-\d+-vendita-[^<]+?)\s*</loc>", xml)))
        if not urls:
            raise ValueError("sitemap Gestim senza annunci di vendita (struttura cambiata?)")
        ctx.log(f"{len(urls)} annunci di vendita nel sitemap")
        out, details = [], 0
        for u in urls:
            ref = re.search(r"/i-(\d+)-", u).group(1)
            L = Listing(source=self.id, ref=ref, url=u)
            if details < self.max_details and ctx.store.needs_detail(L.id):
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

    def _detail(self, html, url, L):
        L = parse_detail(html, url, self.id, L)
        # sotto la scheda vera compaiono card di 'immobili simili' con le stesse icone/etichette:
        # tagliamo l'HTML lì per non leggere i loro valori come se fossero di questo annuncio.
        cut = re.search(r"potrebbero interessarti|immobili simili", html, re.I)
        main_html = html[:cut.start()] if cut else html
        main_soup = soup_of(main_html)
        kv = {}
        # variante A (es. Andrea Oliva): due colonne, <strong>Etichetta</strong> in una e il valore nell'altra
        for row in main_soup.select("div.row.g-0"):
            divs = row.find_all("div", recursive=False)
            if len(divs) < 2:
                continue
            strong = divs[0].find("strong")
            if not strong:
                continue
            label = strong.get_text(strip=True).lower().rstrip(":")
            value = divs[1].get_text(" ", strip=True)
            if value and label not in kv:
                kv[label] = value
        # variante B (es. Di Casa in Casa): colonna singola "Etichetta : Valore" (anche dentro <strong>)
        for m in re.finditer(r'col-12[^"]*border-bottom"[^>]*>\s*(?:<strong>)?\s*([^<:]{2,40}):\s*([^<]{1,100})', main_html):
            label = m.group(1).strip().lower()
            value = m.group(2).strip()
            if value and label not in kv:
                kv[label] = value
        # icone camere/bagni/mq (solo nella parte principale, non negli 'immobili simili' più sotto)
        icon_kv = {}
        for m in re.finditer(r'ico-([a-z]+)\.png"\s*/>\s*<span>([^<]*)</span>', main_html):
            icon_kv.setdefault(m.group(1), m.group(2))
        L.mq = L.mq or pu.parse_mq(kv.get("totale mq") or kv.get("superficie") or icon_kv.get("mq"))
        L.rooms = L.rooms or pu.parse_rooms(kv.get("locali"))
        L.bedrooms = L.bedrooms or pu.parse_small_count(kv.get("camere") or icon_kv.get("camere"), ["camere"])
        L.bathrooms = L.bathrooms or pu.parse_small_count(kv.get("bagni") or icon_kv.get("bagni"), ["bagni"])
        L.floor = L.floor or kv.get("piano")
        if kv.get("classe energetica") and not L.energy:
            m = ENERGY_RE.search(kv["classe energetica"])
            if m:
                L.energy = m.group(1)
        if kv.get("stato conservazione") and not L.condition:
            L.condition = pu.detect_condition(kv["stato conservazione"])
        if kv.get("categoria") and (not L.type or L.type == "altro"):
            L.type = pu.detect_type(kv["categoria"] + " ")
        if kv.get("comune"):
            L.town = detect_town(kv["comune"]) or kv["comune"]
        if kv.get("zona") and not L.zone:
            L.zone = kv["zona"]
        if kv.get("indirizzo") and not L.address:
            L.address = kv["indirizzo"]
        folder = next((m.group(1) for m in re.finditer(r"/custom/(\d+)/", main_html)), None)
        if folder:
            imgs = re.findall(
                r'https://cdn\d*\.gestim\.biz/custom/' + folder + r'/foto/(?!thumb/)[^"\'\s)]+\.(?:jpe?g|png|webp)',
                main_html, re.I)
            if imgs:
                L.images = list(dict.fromkeys(imgs))[:12]
        return L


# ================================================================== Getrix/AgestaNET ASP (4 agenzie)

_ASP_LABELS = {
    "superficie": "mq", "vani": "rooms", "camere": "bedrooms", "bagni": "bathrooms",
    "piano": "floor", "classe energetica": "energy", "comune": "town", "indirizzo": "address",
    "prezzo": "price", "condizioni": "condition",
}


@register("C1_getrix_asp")
class GetrixAspSource(Source):
    """Siti ASP con /web/immobili.asp?tipo_contratto=V + /web/immobile_dettaglio.asp?cod_annuncio=N
    (Gruppo Casa, Giulia Immobiliare, Cheni e Tutta, Casa Programma): teniamo l'ULTIMA occorrenza di ogni
    etichetta nel testo della scheda, che è sempre quella della tabella pulita (la prima, in ordine
    valore-poi-etichetta, produce abbinamenti sbagliati col kv_pairs generico).

    Eccezione per le etichette numeriche (superficie, vani, camere, bagni, prezzo): su Casa Programma
    'Superficie' ricompare una terza volta più sotto, nella tabella di dettaglio "Consistenze", ma lì è
    la cella di INTESTAZIONE della colonna ("Sup. comm.") e non un valore — se la si accetta come ultima
    occorrenza si perde la vera superficie letta poco prima. Per queste etichette si scarta quindi un
    candidato senza cifre, tenendo comunque l'ultima occorrenza valida (le altre agenzie del lotto non
    hanno questa terza tabella, quindi per loro il comportamento resta identico)."""

    def fetch(self, ctx):
        links: dict[str, None] = {}
        for start in self.cfg["start_urls"]:
            html = ctx.http.text(start)
            soup = soup_of(html)
            for a in soup.find_all("a", href=True):
                u = abs_url(start, a["href"])
                if u and re.search(r"immobile_dettaglio\.asp\?cod_annuncio=\d+", u):
                    links.setdefault(u, None)
        if not links:
            raise ValueError("nessuna scheda immobile_dettaglio.asp trovata (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        seen_refs = set()
        for u in links:
            ref = re.search(r"cod_annuncio=(\d+)", u).group(1)
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            L = Listing(source=self.id, ref=ref, url=u)
            if details < self.max_details and ctx.store.needs_detail(L.id):
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
        L = parse_detail(html, url, L.source, L)
        lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
        kv = {}
        _numeric_fields = {"mq", "rooms", "bedrooms", "bathrooms", "price"}
        for i in range(len(lines) - 1):
            key = lines[i].rstrip(":").strip().lower()
            if key in _ASP_LABELS and len(lines[i + 1]) < 60:
                field = _ASP_LABELS[key]
                if field in _numeric_fields and not re.search(r"\d", lines[i + 1]):
                    continue  # cella di intestazione ("Sup. comm."), non un valore: non sovrascrivere
                kv[field] = lines[i + 1]   # sovrascrive: vince l'ultima occorrenza valida
        if kv.get("mq"):
            L.mq = pu.parse_mq(kv["mq"]) or L.mq
        if kv.get("rooms"):
            L.rooms = pu.parse_rooms(kv["rooms"]) or L.rooms
        if kv.get("bedrooms"):
            L.bedrooms = pu.parse_small_count(kv["bedrooms"], ["camere"]) or L.bedrooms
        if kv.get("bathrooms"):
            L.bathrooms = pu.parse_small_count(kv["bathrooms"], ["bagni"]) or L.bathrooms
        if kv.get("floor"):
            L.floor = kv["floor"]
        if kv.get("energy"):
            m = ENERGY_RE.search(kv["energy"])
            if m:
                L.energy = m.group(1)
        if kv.get("town"):
            L.town = detect_town(kv["town"]) or kv["town"]
        if kv.get("address"):
            L.address = kv["address"]
        if kv.get("price"):
            p = pu.parse_price(kv["price"])
            if p:
                L.price = p
        if kv.get("condition"):
            L.condition = pu.detect_condition(kv["condition"]) or L.condition
        return L


# ================================================================== GestionaleImmobiliare.it (Zenith)

@register("C1_gestionaleimmobiliare")
class GestionaleImmobiliareSource(Source):
    """Fox Group 'GestionaleImmobiliare.it': elenco paginato ?=&page=N, scheda con icone FA per
    camere/bagni/mq, tabella per il piano, classe energetica nel commento HTML dell'immagine APE."""
    LINK_RX = re.compile(r"^https?://[^/]+/it/[a-z0-9-]+-\d{6,}$", re.I)

    def fetch(self, ctx):
        start = self.cfg["start_url"]
        page_template = self.cfg["page_template"]
        max_pages = self.cfg.get("max_pages", 12)
        links: dict[str, None] = {}
        for page in range(1, max_pages + 1):
            url = start if page == 1 else page_template.format(n=page)
            html = ctx.http.text(url)
            soup = soup_of(html)
            before = len(links)
            for a in soup.find_all("a", href=True):
                u = abs_url(url, a["href"])
                if not u:
                    continue
                bare = u.split("?")[0]
                if self.LINK_RX.match(bare):
                    links.setdefault(bare, None)
            if len(links) == before and page > 1:
                break
        if not links:
            raise ValueError("nessuna scheda trovata nell'elenco (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if details < self.max_details and ctx.store.needs_detail(L.id):
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
        L = parse_detail(html, url, L.source, L)
        for i in soup.find_all("i", class_=lambda c: c and "fas" in c):
            sp = i.find_next_sibling("span")
            if not sp:
                continue
            cls = " ".join(i.get("class") or [])
            txt = sp.get_text(" ", strip=True)
            if "fa-bed" in cls:
                L.bedrooms = L.bedrooms or pu.parse_small_count(txt, ["camere"])
            elif "fa-bath" in cls:
                L.bathrooms = L.bathrooms or pu.parse_small_count(txt, ["bagni"])
            elif "fa-home" in cls:
                m = re.search(r"\d+", txt)
                if m and not L.mq:
                    L.mq = int(m.group())
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) != 2:
                continue
            strong = tds[0].find("strong")
            if not strong:
                continue
            label = strong.get_text(strip=True).lower()
            value = tds[1].get_text(" ", strip=True)
            if "piano" in label and value and not L.floor:
                L.floor = value
        m = re.search(r'>([A-G])</div><!--\s*sigla classe\s*-->', html)
        if m and not L.energy:
            L.energy = m.group(1)
        mlat = re.search(r'id="latitude_hidden">([\d.]+)<', html)
        mlon = re.search(r'id="longitude_hidden">([\d.]+)<', html)
        if mlat and mlon:
            g = _geo_bounds(mlat.group(1), mlon.group(1))
            if g:
                L.lat, L.lon, L.geo = g[0], g[1], "fonte"
        # la galleria (16 foto a piena risoluzione) non è in tag <img>: è in un array/CSS non intercettato
        # dall'estrattore generico, quindi la cerchiamo per URL diretto.
        imgs = re.findall(r'https://images\.gestionaleimmobiliare\.it/foto/annunci/\d+/\d+/1280x1280/[^"\'\s)]+\.jpe?g',
                           html, re.I)
        if imgs:
            L.images = list(dict.fromkeys(imgs))[:12]
        return L


# ================================================================== Arcasa / Arcube WP plugin (Equipe)

_ARCASA_LABELS = {"metratura": "mq", "vani": "rooms", "camere": "bedrooms", "bagni": "bathrooms",
                  "condizioni": "condition", "piano": "floor"}


@register("C1_arcasa")
class ArcasaSource(Source):
    """Plugin WordPress 'Arcasa' (agenzia web Arcube), es. Equipe Immobiliare: categorie
    /immobili/<tipo>/?acl-contract=0 = vendita; card e scheda condividono le stesse classi 'arcasa-*'."""

    def fetch(self, ctx):
        links: dict[str, None] = {}
        for cat_url in self.cfg["category_urls"]:
            html = ctx.http.text(cat_url)
            soup = soup_of(html)
            for it in soup.select("div.arcasa-single-listing"):
                a = it.select_one("a[href]")
                if not a:
                    continue
                u = abs_url(cat_url, a["href"])
                if u:
                    links.setdefault(u.rstrip("/"), None)
        if not links:
            raise ValueError("nessun 'arcasa-single-listing' trovato (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if details < self.max_details and ctx.store.needs_detail(L.id):
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
        L = parse_detail(html, url, L.source, L)
        h2 = soup.select_one("div.tab-title h2")
        if h2 and h2.get_text(strip=True):
            L.title = h2.get_text(" ", strip=True)
        for p in soup.select("p.arcasa-info-list-item"):
            txt = p.get_text(" ", strip=True)
            low = txt.lower()
            for label, key in _ARCASA_LABELS.items():
                if low.startswith(label):
                    val = txt[len(label):].strip()
                    if key == "mq":
                        # "1.400 m 2": il numero può avere il punto delle migliaia
                        n = pu.to_int(val)
                        if n and not L.mq:
                            L.mq = n
                    elif key == "rooms":
                        L.rooms = L.rooms or pu.parse_rooms(val)
                    elif key == "bedrooms":
                        L.bedrooms = L.bedrooms or pu.parse_small_count(val, ["camere"])
                    elif key == "bathrooms":
                        L.bathrooms = L.bathrooms or pu.parse_small_count(val, ["bagni"])
                    elif key == "condition":
                        L.condition = L.condition or pu.detect_condition(val)
                    elif key == "floor":
                        # "Il piano a cui si trova 4": il valore vero è l'ultima parola del testo.
                        # Sovrascrive sempre: il kv_pairs generico a volte prende il testo del tooltip
                        # da solo (senza il numero finale), scambiandolo per il piano.
                        words = val.split()
                        if words:
                            L.floor = words[-1]
                    break
        mt = soup.select_one("div.map-title")
        if mt:
            loc = mt.select_one(".arcasa-location")
            if loc:
                town_txt = loc.get_text(" ", strip=True)
                L.town = detect_town(town_txt) or town_txt or L.town
                full = mt.get_text(" ", strip=True)
                addr = full.replace(town_txt, "", 1).strip()
                if addr:
                    L.address = addr
        m = re.search(r'initMap\("[^"]+",\s*([\d.]+),\s*([\d.]+)', html)
        if m:
            g = _geo_bounds(m.group(2), m.group(1))   # initMap(id, lon, lat, ...)
            if g:
                L.lat, L.lon, L.geo = g[0], g[1], "fonte"
        heading = soup.select_one("h3.arcasa-info-heading")
        if heading and pu.detect_sold(heading.get_text(" ", strip=True)):
            L.sold = True
        return L


# ================================================================== Gallery Immobiliare (Sanity.io / Getrix)

@register("C1_gallery_sanity")
class GallerySanitySource(Source):
    """Gallery Immobiliare Trieste: il catalogo (dati Getrix) è esposto da un progetto Sanity.io pubblico,
    interrogabile in sola lettura via GROQ senza autenticazione: molto più affidabile della pagina React."""
    PROJECT = "2unles39"
    DATASET = "production"
    API = f"https://{PROJECT}.apicdn.sanity.io/v2025-03-13/data/query/{DATASET}"
    QUERY = """*[_type=="property" && contratto=="V"]{
      getrixID, referenceID, comune, quartiere, indirizzo, civico, pubblicaIndirizzo, pubblicaCivico,
      latitudine, longitudine, mqSuperficie, nrLocali, nrCamere, nrBagni, classeEnergetica,
      ascensore, prezzo, trattativaRiservata,
      titolo, descrizioni, "slugIt": slug[_key=="it"][0].value.current, immagini
    }"""

    def fetch(self, ctx):
        j = ctx.http.json(self.API, params={"query": self.QUERY})
        items = j.get("result")
        if not items:
            raise ValueError("nessun immobile in vendita nel dataset Sanity (struttura cambiata?)")
        ctx.log(f"{len(items)} annunci di vendita")
        out = []
        for it in items:
            try:
                L = self._parse(it)
            except Exception as e:
                ctx.errors.append(f"{it.get('getrixID')}: {e}")
                continue
            out.append(L)
        ctx.detail_ids.update(L.id for L in out)
        return out

    def _parse(self, it):
        ref = str(it.get("getrixID") or it.get("referenceID"))
        slug = it.get("slugIt") or it.get("referenceID")
        url = f"https://www.galleryimmobiliare.it/it/immobili/{slug}"
        title = next((t.get("value") for t in it.get("titolo") or [] if t.get("_key") == "it"), None)
        desc = next((d.get("value") for d in it.get("descrizioni") or [] if d.get("_key") == "it"), None)
        price = None
        if not it.get("trattativaRiservata") and it.get("prezzo") not in (False, None):
            price = pu.parse_price(it.get("prezzo"))
        address = None
        if it.get("pubblicaIndirizzo") and it.get("indirizzo"):
            address = it["indirizzo"]
            if it.get("pubblicaCivico") and it.get("civico"):
                address = f"{address}, {it['civico']}"
        comune = it.get("comune")
        L = Listing(source=self.id, ref=ref, url=url, title=pu.clean_text(title, 200),
                    description=pu.clean_text(desc), price=price,
                    mq=pu.parse_mq(it.get("mqSuperficie")), rooms=pu.parse_rooms(it.get("nrLocali")),
                    bedrooms=pu.parse_small_count(it.get("nrCamere"), ["camere"]),
                    bathrooms=pu.parse_small_count(it.get("nrBagni"), ["bagni"]),
                    town=detect_town(comune) or comune or None, zone=it.get("quartiere") or None,
                    address=address)
        en = (it.get("classeEnergetica") or "").strip().upper()
        if ENERGY_RE.fullmatch(en):
            L.energy = en
        g = _geo_bounds(it.get("latitudine"), it.get("longitudine"))
        if g:
            L.lat, L.lon, L.geo = g[0], g[1], "fonte"
        if it.get("ascensore") is not None:
            L.features["elevator"] = bool(it["ascensore"])
        imgs = []
        for im in it.get("immagini") or []:
            ref_img = ((im.get("asset") or {}).get("_ref")) or im.get("_key")
            u = self._img_url(ref_img)
            if u:
                imgs.append(u)
        L.images = imgs[:12]
        return L

    def _img_url(self, ref):
        m = re.match(r"image-([a-f0-9]+)-(\d+x\d+)-(\w+)$", ref or "")
        if not m:
            return None
        h, dim, ext = m.groups()
        return f"https://cdn.sanity.io/images/{self.PROJECT}/{self.DATASET}/{h}-{dim}.{ext}"


# ================================================================== Rigatti (Swanet / earth_tng)

@register("C1_rigatti")
class RigattiSource(Source):
    """CMS Swanet ('earth_tng'): la pagina elenco per comune mostra card 'listing-item' con badge
    Vendita/Affitto e già mq/camere/bagni/prezzo/indirizzo; la scheda serve solo per descrizione/foto."""

    def fetch(self, ctx):
        html = ctx.http.text(self.cfg["start_url"])
        soup = soup_of(html)
        items = soup.select("div.listing-item")
        if not items:
            raise ValueError("nessuna 'listing-item' trovata (struttura cambiata?)")
        out, details, seen = [], 0, set()
        for it in items:
            badge = it.select_one(".listing-badges")
            if badge and "vendita" not in badge.get_text(" ", strip=True).lower():
                continue
            a = it.select_one("a.listing-img-container[href]") or it.select_one(".listing-title h4 a[href]")
            if not a:
                continue
            url = a["href"]
            ref = ref_from_url(url)
            if ref in seen:
                continue
            seen.add(ref)
            L = self._from_card(it, url, ref)
            if details < self.max_details and ctx.store.needs_detail(L.id, L.price):
                details += 1
                try:
                    self._enrich(ctx, L)
                    ctx.detail_ids.add(L.id)
                except Exception as e:
                    ctx.errors.append(f"{url}: {e}")
                    ctx.merge_known(L)
            else:
                ctx.merge_known(L)
            out.append(L)
        return out

    def _from_card(self, it, url, ref):
        h4s = it.select(".listing-title h4")
        title = h4s[0].get_text(" ", strip=True) if h4s else None
        price = pu.parse_price(h4s[1].get_text(" ", strip=True)) if len(h4s) > 1 else None
        addr_tag = it.select_one("a.listing-address")
        address = addr_tag.get_text(" ", strip=True) if addr_tag else None
        mq = rooms = bedrooms = bathrooms = None
        for li in it.select("ul.listing-details li"):
            t = li.get_text(" ", strip=True)
            if re.search(r"\bmq\b", t, re.I):
                mq = pu.parse_mq(t)
            elif re.search(r"camer", t, re.I):
                bedrooms = pu.parse_small_count(t, ["camere"])
            elif re.search(r"bagn", t, re.I):
                bathrooms = pu.parse_small_count(t, ["bagni"])
            elif re.search(r"local", t, re.I):
                rooms = pu.parse_rooms(t)
        return Listing(source=self.id, ref=ref, url=url, title=title, price=price, address=address,
                       mq=mq, rooms=rooms, bedrooms=bedrooms, bathrooms=bathrooms,
                       town=self.default_town)

    @staticmethod
    def _enrich(ctx, L):
        html = ctx.http.text(L.url)
        base = parse_detail(html, L.url, L.source)   # non passiamo L: non vogliamo perdere title/price della card
        L.description = base.description
        imgs = [u for u in base.images if "/earth_tng/img/property/upload/" in u]
        if imgs:
            L.images = list(dict.fromkeys(imgs))[:12]
        if not L.energy and base.energy:
            L.energy = base.energy


# ================================================================== La Rue Immobiliare (Next.js custom)

@register("C1_larue")
class LaRueSource(Source):
    """Sito Next.js senza dati incorporati nella pagina elenco: l'elenco si legge dal sitemap.xml (URL con
    id CUID); nella scheda i 'Dettagli' strutturati sono caricati lato client (placeholder), quindi mq e
    locali si ricavano dal testo della descrizione."""
    SLUG_RX = re.compile(r"^https://www\.larueimmobiliare\.it/[a-z0-9]{20,30}$")

    def fetch(self, ctx):
        xml = ctx.http.text(self.cfg.get("sitemap_url", "https://www.larueimmobiliare.it/sitemap.xml"))
        urls = sorted(set(m for m in re.findall(r"<loc>([^<]+)</loc>", xml) if self.SLUG_RX.match(m)))
        if not urls:
            raise ValueError("nessun annuncio nel sitemap (struttura cambiata?)")
        ctx.log(f"{len(urls)} annunci trovati")
        out, details = [], 0
        for u in urls:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if details < self.max_details and ctx.store.needs_detail(L.id):
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
        L = parse_detail(html, url, L.source, L)
        h1 = soup.select_one("h1.font-bold.text-2xl") or (soup.find_all("h1") or [None, None])[-1]
        if h1:
            title = h1.get_text(" ", strip=True)
            if title:
                L.title = title
        if not L.mq:
            # i "Dettagli" strutturati sono caricati lato client: cerchiamo tutte le menzioni "NN mq" nel
            # testo della pagina e teniamo la più grande (l'abitazione, non il terrazzo/la cantina).
            nums = [pu.to_int(m.group(0)) for m in re.finditer(r"[\d.]{1,6}\s*mq", soup.get_text(" "), re.I)]
            nums = [n for n in nums if n and 15 <= n <= 2000]
            if nums:
                L.mq = max(nums)
        if not L.rooms:
            L.rooms = pu.parse_rooms(L.title) or pu.parse_rooms(L.description)
        imgs = re.findall(r"https://res\.cloudinary\.com/[^\"'\s]+\.(?:jpe?g|webp|png)", html, re.I)
        if imgs:
            L.images = list(dict.fromkeys(imgs))[:12]
        return L


# ================================================================== generic + comune (6 agenzie)

FOREIGN_HINT = re.compile(r"\(cro\)|\(slo\)|croazia|slovenia|istra\b|savudrija|umago|umag\b|kobdilj|portoroz|"
                          r"gorizia|monfalcone|cervignano|udine|pordenone|tarvisio|venezia|isontino", re.I)


@register("C1_generic_ts")
class GenericTsSource(Source):
    """Come l'adattatore 'generic' (elenco -> link schede -> dettaglio), ma con qualche aiuto in più per
    riconoscere il comune quando la scheda non lo scrive per esteso (frequente nei siti di piccole agenzie
    che citano solo la via o il rione):
      town_from_url_regex: regex con un gruppo sull'URL della scheda (es. '/Vendite/(trieste)/')
      full_address_tag: selettore CSS di un tag che contiene via+comune su righe separate (es. 'address')
      zone_fallback: se un rione di Trieste è riconosciuto nel testo, assume Trieste (default True)
      default_town_if_unrecognized: se non si riconosce nessun comune/rione né un indizio di luogo fuori
        provincia (vedi FOREIGN_HINT), assume Trieste (solo per agenzie quasi tutte triestine)
    """

    def fetch(self, ctx):
        cfg = self.cfg
        link_rx = re.compile(cfg["link_regex"], re.I)
        excl = re.compile(cfg["exclude_regex"], re.I) if cfg.get("exclude_regex") else None
        max_pages = cfg.get("max_pages", 1)
        page_template = cfg.get("page_template")
        links: dict[str, None] = {}
        for start in cfg["start_urls"]:
            url, page = start, cfg.get("page_start", 2)
            for _ in range(max_pages):
                if not url:
                    break
                html = ctx.http.text(url)
                soup = soup_of(html)
                before = len(links)
                for a in soup.find_all("a", href=True):
                    u = abs_url(url, a["href"])
                    if u and link_rx.search(u) and not (excl and excl.search(u)):
                        links.setdefault(u, None)
                if len(links) == before:
                    break
                if not page_template:
                    break
                url, page = page_template.format(n=page), page + 1
        if not links:
            raise ValueError("nessuna scheda trovata (struttura cambiata?)")
        ctx.log(f"{len(links)} schede trovate")
        out, details = [], 0
        for u in links:
            L = Listing(source=self.id, ref=ref_from_url(u), url=u)
            if details < self.max_details and ctx.store.needs_detail(L.id):
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

    def _detail(self, html, url, L):
        soup = soup_of(html)
        L = parse_detail(html, url, self.id, L)
        cfg = self.cfg
        # alcuni siti hanno anche un JSON-LD "RealEstateAgent" (nome dell'agenzia): il tipo generico
        # RealEstate* del JSON-LD lo confonde con l'annuncio e usa il nome dell'agenzia come titolo.
        # L'h1 della pagina (quando c'è un solo h1) resta il titolo più affidabile.
        h1s = soup.find_all("h1")
        if len(h1s) == 1:
            h1_text = h1s[0].get_text(" ", strip=True)
            if h1_text:
                L.title = h1_text
        if cfg.get("full_address_tag") and not L.town:
            tag = soup.select_one(cfg["full_address_tag"])
            if tag:
                full = tag.get_text(" ", strip=True)
                if full:
                    L.address = full
        # alcuni siti mostrano in scheda solo l'indirizzo della SEDE dell'agenzia (non dell'immobile):
        # se combacia, lo scartiamo per non inquinare zona/comune con la posizione dell'ufficio.
        if cfg.get("agency_address_hint") and L.address and cfg["agency_address_hint"].lower() in L.address.lower():
            L.address = None
        if cfg.get("town_from_url_regex") and not L.town:
            m = re.search(cfg["town_from_url_regex"], url, re.I)
            if m:
                L.town = detect_town(m.group(1)) or None
        blob = " ".join(x for x in (L.title, L.address, L.zone, L.description) if x)
        foreign = FOREIGN_HINT.search(blob)   # es. "Altipiano/Carso e altre province" cita "Carso" ma è
                                              # la categoria del sito per gli annunci FUORI provincia
        if not L.town and not foreign and cfg.get("zone_fallback", True):
            from ..zones import detect_zone
            if detect_zone(blob):
                L.town = "Trieste"
        if not L.town and not foreign and cfg.get("default_town_if_unrecognized"):
            L.town = "Trieste"
        return L
