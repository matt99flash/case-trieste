"use strict";

const TYPE_LABELS = { appartamento: "Appartamento", attico: "Attico / Mansarda", villa: "Villa / Villetta", casa: "Casa indipendente", terreno: "Terreno", box: "Box / Posto auto", commerciale: "Commerciale", altro: "Altro" };
// "altro" incluso: annunci con titolo poco chiaro (spesso solo la via) non devono sparire
const DEFAULT_TYPES = ["appartamento", "attico", "villa", "casa", "altro"];
const COND_LABELS = { nuovo: "Nuova costruzione", ristrutturato: "Ristrutturato / Ottimo", buono: "Buono / Abitabile", da_ristrutturare: "Da ristrutturare", nd: "Non indicato" };
const FEATURES = { elevator: "Ascensore", garage: "Garage / posto auto", terrace: "Terrazzo / balcone", garden: "Giardino", sea_view: "Vista mare" };
const KINDS = { agenzia: "Agenzie", costruttore: "Costruttori", privato: "Privati (Subito)" };
const EV = { new: "Nuovo", price_down: "Ribasso", price_up: "Rialzo", removed: "Non più online", sold: "Venduto", back: "Di nuovo online" };
const TOWN_CENTER = {
  "Trieste": [45.6495, 13.7768], "Muggia": [45.6024, 13.7676], "Duino-Aurisina": [45.7538, 13.6560],
  "San Dorligo della Valle": [45.6140, 13.8560], "Sgonico": [45.7360, 13.7430], "Monrupino": [45.7190, 13.7960],
};
const ZONE_CENTER = {
  "Centro / Borgo Teresiano": [45.6530, 13.7760], "Borgo Giuseppino / Cavana": [45.6470, 13.7690],
  "Città Vecchia / San Giusto": [45.6480, 13.7720], "Borgo Franceschino / Giulia": [45.6515, 13.7850],
  "San Vito / Campi Elisi": [45.6405, 13.7610], "San Giacomo": [45.6390, 13.7780], "Barriera": [45.6450, 13.7820],
  "Chiadino / Rozzol": [45.6440, 13.7960], "San Luigi / Guardiella": [45.6540, 13.8040],
  "Cologna / Scorcola / Gretta": [45.6620, 13.7800], "Roiano": [45.6650, 13.7700],
  "Barcola / Grignano / Miramare": [45.6860, 13.7400], "Carso triestino (Opicina e frazioni)": [45.6870, 13.7900],
  "Servola / Valmaura / Borgo San Sergio": [45.6230, 13.7920], "Montebello / Rotonda del Boschetto": [45.6480, 13.8000],
};

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// punto delle migliaia anche sotto 10.000 ("€ 5.460"; toLocaleString in italiano scriverebbe "5460")
const num = v => String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
const eur = v => v ? "€ " + num(v) : "Prezzo su richiesta";
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage non disponibile */ } },
};

// ultima visita: una "visita" finisce dopo 30 minuti senza aprire la dashboard
const VISIT = (() => {
  const v = store.get("visit", {}), now = Date.now();
  if (!v.last || now - v.last > 30 * 60e3) v.prev = v.last || null;
  v.last = now;
  store.set("visit", v);
  return v.prev ? new Date(v.prev).toISOString() : null;
})();

const S = { listings: [], byId: new Map(), groups: new Map(), events: [], sources: {}, updated: null,
  favs: new Set(store.get("favs", [])), shown: 60, map: null, markers: null };

// ------------------------------------------------------------------ dati

async function load() {
  // "no-cache": il browser chiede sempre al server, ma riscarica il file solo se è cambiato
  // (i file piccoli impostati dall'utente, notify/favorites, saltano anche la cache di GitHub Pages)
  const get = async f => { const r = await fetch(`data/${f}`, { cache: "no-cache" }); if (!r.ok) throw new Error(f); return r.json(); };
  const [l, e, s, t] = await Promise.all([get("listings.json"), get("events.json").catch(() => ({ events: [] })),
    get("sources.json").catch(() => ({ sources: {} })), get("trend.json").catch(() => ({ days: {} }))]);
  S.trend = t.days || {};
  S.omi = await get("omi.json").catch(() => null);
  S.stima = await get("stima.json").catch(() => null);
  S.updated = l.updated;
  S.notify = await fetch(`notify.json?t=${Date.now()}`).then(r => r.ok ? r.json() : null).catch(() => null);
  S.sources = s.sources || {};
  S.events = (e.events || []).slice().reverse();
  S.listings = l.listings || [];
  for (const r of S.listings) {
    r.kind = r.private ? "privato" : (S.sources[r.source]?.kind || "agenzia");
    r.ppm = r.price && r.mq ? r.price / r.mq : null;
    r.srcName = r.agency || S.sources[r.source]?.name || r.source;
    S.byId.set(r.id, r);
    const g = r.group || r.id;
    if (!S.groups.has(g)) S.groups.set(g, []);
    S.groups.get(g).push(r);
  }
  computeZoneMedians();
  computeSince();
}

// case nuove, ribassate o tornate online dall'ultima visita: gid -> motivo
function computeSince() {
  S.since = new Map();
  if (!VISIT) return;
  for (const e of S.events) {
    if (e.ts <= VISIT || !["new", "price_down", "back"].includes(e.type)) continue;
    const r = S.byId.get(e.id);
    if (!r || r.status !== "active") continue;
    const g = r.group || r.id, cur = S.since.get(g);
    if (!cur || cur === "back" || (cur === "price_down" && e.type === "new")) S.since.set(g, e.type);
  }
}

// testi e foto (file separato, più pesante): caricati dopo che l'elenco è già visibile
function loadDetails() {
  S.details = fetch("data/details.json", { cache: "no-cache" }).then(r => r.ok ? r.json() : { details: [] }).then(d => {
    for (const x of d.details || []) {
      const r = S.byId.get(x.id);
      if (!r) continue;
      if (x.description) r.description = x.description;
      if (x.images?.length) r.images = x.images;
    }
  }).catch(() => {}).finally(() => { S.detailsReady = true; });
  return S.details;
}

// €/mq rispetto alla mediana della zona (solo appartamenti/attici; ogni casa conta una volta; nuove costruzioni escluse dal riferimento)
const DEAL_TYPES = ["appartamento", "attico"];
const ZONE_MIN_SAMPLE = 8;
const zoneKey = r => r.town === "Trieste" ? (r.zone ? "z:" + r.zone : null) : (r.town ? "t:" + r.town : null);
function computeZoneMedians() {
  const buckets = new Map();
  for (const members of S.groups.values()) {
    const r = members.find(m => m.status === "active" && m.ppm && DEAL_TYPES.includes(m.type) && m.condition !== "nuovo" && !m.features?._project);
    const k = r && zoneKey(r);
    if (!k) continue;
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push(r.ppm);
  }
  S.zoneMed = new Map();
  for (const [k, v] of buckets) {
    if (v.length < ZONE_MIN_SAMPLE) continue;
    v.sort((a, b) => a - b);
    const n = v.length;
    S.zoneMed.set(k, { med: n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2, n });
  }
  for (const r of S.listings) {
    const z = r.ppm && DEAL_TYPES.includes(r.type) && !r.features?._project ? S.zoneMed.get(zoneKey(r)) : null;
    const pct = z && (r.ppm / z.med - 1) * 100;
    // scarti enormi sono quasi sempre dati letti male (nuda proprietà, superficie del terreno...): non mostrarli
    r.zoneCmp = z && pct > -60 && pct < 150 ? { pct, med: z.med, n: z.n } : null;
  }
}
const pctTxt = p => (p > 0 ? "+" : "−") + Math.abs(Math.round(p)) + "%";

// ---- stima del prezzo giusto (docs/data/stima.json, calcolata da scraper/stima.py)
// est[gid] = [valore stimato, annunci della zona nel campione, può essere un affare 0/1, prezzo "speciale" 0/1]
function stimaOf(h) {
  const e = S.stima?.est?.[h.gid];
  if (!e) return null;
  const [v, n, dealable, special] = e;
  let pct = h.price ? (h.price / v - 1) * 100 : null;
  // scarti enormi sono quasi sempre dati letti male (superficie del terreno, nuda proprietà...): non mostrarli
  if (pct != null && (pct <= -50 || pct > 100)) pct = null;
  const deal = pct != null && dealable && !special && pct <= -S.stima.deal * 100;
  const band = S.stima.band || [1 - S.stima.mdape, 1 + S.stima.mdape];
  return { v, n, pct, deal, special: !!special, lo: v * band[0], hi: v * band[1] };
}

// Una "casa" = gruppo di annunci (stesso immobile su più agenzie). Rappresentante: attivo, con più dati, prezzo minore.
function houses(includeRemoved) {
  const out = [];
  for (const [gid, members] of S.groups) {
    const act = members.filter(m => m.status === "active");
    const pool = act.length ? act : (includeRemoved ? members : []);
    if (!pool.length) continue;
    const rep = pool.slice().sort((a, b) => score(b) - score(a))[0];
    const firstSeen = members.reduce((m, x) => x.first_seen < m ? x.first_seen : m, rep.first_seen);
    const h = { gid, rep, members: pool, all: members, active: act.length > 0, firstSeen,
      price: Math.min(...pool.map(m => m.price || Infinity)) === Infinity ? null : Math.min(...pool.map(m => m.price || Infinity)) };
    h.stima = h.active ? stimaOf(h) : null;
    out.push(h);
  }
  return out;
}
const score = r => (r.images?.length ? 4 : 0) + (r.mq ? 2 : 0) + (r.price ? 2 : 0) + (r.rooms ? 1 : 0) + (r.description ? 1 : 0) - (r.price || 0) / 1e9;

function priceChange(r) {
  const h = r.price_history || [];
  if (h.length < 2) return null;
  const first = h[0][1], last = h[h.length - 1][1];
  return first && last && first !== last ? { first, last, pct: (last - first) / first * 100 } : null;
}

// ------------------------------------------------------------------ filtri

const DEFAULT_F = { q: "", pmin: "", pmax: "", mqmin: "", mqmax: "", rooms: "", sort: "recent", types: DEFAULT_TYPES, conditions: [], towns: [], zones: [], features: [], kinds: [], noprice: true, removed: false };
let F = Object.assign({}, DEFAULT_F, store.get("filters", {}));
// filtri salvati con la vecchia tipologia predefinita (senza "altro"): aggiornali una volta
if (!F.v && String(F.types) === "appartamento,attico,villa,casa") F.types = DEFAULT_TYPES.slice();
F.v = 2;

function chipGroup(el, name, entries, counts) {
  el.innerHTML = entries.map(([v, label]) => `<label class="chip"><input type="checkbox" name="${name}" value="${esc(v)}" ${F[name].includes(v) ? "checked" : ""}><span>${esc(label)}${counts && counts[v] ? `<small>${counts[v]}</small>` : ""}</span></label>`).join("");
}

function buildFilters() {
  const act = S.listings.filter(r => r.status === "active");
  const cnt = key => act.reduce((m, r) => (m[r[key] || "nd"] = (m[r[key] || "nd"] || 0) + 1, m), {});
  const tc = cnt("type"), cc = cnt("condition"), town = cnt("town"), zc = cnt("zone"), kc = cnt("kind");
  chipGroup($("#f-types"), "types", Object.entries(TYPE_LABELS).filter(([k]) => tc[k]), tc);
  chipGroup($("#f-conditions"), "conditions", Object.entries(COND_LABELS).filter(([k]) => cc[k]), cc);
  chipGroup($("#f-towns"), "towns", Object.keys(TOWN_CENTER).filter(t => town[t]).map(t => [t, t]), town);
  chipGroup($("#f-zones"), "zones", Object.keys(ZONE_CENTER).filter(z => zc[z]).map(z => [z, z]).concat(zc.nd ? [["nd", "Zona non indicata"]] : []), zc);
  chipGroup($("#f-features"), "features", Object.entries(FEATURES));
  chipGroup($("#f-kinds"), "kinds", Object.entries(KINDS).filter(([k]) => kc[k]), kc);
  const form = $("#filter-form");
  for (const k of ["q", "pmin", "pmax", "mqmin", "mqmax", "rooms", "sort"]) form.elements[k].value = F[k];
  form.elements.noprice.checked = F.noprice;
  form.elements.removed.checked = F.removed;
  syncZonesBox();
}

function readForm() {
  const form = $("#filter-form"), fd = new FormData(form);
  F = { q: fd.get("q").trim(), pmin: fd.get("pmin"), pmax: fd.get("pmax"), mqmin: fd.get("mqmin"), mqmax: fd.get("mqmax"),
    rooms: fd.get("rooms"), sort: fd.get("sort"), types: fd.getAll("types"), conditions: fd.getAll("conditions"),
    towns: fd.getAll("towns"), zones: fd.getAll("zones"), features: fd.getAll("features"), kinds: fd.getAll("kinds"),
    noprice: form.elements.noprice.checked, removed: form.elements.removed.checked, v: 2 };
  store.set("filters", F);
  syncZonesBox();
}

function syncZonesBox() {
  $("#zones-box").hidden = F.towns.length > 0 && !F.towns.includes("Trieste");
  const n = ["q", "pmin", "pmax", "mqmin", "mqmax", "rooms"].filter(k => F[k]).length
    + ["conditions", "towns", "zones", "features", "kinds"].filter(k => F[k].length).length
    + (String(F.types) !== String(DEFAULT_TYPES) ? 1 : 0);
  $("#filters-active").textContent = n || "";
}

function matchListing(r) {
  const n = v => v === "" || v == null ? null : Number(v);
  const pmin = n(F.pmin), pmax = n(F.pmax), mqmin = n(F.mqmin), mqmax = n(F.mqmax), rooms = n(F.rooms);
  if (!r.price && !F.noprice) return false;
  if (pmin && r.price && r.price < pmin) return false;
  if (pmax && r.price && r.price > pmax) return false;
  if ((pmin || pmax) && !r.price && !F.noprice) return false;
  if (mqmin && (!r.mq || r.mq < mqmin)) return false;
  if (mqmax && r.mq && r.mq > mqmax) return false;
  if (rooms && (!roomsOf(r) || roomsOf(r) < rooms)) return false;
  if (F.types.length && !F.types.includes(r.type || "altro")) return false;
  if (F.conditions.length && !F.conditions.includes(r.condition || "nd")) return false;
  if (F.towns.length && !F.towns.includes(r.town)) return false;
  if (F.zones.length && r.town === "Trieste" && !F.zones.includes(r.zone || "nd")) return false;
  if (F.zones.length && r.town !== "Trieste" && !F.towns.length) return false;
  if (F.features.some(f => r.features?.[f] !== true)) return false;
  if (F.kinds.length && !F.kinds.includes(r.kind)) return false;
  if (F.q) {
    const blob = [r.title, r.description, r.address, r.zone, r.town, r.ref, r.srcName].join(" ").toLowerCase();
    if (!F.q.toLowerCase().split(/\s+/).every(w => blob.includes(w))) return false;
  }
  return true;
}
const matchHouse = h => h.members.some(matchListing);
// locali; se mancano ma si sanno le camere, si contano camere + soggiorno (un "trilocale" ha di solito due camere)
const roomsOf = r => r.rooms || (r.bedrooms ? r.bedrooms + 1 : null);

function sortHouses(list) {
  const by = {
    recent: (a, b) => b.firstSeen.localeCompare(a.firstSeen),
    price_asc: (a, b) => (a.price || 9e12) - (b.price || 9e12),
    price_desc: (a, b) => (b.price || 0) - (a.price || 0),
    ppm_asc: (a, b) => (a.rep.ppm || 9e12) - (b.rep.ppm || 9e12),
    zone_deal: (a, b) => (a.rep.zoneCmp?.pct ?? 9e9) - (b.rep.zoneCmp?.pct ?? 9e9),
    stima: (a, b) => (a.stima?.pct ?? 9e9) - (b.stima?.pct ?? 9e9),
    mq_desc: (a, b) => (b.rep.mq || 0) - (a.rep.mq || 0),
    drop: (a, b) => (priceChange(a.rep)?.pct ?? 0) - (priceChange(b.rep)?.pct ?? 0),
  }[F.sort] || (() => 0);
  return list.sort(by);
}

// ------------------------------------------------------------------ rendering

const ago = iso => {
  const d = (Date.now() - new Date(iso)) / 864e5;
  if (d < 1 / 24) return "da meno di un'ora";
  if (d < 1) return `da ${Math.round(d * 24)} ore`;
  if (d < 2) return "da ieri";
  return `da ${Math.round(d)} giorni`;
};
const fmtDate = iso => new Date(iso).toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" });
const where = r => r.town === "Trieste" ? (r.zone || "Trieste") : (r.town || "");
const specs = r => [r.mq && `${r.mq} mq`, r.rooms && `${r.rooms} locali`, r.bathrooms && `${r.bathrooms} bagni`, r.floor && `piano ${r.floor}`].filter(Boolean).join(" · ");
// "Nuovo" = comparso negli ultimi 3 giorni, ma non al primo giro di lettura della fonte (lì era già online)
const isNew = h => (Date.now() - new Date(h.firstSeen)) / 864e5 < 3 && h.members.some(m => {
  const f = S.sources[m.source]?.first_ok; return f && m.first_seen > f; });

function card(h) {
  const r = h.rep, pc = priceChange(r), img = r.images?.[0];
  const badges = [
    !h.active && `<span class="badge gone">Non più online</span>`,
    h.active && S.since?.get(h.gid) === "new" ? `<span class="badge fresh">Nuovo per te</span>` : h.active && isNew(h) && `<span class="badge new">Nuovo</span>`,
    pc && pc.pct < 0 && `<span class="badge down">${pc.pct.toFixed(0)}%</span>`,
    pc && pc.pct > 0 && `<span class="badge up">+${pc.pct.toFixed(0)}%</span>`,
    r.sold && `<span class="badge gone">Venduto / trattativa</span>`,
    r.private && `<span class="badge priv">Privato</span>`,
    // stima del prezzo giusto quando c'è; altrimenti il confronto con la media della zona (mai entrambi: possono discordare)
    h.stima?.deal && `<span class="badge deal" title="Prezzo molto sotto la stima: possibile affare, da verificare">${pctTxt(h.stima.pct)} stima</span>`,
    h.active && !h.stima && r.zoneCmp && r.zoneCmp.pct <= -15 && `<span class="badge deal" title="Prezzo al mq rispetto alla media della zona">${pctTxt(r.zoneCmp.pct)} zona</span>`,
    notes.get(h.gid).status && `<span class="badge mine">${esc(STATUS[notes.get(h.gid).status])}</span>`,
  ].filter(Boolean).join("");
  const zc = h.stima ? (h.stima.pct != null && Math.abs(h.stima.pct) >= 5
      ? ` <span class="${h.stima.pct < 0 ? "deal" : "dear"}" title="Rispetto al prezzo stimato (${eur(h.stima.v)})">(${pctTxt(h.stima.pct)} stima)</span>` : "")
    : r.zoneCmp && Math.abs(r.zoneCmp.pct) >= 5
      ? ` <span class="${r.zoneCmp.pct < 0 ? "deal" : "dear"}" title="Rispetto alla media della zona (${num(r.zoneCmp.med)} €/mq)">(${pctTxt(r.zoneCmp.pct)} zona)</span>` : "";
  const others = h.members.length > 1 ? ` · anche su altre ${h.members.length - 1}` : "";
  return `<article class="card ${h.active ? "" : "gone"}" data-gid="${esc(h.gid)}">
    <div class="thumb">${img ? `<img loading="lazy" src="${esc(img)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}<div class="noimg">${img ? "" : "Nessuna foto"}</div>
      <div class="badges">${badges}</div>
      <button class="star ${S.favs.has(h.gid) ? "on" : ""}" data-fav="${esc(h.gid)}" aria-label="Preferito" title="Aggiungi ai preferiti">★</button></div>
    <div class="body">
      <div class="price">${r.features?._project && h.price ? "da " : ""}${eur(h.price)}${pc ? `<span class="old">${eur(pc.first)}</span>` : ""}${!h.price && h.stima ? `<small class="est">valore stimato ~${eur(h.stima.v)}</small>` : ""}</div>
      <div class="specs">${esc(TYPE_LABELS[r.type] || "Immobile")}${specs(r) ? " · " + esc(specs(r)) : ""}</div>
      <div class="where">${esc(where(r))}${r.ppm ? ` · ${num(r.ppm)} €/mq` : ""}${zc}</div>
      <div class="title">${esc(r.title || "")}</div>
      <div class="src">${esc(r.srcName)}${esc(others)} · online ${ago(h.firstSeen)}</div>
    </div></article>`;
}

function syncSort() {
  document.querySelectorAll(".sort-top").forEach(el => { el.value = F.sort; });
  const f = $("#filter-form");
  if (f) f.elements.sort.value = F.sort;
}

let ONLY_SINCE = false;
function renderSince(all) {
  const box = $("#since");
  const fresh = all.filter(h => S.since?.has(h.gid));
  if (!VISIT || (!fresh.length && !ONLY_SINCE)) { box.hidden = true; ONLY_SINCE = false; return; }
  const n = t => fresh.filter(h => S.since.get(h.gid) === t).length;
  const parts = [[n("new"), "nuove", "nuova"], [n("price_down"), "ribassate", "ribassata"], [n("back"), "tornate online", "tornata online"]]
    .filter(([c]) => c).map(([c, pl, sg]) => `<b>${c}</b> ${c === 1 ? sg : pl}`);
  const when = new Date(VISIT).toLocaleString("it-IT", { weekday: "long", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  box.hidden = false;
  box.innerHTML = `<span>Dalla tua ultima visita (${esc(when)}), con questi filtri: ${parts.length ? parts.join(", ") : "nessuna novità"}.</span>
    ${fresh.length || ONLY_SINCE ? `<button type="button" class="btn ${ONLY_SINCE ? "" : "ghost"}" data-since>${ONLY_SINCE ? "Mostra tutte" : "Mostra solo queste"}</button>` : ""}`;
}

function renderGrid() {
  syncSort();
  const all = sortHouses(houses(F.removed).filter(matchHouse));
  renderSince(all);
  const list = ONLY_SINCE ? all.filter(h => S.since.has(h.gid)) : all;
  $("#count").textContent = `${list.length.toLocaleString("it-IT")} immobili corrispondono ai filtri`;
  $("#grid").innerHTML = list.length ? list.slice(0, S.shown).map(card).join("") : `<div class="empty">Nessun immobile con questi filtri.</div>`;
  $("#more").hidden = list.length <= S.shown;
}

function renderEvents() {
  const days = Number($("#ev-days").value);
  const types = store.get("evtypes", ["new", "price_down", "price_up", "removed", "sold"]);
  $("#ev-types").innerHTML = Object.entries(EV).map(([k, l]) => `<label class="chip"><input type="checkbox" value="${k}" ${types.includes(k) ? "checked" : ""}><span>${l}</span></label>`).join("");
  const cut = Date.now() - days * 864e5, seen = new Set(), byDay = new Map();
  for (const e of S.events) {
    if (new Date(e.ts) < cut || !types.includes(e.type)) continue;
    const r = S.byId.get(e.id);
    if (!r || !matchListing(r)) continue;
    const key = e.type + "|" + (r.group || r.id) + "|" + e.ts.slice(0, 10);
    if (seen.has(key)) continue;
    seen.add(key);
    const day = new Date(e.ts).toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" });
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push([e, r]);
  }
  if (!byDay.size) {
    const first = !S.events.length;
    $("#events").innerHTML = `<div class="empty">${first ? "Le novità compariranno qui dal prossimo controllo: il primo giro serve a registrare tutti gli annunci già online." : "Nessuna novità nel periodo scelto con questi filtri."}</div>`;
    return;
  }
  $("#events").innerHTML = [...byDay].map(([day, items]) => `<div class="day"><h3>${esc(day)} · ${items.length}</h3>${items.map(([e, r]) => {
    const img = r.images?.[0];
    let p = eur(e.price ?? r.price);
    if ((e.type === "price_down" || e.type === "price_up") && e.old_price) {
      const pct = (e.price - e.old_price) / e.old_price * 100;
      p = `${eur(e.price)}<small style="color:var(--${pct < 0 ? "down" : "up"})">${pct > 0 ? "+" : ""}${pct.toFixed(1)}% (era ${eur(e.old_price)})</small>`;
    }
    return `<div class="ev" data-gid="${esc(r.group || r.id)}">${img ? `<img loading="lazy" src="${esc(img)}" alt="" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'ph'}))">` : `<div class="ph"></div>`}
      <div><div class="t"><span class="tag ${e.type}">${EV[e.type]}</span>${esc(TYPE_LABELS[r.type] || "Immobile")}${specs(r) ? " · " + esc(specs(r)) : ""}</div>
      <div class="s">${esc(where(r))} · ${esc(r.srcName)} · ore ${new Date(e.ts).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}</div></div>
      <div class="p">${p}</div></div>`;
  }).join("")}</div>`).join("");
}

function renderFavs() {
  syncSort();
  const mode = store.get("favview", "cards"), hide = store.get("favhide", false);
  let list = sortHouses(houses(true).filter(h => S.favs.has(h.gid)));
  if (hide) list = list.filter(h => notes.get(h.gid).status !== "scartata");
  document.querySelectorAll("[data-favview]").forEach(b => b.classList.toggle("on", b.dataset.favview === mode));
  $("#fav-hide").checked = hide;
  $("#favs").className = mode === "cmp" ? "" : "grid";
  $("#favs").innerHTML = !S.favs.size ? `<div class="empty">Tocca ★ su un annuncio per salvarlo qui. I preferiti restano su questo dispositivo.</div>`
    : mode === "cmp" ? compareHtml(list) : list.length ? list.map(card).join("") : `<div class="empty">Tutti i preferiti sono scartati.</div>`;
  updateFavCount();
  renderFavSync();
}

function renderSources() {
  const rows = Object.entries(S.sources).sort((a, b) => (b[1].last_count || 0) - (a[1].last_count || 0));
  const active = S.listings.filter(r => r.status === "active");
  const cnt = active.reduce((m, r) => (m[r.source] = (m[r.source] || 0) + 1, m), {});
  const ok = rows.filter(([, s]) => s.last_ok && !s.fail_streak).length;
  $("#sources").innerHTML = `<p class="small">${ok} fonti su ${rows.length} lette correttamente all'ultimo controllo · ${active.length.toLocaleString("it-IT")} annunci attivi.</p>
  <table class="src-table"><thead><tr><th>Fonte</th><th class="n">Annunci</th><th class="hide-m">Ultima lettura riuscita</th><th class="hide-m">Note</th></tr></thead><tbody>${rows.map(([id, s]) => {
    const cls = !s.last_ok ? "err" : s.fail_streak || s.quality_issue ? "warn" : "";
    return `<tr><td><span class="dot ${cls}"></span>${s.website ? `<a href="${esc(s.website)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)}<br><span class="muted small">${esc(KINDS[s.kind] || s.kind || "")}</span></td>
      <td class="n">${cnt[id] || 0}</td><td class="hide-m">${s.last_ok ? fmtDate(s.last_ok) + " " + new Date(s.last_ok).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "mai"}</td>
      <td class="hide-m small muted">${esc(s.fail_streak ? s.last_error || "" : s.quality_issue ? "Dati incompleti: " + s.quality_issue : "")}</td></tr>`;
  }).join("")}</tbody></table>`;
}

function renderMap() {
  if (!window.L) { $("#map-note").textContent = "Mappa non disponibile (libreria non caricata)."; return; }
  if (!S.map) {
    S.map = L.map("map").setView([45.66, 13.78], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(S.map);
    S.markers = L.layerGroup().addTo(S.map);
  }
  S.markers.clearLayers();
  const list = houses(F.removed).filter(matchHouse);
  let exact = 0, street = 0, approx = 0;
  for (const h of list) {
    const r = h.members.find(m => m.lat && m.geo !== "via") || h.members.find(m => m.lat) || h.rep;
    let ll = r.lat && r.lon ? [r.lat, r.lon] : null;
    if (ll && r.geo === "via") street++;
    else if (ll) exact++;
    else {
      const c = (r.town === "Trieste" && ZONE_CENTER[r.zone]) || TOWN_CENTER[r.town];
      if (!c) continue;
      const seed = [...h.gid].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
      ll = [c[0] + ((seed % 1000) / 1000 - .5) * .012, c[1] + ((Math.floor(seed / 1000) % 1000) / 1000 - .5) * .016];
      approx++;
    }
    const m = L.circleMarker(ll, { radius: 7, weight: 2, color: !r.lat ? "#8a8277" : r.geo === "via" ? "#6aa9c4" : "#0f6e7c", fillOpacity: .75 });
    const img = h.rep.images?.[0];
    m.bindPopup(`${img ? `<img src="${esc(img)}" referrerpolicy="no-referrer" alt="">` : ""}<b>${eur(h.price)}</b><br>${esc(TYPE_LABELS[h.rep.type] || "")} ${esc(specs(h.rep))}<br>${esc(where(h.rep))}<br><a href="#" data-open="${esc(h.gid)}">Apri scheda</a>`);
    S.markers.addLayer(m);
  }
  $("#map-note").textContent = `${exact} immobili con posizione esatta (blu scuro), ${street} al centro della via indicata nell'annuncio (azzurro), ${approx} al centro della zona (grigio).`;
  setTimeout(() => S.map.invalidateSize(), 50);
}

function openDetail(gid, refresh) {
  const members = S.groups.get(gid);
  if (!members) return;
  const act = members.filter(m => m.status === "active");
  const list = (act.length ? act : members).slice().sort((a, b) => score(b) - score(a));
  const r = list[0];
  S.openRep = r;
  const imgs = [...new Set(list.flatMap(m => m.images || []))].slice(0, 16);
  const facts = [
    [(r.features?._project ? "da " : "") + eur(Math.min(...list.map(m => m.price || Infinity)) === Infinity ? null : Math.min(...list.map(m => m.price || Infinity))), r.features?._project ? "Prezzo minimo del cantiere" : "Prezzo"],
    [r.mq && `${r.mq} mq`, "Superficie"], [r.ppm && `${num(r.ppm)} €/mq`, "Prezzo al mq"],
    [r.zoneCmp && `${pctTxt(r.zoneCmp.pct)} rispetto alla zona`, r.zoneCmp && `Media zona ${num(r.zoneCmp.med)} €/mq (${r.zoneCmp.n} annunci)`],
    [r.rooms, "Locali"], [r.bedrooms, "Camere"], [r.bathrooms, "Bagni"], [r.floor, "Piano"], [r.energy, "Classe energetica"],
    [COND_LABELS[r.condition], "Stato"], [TYPE_LABELS[r.type], "Tipologia"], [where(r) + (r.town !== "Trieste" ? "" : ""), "Zona"],
    ...Object.entries(FEATURES).map(([k, l]) => [r.features?.[k] === true ? "Sì" : r.features?.[k] === false ? "No" : null, l]),
  ].filter(([v]) => v);
  const firstSeen = members.reduce((m, x) => x.first_seen < m ? x.first_seen : m, r.first_seen);
  const hist = members.flatMap(m => (m.price_history || []).map(([ts, p]) => [ts, p, m.srcName])).sort((a, b) => a[0].localeCompare(b[0]));
  $("#detail-body").innerHTML = `<div class="d-head"><div><h2>${esc(TYPE_LABELS[r.type] || "Immobile")}${specs(r) ? " · " + esc(specs(r)) : ""}</h2>
      <p class="muted" style="margin:4px 0 0">${esc([r.address, where(r)].filter(Boolean).join(" · "))}</p></div>
      <div style="display:flex;gap:6px"><button class="star ${S.favs.has(gid) ? "on" : ""}" data-fav="${esc(gid)}" style="position:static;background:var(--surface-2)" aria-label="Preferito">★</button><button class="close" aria-label="Chiudi">×</button></div></div>
    ${imgs.length ? `<div class="gallery">${imgs.map(u => `<img loading="lazy" src="${esc(u)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">`).join("")}</div>` : ""}
    <div class="d-body">
      <div class="facts">${facts.map(([v, l]) => `<div class="fact"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("")}</div>
      ${stimaHtml(gid)}
      ${zoneTrendHtml(r)}
      ${omiHtml(r, members)}
      ${similarHtml(gid, r)}
      <div class="story"><h3>Storia della casa</h3><ul>${houseStory(members).map(x => `<li>${x}</li>`).join("")}</ul></div>
      ${mineHtml(gid)}
      ${list.some(m => m.price) && act.length ? calcHtml(Math.min(...list.map(m => m.price || Infinity)), r) : ""}
      <div><h3 style="font-size:15px;margin-bottom:8px">Dove è in vendita</h3><div class="offers">${members.map(m => `<div class="offer"><div><b>${esc(m.srcName)}</b>${m.private ? " (privato)" : ""}<br><span class="muted small">${eur(m.price)}${m.ref ? " · rif. " + esc(m.ref) : ""}${m.status !== "active" ? " · non più online dal " + fmtDate(m.removed_at) : ""}</span></div><a class="btn" href="${esc(m.url)}" target="_blank" rel="noopener">Vedi annuncio</a></div>`).join("")}</div></div>
      ${hist.length > 1 ? `<div><h3 style="font-size:15px;margin-bottom:6px">Storico prezzi</h3><table class="hist">${hist.map(([ts, p, s]) => `<tr><td>${fmtDate(ts)}</td><td><b>${eur(p)}</b></td><td class="muted">${esc(s)}</td></tr>`).join("")}</table></div>` : ""}
      ${r.title ? `<div><b>${esc(r.title)}</b></div>` : ""}
      ${r.description ? `<div class="desc">${esc(r.description)}</div>` : ""}
      <p class="muted small">Primo avvistamento: ${fmtDate(firstSeen)}. I dati sono letti in automatico dal sito dell'agenzia: verifica sempre sull'annuncio originale.</p>
    </div>`;
  const cf = $("#detail-body .calc-form");
  if (cf) fillCalc(cf, r);
  const dlg = $("#detail");
  S.openGid = gid;
  if (!dlg.open) dlg.showModal();
  if (!refresh) dlg.scrollTop = 0;
  // scheda aperta prima che arrivassero testi e foto: completala appena ci sono
  if (!S.detailsReady && S.details) S.details.then(() => { if (dlg.open && S.openGid === gid) openDetail(gid, true); });
}

// ------------------------------------------------------------------ strumenti per decidere

// Stato e appunti personali: restano SOLO su questo dispositivo (il progetto è pubblico, le note non escono da qui).
const STATUS = { vedere: "Da vedere", visitata: "Visitata", piace: "Mi piace", scartata: "Scartata" };
const notes = {
  all: () => store.get("notes", {}),
  get: gid => notes.all()[gid] || {},
  set(gid, v) {
    const a = notes.all();
    if (!v.status && !v.note) delete a[gid]; else a[gid] = v;
    store.set("notes", a);
  },
};

// Storia della casa, sommando tutte le agenzie che la propongono
function houseStory(members) {
  const out = [];
  const first = members.reduce((m, x) => x.first_seen < m ? x.first_seen : m, members[0].first_seen);
  const days = Math.max(0, Math.round((Date.now() - new Date(first)) / 864e5));
  const srcStart = members.map(m => S.sources[m.source]?.first_ok).filter(Boolean).sort()[0];
  const atStart = srcStart && new Date(first) - new Date(srcStart) < 864e5;
  out.push(atStart
    ? `Era già in vendita il ${fmtDate(first)}, quando il monitoraggio ha iniziato a leggere ${members.length > 1 ? "queste agenzie" : "questa agenzia"}: online da <b>almeno ${days} giorni</b>.`
    : `In vendita da <b>${days} giorni</b> (comparsa il ${fmtDate(first)}).`);

  let downs = 0, ups = 0;
  for (const m of members) {
    const h = m.price_history || [];
    for (let i = 1; i < h.length; i++) { if (h[i][1] < h[i - 1][1]) downs++; else if (h[i][1] > h[i - 1][1]) ups++; }
  }
  const startPrices = members.map(m => m.price_history?.[0]?.[1]).filter(Boolean);
  const nowPrices = members.filter(m => m.status === "active").map(m => m.price).filter(Boolean);
  if (downs && startPrices.length && nowPrices.length) {
    const a = Math.max(...startPrices), b = Math.min(...nowPrices);
    out.push(`Prezzo <b>sceso ${downs === 1 ? "una volta" : downs + " volte"}</b>: da ${eur(a)} a ${eur(b)}` + (a > b ? ` (${pctTxt((b - a) / a * 100)}).` : "."));
  } else if (ups) out.push(`Prezzo <b>aumentato ${ups === 1 ? "una volta" : ups + " volte"}</b>.`);
  else if (startPrices.length) out.push("Prezzo mai cambiato da quando è online.");

  const ids = new Set(members.map(m => m.id));
  const backs = S.events.filter(e => e.type === "back" && ids.has(e.id)).length;
  if (backs) out.push(`Tolta e rimessa online ${backs === 1 ? "una volta" : backs + " volte"}.`);

  const act = members.filter(m => m.status === "active" && m.price);
  if (act.length > 1) {
    const lo = Math.min(...act.map(m => m.price)), hi = Math.max(...act.map(m => m.price));
    const cheapest = act.find(m => m.price === lo);
    out.push(`Proposta da <b>${act.length} agenzie</b>` + (hi > lo
      ? `: ${eur(hi - lo)} di differenza, il prezzo più basso è di ${esc(cheapest.srcName)}.` : " allo stesso prezzo."));
  }
  if (days >= 60 && downs) out.push("In vendita da tempo e già ribassata: di solito c'è più margine per trattare.");
  return out;
}

// ---- andamento del prezzo al mq per zona (docs/data/trend.json, una fotografia al giorno)
const ALL_AREA = "Tutta la provincia";
const trendKey = r => r.town === "Trieste" ? r.zone : r.town;
// i primi giorni del monitoraggio avevano poche agenzie collegate: si usano solo i giorni con copertura piena
function trendDays() {
  if (S.trendDays) return S.trendDays;
  const ds = Object.keys(S.trend || {}).sort();
  const last = ds.length ? S.trend[ds[ds.length - 1]][ALL_AREA]?.[1] || 0 : 0;
  return S.trendDays = ds.filter(d => (S.trend[d][ALL_AREA]?.[1] || 0) >= 0.8 * last);
}
const trendSeries = key => trendDays().map(d => [d, S.trend[d][key]?.[0], S.trend[d][key]?.[1]]).filter(x => x[1]);
const shortDate = d => new Date(d).toLocaleDateString("it-IT", { day: "numeric", month: "short" });

function trendChange(series) {
  if (series.length < 2) return null;
  const a = series[0][1], b = series[series.length - 1][1];
  return { pct: (b - a) / a * 100, from: series[0][0], days: Math.round((new Date(series[series.length - 1][0]) - new Date(series[0][0])) / 864e5) };
}

// grafico a linea con mirino al passaggio del mouse/dito (una sola serie: il titolo la nomina, niente legenda)
function lineChart(series, { w = 640, h = 220, mini = false } = {}) {
  if (series.length < 2) return "";
  const pad = mini ? { l: 2, r: 4, t: 4, b: 4 } : { l: 52, r: 14, t: 12, b: 26 };
  const vals = series.map(x => x[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const span = Math.max(hi - lo, hi * 0.04);
  lo -= span * 0.15; hi += span * 0.15;
  const X = i => pad.l + i * (w - pad.l - pad.r) / (series.length - 1);
  const Y = v => pad.t + (hi - v) * (h - pad.t - pad.b) / (hi - lo);
  const pts = series.map((x, i) => `${X(i).toFixed(1)},${Y(x[1]).toFixed(1)}`).join(" ");
  const last = series.length - 1;
  if (mini) {
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${X(last)}" cy="${Y(series[last][1])}" r="3" fill="var(--accent)"/></svg>`;
  }
  const ticks = [lo + (hi - lo) * 0.2, (lo + hi) / 2, lo + (hi - lo) * 0.8];
  const data = esc(JSON.stringify(series.map(x => [shortDate(x[0]), x[1], x[2]])));
  return `<div class="lc" data-series="${data}" data-w="${w}" data-padl="${pad.l}" data-padr="${pad.r}">
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Andamento del prezzo al metro quadro">
      ${ticks.map(t => `<line x1="${pad.l}" x2="${w - pad.r}" y1="${Y(t)}" y2="${Y(t)}" class="grid"/>
        <text x="${pad.l - 8}" y="${Y(t) + 4}" text-anchor="end" class="ax">${num(t)}</text>`).join("")}
      <text x="${pad.l}" y="${h - 6}" class="ax">${shortDate(series[0][0])}</text>
      <text x="${w - pad.r}" y="${h - 6}" text-anchor="end" class="ax">${shortDate(series[last][0])}</text>
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${X(last)}" cy="${Y(series[last][1])}" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>
      <line class="cross" y1="${pad.t}" y2="${h - pad.b}" x1="0" x2="0" style="display:none"/>
      <circle class="cross-dot" r="5" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" cx="0" cy="0" style="display:none"/>
      ${series.map((x, i) => `<circle class="pt" data-i="${i}" cx="${X(i)}" cy="${Y(x[1])}" r="0"/>`).join("")}
    </svg><div class="tip" hidden></div></div>`;
}

function chartHover(e) {
  const box = e.target.closest?.(".lc");
  if (!box) return;
  const svg = box.querySelector("svg"), rect = svg.getBoundingClientRect();
  const series = JSON.parse(box.dataset.series), w = +box.dataset.w, pl = +box.dataset.padl, pr = +box.dataset.padr;
  const x = (e.clientX - rect.left) * w / rect.width;
  const i = Math.max(0, Math.min(series.length - 1, Math.round((x - pl) / ((w - pl - pr) / (series.length - 1)))));
  const pt = box.querySelector(`.pt[data-i="${i}"]`), cross = box.querySelector(".cross"), dot = box.querySelector(".cross-dot"), tip = box.querySelector(".tip");
  const cx = +pt.getAttribute("cx"), cy = +pt.getAttribute("cy");
  cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.style.display = "";
  dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.style.display = "";
  tip.hidden = false;
  tip.innerHTML = `<b>${num(series[i][1])} €/mq</b><span>${esc(series[i][0])} · ${series[i][2]} case</span>`;
  const px = cx * rect.width / w;
  tip.style.left = Math.max(0, Math.min(rect.width - tip.offsetWidth, px - tip.offsetWidth / 2)) + "px";
  tip.style.top = Math.max(0, cy * rect.height / (+svg.viewBox.baseVal.height) - tip.offsetHeight - 12) + "px";
}
function chartLeave(e) {
  const box = e.target.closest?.(".lc");
  if (!box || box.contains(e.relatedTarget)) return;
  box.querySelectorAll(".cross, .cross-dot").forEach(el => el.style.display = "none");
  box.querySelector(".tip").hidden = true;
}

function zoneTrendHtml(r) {
  const key = trendKey(r);
  const s = key && trendSeries(key);
  if (!s || s.length < 2) return "";
  const ch = trendChange(s);
  const how = Math.abs(ch.pct) < 0.5 ? "<b>stabili</b>" : `<b>${ch.pct > 0 ? "in salita" : "in discesa"}</b> (${ch.pct > 0 ? "+" : "−"}${Math.abs(ch.pct).toFixed(1).replace(".", ",")}%)`;
  return `<div class="zt"><div>Prezzi al mq in ${esc(key)}: ${how} negli ultimi ${ch.days} giorni.
      <a href="#prezzi/${encodeURIComponent(key)}">Vedi andamento</a></div>${lineChart(s, { w: 120, h: 32, mini: true })}</div>`;
}

let PRICE_SEL = ALL_AREA;
function renderPrices() {
  const days = trendDays();
  const want = decodeURIComponent((location.hash.split("/")[1] || ""));
  if (want && S.trend?.[days[days.length - 1]]?.[want]) PRICE_SEL = want;
  if (!days.length) { $("#prices").innerHTML = `<div class="empty">L'andamento dei prezzi comparirà dopo il prossimo controllo.</div>`; return; }
  const today = S.trend[days[days.length - 1]];
  const rows = Object.keys(today).map(k => ({ k, s: trendSeries(k) })).filter(x => x.s.length)
    .sort((a, b) => (a.k === ALL_AREA) - (b.k === ALL_AREA) || b.s[b.s.length - 1][1] - a.s[a.s.length - 1][1]);
  const sel = trendSeries(PRICE_SEL), ch = trendChange(sel);
  $("#prices").innerHTML = `<div class="panel wide">
      <h2>${esc(PRICE_SEL)}: ${num(sel[sel.length - 1][1])} €/mq</h2>
      <p class="muted small">Prezzo al mq medio (mediana) chiesto per appartamenti e attici usati, una volta per casa anche se è su più agenzie.
        ${ch ? `Dal ${fmtDate(ch.from)}: <b>${ch.pct >= 0 ? "+" : "−"}${Math.abs(ch.pct).toFixed(1).replace(".", ",")}%</b>.` : ""}
        Il grafico si allunga di giorno in giorno.</p>
      ${lineChart(sel)}
    </div>
    <table class="src-table prices"><thead><tr><th>Zona</th><th class="n">€/mq</th><th class="n hide-m">Case</th><th class="n">Variazione</th><th class="hide-m">Andamento</th></tr></thead><tbody>
    ${rows.map(({ k, s }) => {
      const c = trendChange(s), last = s[s.length - 1];
      return `<tr class="${k === PRICE_SEL ? "sel" : ""} ${k === ALL_AREA ? "all" : ""}" data-zone="${esc(k)}" tabindex="0">
        <td>${esc(k)}</td><td class="n"><b>${num(last[1])}</b></td><td class="n hide-m">${last[2]}</td>
        <td class="n">${c ? `<span class="${c.pct <= -0.5 ? "dn" : c.pct >= 0.5 ? "upc" : "muted"}">${c.pct >= 0 ? "+" : "−"}${Math.abs(c.pct).toFixed(1).replace(".", ",")}%</span>` : "–"}</td>
        <td class="hide-m">${lineChart(s, { w: 120, h: 28, mini: true })}</td></tr>`;
    }).join("")}</tbody></table>
    <p class="muted small">Zone con almeno 5 case confrontabili. Sono prezzi richiesti negli annunci, non prezzi di vendita effettivi.</p>
    ${omiTableHtml()}`;
}

// ---- prezzo giusto stimato e case simili
function stimaHtml(gid) {
  const h = houses(true).find(x => x.gid === gid), s = h?.stima;
  if (!s) return "";
  const err = Math.round(S.stima.mdape * 100);
  let verdict = "";
  if (s.pct != null) {
    const p = Math.round(Math.abs(s.pct));
    verdict = p < 5 ? "Il prezzo chiesto è <b>in linea</b> con la stima."
      : s.pct < 0 ? `Il prezzo chiesto è <b>${p}% sotto</b> la stima${s.deal ? ": <b>possibile affare</b>, vale la pena capire perché costa meno (stato, piano, spese, superficie reale)" : ""}.`
      : `Il prezzo chiesto è <b>${p}% sopra</b> la stima${p >= err ? ": c'è probabilmente margine per trattare" : ""}.`;
  } else if (!h.price) verdict = "Il prezzo non è pubblicato: la stima dà un'idea di cosa aspettarsi.";
  return `<div class="zt omi stima"><div><b>Prezzo giusto stimato: ${eur(s.v)}</b> <span class="muted">(8 case su 10 come questa chiedono tra ${eur(Math.round(s.lo / 1000) * 1000)} e ${eur(Math.round(s.hi / 1000) * 1000)})</span><br>
    ${verdict}${s.special ? " <span class=\"muted\">Annuncio particolare (es. nuda proprietà o già affittato): il confronto vale poco.</span>" : ""}
    <span class="muted small">Calcolata da ${num(S.stima.n)} annunci di abitazioni (${s.n} nella stessa zona) in base a superficie, zona, tipologia, stato, piano e ascensore, terrazzo, giardino, box, vista mare, bagni e classe energetica.
    Sbaglia in media del ${err}%: è un'indicazione, basata sui prezzi chiesti e non su quelli di vendita.</span></div></div>`;
}

// le case più simili oggi in vendita: stessa zona, stesso genere (appartamento o casa), superficie vicina
function similarHtml(gid, r) {
  const flat = t => ["appartamento", "attico"].includes(t);
  if (!r.mq || !["appartamento", "attico", "villa", "casa"].includes(r.type) || !zoneKey(r)) return "";
  const list = houses(false).filter(h => h.gid !== gid && h.price && h.rep.mq && zoneKey(h.rep) === zoneKey(r)
      && flat(h.rep.type) === flat(r.type) && Math.abs(h.rep.mq - r.mq) <= r.mq * 0.25 && !h.rep.features?._project)
    .sort((a, b) => Math.abs(a.rep.mq - r.mq) - Math.abs(b.rep.mq - r.mq)).slice(0, 5);
  if (list.length < 2) return "";
  return `<div><h3 style="font-size:15px;margin-bottom:6px">Case simili in vendita in zona</h3><table class="hist sim">
    ${list.sort((a, b) => a.price - b.price).map(h => `<tr><td><a href="#" data-open="${esc(h.gid)}">${esc(TYPE_LABELS[h.rep.type] || "")} ${h.rep.mq} mq</a><br><span class="muted small">${esc(COND_LABELS[h.rep.condition] || "stato non indicato")}${h.rep.floor ? " · " + esc(/piano|livell/i.test(h.rep.floor) ? h.rep.floor : "piano " + h.rep.floor) : ""}</span></td>
      <td class="n"><b>${eur(h.price)}</b><br><span class="muted small">${num(h.price / h.rep.mq)} €/mq</span></td></tr>`).join("")}</table></div>`;
}

// ---- quotazioni ufficiali OMI (Agenzia delle Entrate), per la zona OMI in cui cade la casa
const omiTip = (r, z) => ["villa", "casa"].includes(r.type) && z.valori["Ville e Villini"] ? "Ville e Villini" : "Abitazioni civili";
function omiCompare(r, members) {
  const key = (members || [r]).map(m => m.omi).find(Boolean);
  const z = key && S.omi?.zone?.[key];
  if (!z || !r.ppm || !DEAL_TYPES.concat(["villa", "casa"]).includes(r.type)) return null;
  const tip = omiTip(r, z), v = z.valori[tip];
  if (!v) return null;
  const [lo, hi, stato] = v;
  const pct = r.ppm > hi ? (r.ppm / hi - 1) * 100 : r.ppm < lo ? (r.ppm / lo - 1) * 100 : 0;
  return { z, tip, lo, hi, stato, pct };
}
function omiHtml(r, members) {
  const c = omiCompare(r, members);
  if (!c) return "";
  const where = c.pct > 0 ? `<b>${Math.round(c.pct)}% sopra il massimo</b>` : c.pct < 0 ? `<b>${Math.round(-c.pct)}% sotto il minimo</b>` : "<b>dentro la forbice</b>";
  return `<div class="zt omi"><div>Quotazione ufficiale OMI, zona ${esc(c.z.zona)} ${esc(c.z.nome)} (${esc(S.omi.semestre)}):
    ${esc(c.tip.toLowerCase())} da <b>${num(c.lo)}</b> a <b>${num(c.hi)} €/mq</b> in stato ${esc(c.stato)}. Questa casa: ${num(r.ppm)} €/mq, ${where}.
    <span class="muted small">Ristrutturate e nuove stanno di solito sopra. Fonte: Agenzia Entrate - OMI.</span></div></div>`;
}

function omiTableHtml() {
  if (!S.omi?.zone) return "";
  const asked = new Map();   // zona OMI -> €/mq chiesti (appartamenti usati, una volta per casa)
  for (const members of S.groups.values()) {
    const r = members.find(m => m.status === "active" && m.omi && m.ppm && DEAL_TYPES.includes(m.type) && m.condition !== "nuovo" && !m.features?._project);
    if (!r) continue;
    if (!asked.has(r.omi)) asked.set(r.omi, []);
    asked.get(r.omi).push(r.ppm);
  }
  const med = v => { const a = v.slice().sort((x, y) => x - y), n = a.length; return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2; };
  const rows = Object.entries(S.omi.zone).filter(([, z]) => z.valori["Abitazioni civili"])
    .map(([k, z]) => ({ k, z, v: z.valori["Abitazioni civili"], a: (asked.get(k) || []).length >= 5 ? med(asked.get(k)) : null, n: (asked.get(k) || []).length }))
    .sort((x, y) => (y.z.comune === "Trieste") - (x.z.comune === "Trieste") || x.z.comune.localeCompare(y.z.comune) || x.z.zona.localeCompare(y.z.zona));
  return `<h2 class="sec">Prezzi chiesti e quotazioni ufficiali</h2>
    <p class="muted small">Quotazioni OMI dell'Agenzia delle Entrate (${esc(S.omi.semestre)}), abitazioni civili in stato normale, confrontate con il €/mq medio chiesto negli annunci di appartamenti usati che cadono in quella zona OMI. Fonte: Agenzia Entrate - OMI.</p>
    <table class="src-table prices omi-t"><thead><tr><th>Zona OMI</th><th class="n">Ufficiale €/mq</th><th class="n">Chiesto €/mq</th><th class="n hide-m">Case</th></tr></thead><tbody>
    ${rows.map(({ z, v, a, n }) => `<tr><td>${esc(z.comune === "Trieste" ? "" : z.comune + " · ")}${esc(z.zona)} ${esc(z.nome)}</td>
      <td class="n">${num(v[0])}–${num(v[1])}</td>
      <td class="n">${a ? `<b>${num(a)}</b>${a > v[1] ? ` <span class="upc">(${pctTxt((a / v[1] - 1) * 100)})</span>` : ""}` : "–"}</td>
      <td class="n hide-m">${n || ""}</td></tr>`).join("")}</tbody></table>`;
}

// ---- quanto costa davvero (stime; impostazioni ricordate su questo dispositivo)
const CALC_DEF = { prima: true, anticipo: 20, tasso: 3.2, anni: 25, agenzia: 3, rendita: "" };
function calcCosts(price, r, c) {
  const builder = r.kind === "costruttore", priv = r.kind === "privato";
  let tax, taxNote;
  if (builder) {
    tax = price * (c.prima ? 0.04 : 0.10) + 600;
    taxNote = `IVA ${c.prima ? 4 : 10}% + imposte fisse (vendita da costruttore)`;
  } else {
    const val = c.rendita ? c.rendita * 1.05 * (c.prima ? 110 : 120) : price * 0.4;
    tax = Math.max(1000, val * (c.prima ? 0.02 : 0.09)) + 100;
    taxNote = `imposta di registro ${c.prima ? 2 : 9}% del valore catastale` + (c.rendita ? "" : ", qui ipotizzato al 40% del prezzo");
  }
  const agency = builder || priv ? 0 : price * c.agenzia / 100 * 1.22;
  const loan = Math.max(0, price * (1 - c.anticipo / 100));
  const notary = 2500 + (loan ? 1500 : 0);
  const loanCosts = loan ? loan * (c.prima ? 0.0025 : 0.02) + 1000 : 0;
  const i = c.tasso / 100 / 12, n = c.anni * 12;
  const rata = loan ? (i ? loan * i / (1 - Math.pow(1 + i, -n)) : loan / n) : 0;
  return { tax, taxNote, agency, notary, loan, loanCosts, rata, upfront: price - loan + tax + agency + notary + loanCosts, builder, priv };
}

function calcHtml(price, r) {
  const c = { ...CALC_DEF, ...store.get("calc", {}) };
  return `<details class="calc" ${store.get("calcOpen", false) ? "open" : ""}><summary>Quanto costa davvero</summary>
    <form class="calc-form" data-price="${price}" autocomplete="off">
      <label class="check"><input type="checkbox" name="prima" ${c.prima ? "checked" : ""}> Prima casa</label>
      <label>Anticipo % <input type="number" name="anticipo" min="0" max="100" step="5" value="${c.anticipo}"></label>
      <label>Tasso mutuo % <input type="number" name="tasso" min="0" max="15" step="0.1" value="${c.tasso}"></label>
      <label>Anni <input type="number" name="anni" min="5" max="40" step="5" value="${c.anni}"></label>
      ${r.kind === "agenzia" ? `<label>Provvigione agenzia % <input type="number" name="agenzia" min="0" max="6" step="0.5" value="${c.agenzia}"></label>` : ""}
      ${r.kind !== "costruttore" ? `<label>Rendita catastale € <input type="number" name="rendita" min="0" step="10" value="${c.rendita}" placeholder="se la sai"></label>` : ""}
    </form>
    <div class="calc-out"></div></details>`;
}

function fillCalc(form, r) {
  const fd = new FormData(form), price = +form.dataset.price;
  const c = { prima: form.elements.prima.checked, anticipo: +fd.get("anticipo") || 0, tasso: +fd.get("tasso") || 0,
    anni: +fd.get("anni") || 25, agenzia: fd.get("agenzia") != null ? +fd.get("agenzia") : CALC_DEF.agenzia,
    rendita: fd.get("rendita") ? +fd.get("rendita") : "" };
  if (fd.get("agenzia") == null) c.agenzia = store.get("calc", {}).agenzia ?? CALC_DEF.agenzia;   // campo assente (privato/costruttore)
  store.set("calc", c);
  const x = calcCosts(price, r, c);
  const row = (l, v, note) => `<tr><td>${l}${note ? `<br><span class="muted small">${note}</span>` : ""}</td><td class="n">${eur(v)}</td></tr>`;
  form.nextElementSibling.innerHTML = `<table class="calc-t">
    ${row("Prezzo", price)}
    ${row("Imposte", x.tax, esc(x.taxNote))}
    ${x.builder || x.priv ? "" : row("Agenzia", x.agency, `${c.agenzia}% + IVA, spesso trattabile`)}
    ${row("Notaio", x.notary, "stima, compreso l'atto del mutuo")}
    ${x.loan ? row("Spese del mutuo", x.loanCosts, `imposta ${c.prima ? "0,25" : "2"}% + perizia e istruttoria (stima)`) : ""}
    <tr class="tot"><td>Soldi da avere al rogito</td><td class="n">${eur(x.upfront)}</td></tr>
    ${x.loan ? `<tr class="tot"><td>Mutuo di ${eur(x.loan)}: rata al mese</td><td class="n">${eur(x.rata)}</td></tr>` : ""}
    </table><p class="muted small">Stime indicative: chiedi conferma a notaio e banca.</p>`;
}

function mineHtml(gid) {
  const n = notes.get(gid);
  return `<div class="mine"><h3>I tuoi appunti</h3>
    <div class="chips">${Object.entries(STATUS).map(([k, l]) => `<button type="button" class="chip-btn ${n.status === k ? "on" : ""}" data-status="${k}" data-gid="${esc(gid)}">${l}</button>`).join("")}</div>
    <textarea data-note="${esc(gid)}" rows="3" placeholder="Impressioni della visita, domande per l'agenzia, spese condominiali…">${esc(n.note || "")}</textarea>
    <p class="muted small">Salvati solo su questo dispositivo.</p></div>`;
}

function setStatus(gid, status) {
  const n = notes.get(gid);
  n.status = n.status === status ? null : status;
  notes.set(gid, n);
  // una casa con uno stato (tranne "scartata") va tra i preferiti, così si ritrova
  if (n.status && n.status !== "scartata" && !S.favs.has(gid)) toggleFav(gid);
  document.querySelectorAll(`.chip-btn[data-gid="${CSS.escape(gid)}"]`).forEach(b => b.classList.toggle("on", b.dataset.status === n.status));
}

// ---- confronto dei preferiti, fianco a fianco
function compareHtml(list) {
  if (!list.length) return `<div class="empty">Nessun preferito da confrontare.</div>`;
  const yes = v => v === true ? "Sì" : v === false ? "No" : "–";
  const story = h => {
    const days = Math.round((Date.now() - new Date(h.firstSeen)) / 864e5);
    const pc = priceChange(h.rep);
    return { days, drop: pc && pc.pct < 0 ? pctTxt(pc.pct) : "–" };
  };
  const best = (vals, dir) => { const nums = vals.filter(v => v != null); return nums.length > 1 ? (dir < 0 ? Math.min(...nums) : Math.max(...nums)) : null; };
  const prices = list.map(h => h.price), ppms = list.map(h => h.rep.ppm), mqs = list.map(h => h.rep.mq);
  const bp = best(prices, -1), bppm = best(ppms, -1), bmq = best(mqs, 1);
  const rows = [
    ["Prezzo", h => eur(h.price), h => h.price && h.price === bp],
    ["Superficie", h => h.rep.mq ? h.rep.mq + " mq" : "–", h => h.rep.mq && h.rep.mq === bmq],
    ["€/mq", h => h.rep.ppm ? num(h.rep.ppm) : "–", h => h.rep.ppm && h.rep.ppm === bppm],
    ["Prezzo stimato", h => h.stima ? eur(h.stima.v) + (h.stima.pct != null ? ` (${pctTxt(h.stima.pct)})` : "") : "–"],
    ["Rispetto alla zona", h => h.rep.zoneCmp ? pctTxt(h.rep.zoneCmp.pct) : "–"],
    ["Quotazione OMI", h => { const c = omiCompare(h.rep, h.members); return c ? `${num(c.lo)}–${num(c.hi)}` + (c.pct ? ` (${Math.round(Math.abs(c.pct))}% ${c.pct > 0 ? "sopra" : "sotto"})` : "") : "–"; }],
    ["Zona", h => esc(where(h.rep))],
    ["Locali", h => h.rep.rooms || "–"], ["Bagni", h => h.rep.bathrooms || "–"], ["Piano", h => esc(h.rep.floor || "–")],
    ["Stato", h => esc(COND_LABELS[h.rep.condition] || "–")], ["Classe energetica", h => esc(h.rep.energy || "–")],
    ...Object.entries(FEATURES).map(([k, l]) => [l, h => yes(h.rep.features?.[k])]),
    ["In vendita da", h => story(h).days + " giorni"], ["Ribasso", h => story(h).drop],
    ["Agenzie", h => h.members.length],
    ["Il tuo stato", h => esc(STATUS[notes.get(h.gid).status] || "–")],
    ["Appunti", h => `<span class="note">${esc(notes.get(h.gid).note || "–")}</span>`],
  ];
  return `<div class="cmp-wrap"><table class="cmp"><thead><tr><th></th>${list.map(h => `<th><a href="#" data-open="${esc(h.gid)}">
      ${h.rep.images?.[0] ? `<img src="${esc(h.rep.images[0])}" alt="" referrerpolicy="no-referrer" loading="lazy" onerror="this.remove()">` : ""}
      <span>${esc(TYPE_LABELS[h.rep.type] || "Immobile")}</span></a></th>`).join("")}</tr></thead>
    <tbody>${rows.map(([l, f, isBest]) => `<tr><th>${l}</th>${list.map(h => `<td class="${isBest && isBest(h) ? "best" : ""}">${f(h)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="muted small">In grassetto il valore migliore della riga.</p>`;
}

// ------------------------------------------------------------------ impostazioni notifiche

const NDEFAULT = { enabled: true, events: ["new", "price_down", "price_up", "removed", "sold", "deal"], max_single: 5,
  filters: { types: DEFAULT_TYPES, include_noprice: true } };
const NEV = { new: "Nuovo annuncio", price_down: "Ribasso di prezzo", price_up: "Rialzo di prezzo", removed: "Non più online (venduto/ritirato)", sold: "Segnato come venduto",
  deal: "💎 Possibile affare (molto sotto il prezzo stimato)" };
let NDRAFT = null;

function repoInfo() {
  const gh = location.hostname.endsWith(".github.io");
  return { owner: gh ? location.hostname.split(".")[0] : "matt99flash", repo: gh ? (location.pathname.split("/")[1] || "case-trieste") : "case-trieste" };
}

function nChips(id, name, entries, selected) {
  $(id).innerHTML = entries.map(([v, label]) => `<label class="chip"><input type="checkbox" name="${name}" value="${esc(v)}" ${selected.includes(v) ? "checked" : ""}><span>${esc(label)}</span></label>`).join("");
}

function fillNotifyForm(cfg) {
  const f = cfg.filters || {}, form = $("#notify-form");
  form.elements.enabled.checked = cfg.enabled !== false;
  nChips("#n-events", "events", Object.entries(NEV), cfg.events || []);
  nChips("#n-types", "types", Object.entries(TYPE_LABELS), f.types || []);
  nChips("#n-conditions", "conditions", Object.entries(COND_LABELS), f.conditions || []);
  nChips("#n-towns", "towns", Object.keys(TOWN_CENTER).map(t => [t, t]), f.towns || []);
  nChips("#n-zones", "zones", Object.keys(ZONE_CENTER).map(z => [z, z]).concat([["nd", "Zona non indicata"]]), f.zones || []);
  nChips("#n-features", "features", Object.entries(FEATURES), f.features || []);
  nChips("#n-kinds", "kinds", Object.entries(KINDS), f.kinds || []);
  for (const k of ["price_min", "price_max", "mq_min", "mq_max", "rooms_min"]) form.elements[k].value = f[k] ?? "";
  form.elements.keywords.value = f.keywords || "";
  form.elements.include_noprice.checked = f.include_noprice !== false;
  form.elements.max_single.value = String(cfg.max_single || 5);
}

function readNotifyForm() {
  const form = $("#notify-form"), fd = new FormData(form), num = k => fd.get(k) ? Number(fd.get(k)) : null;
  const filters = { price_min: num("price_min"), price_max: num("price_max"), mq_min: num("mq_min"), mq_max: num("mq_max"),
    rooms_min: num("rooms_min"), types: fd.getAll("types"), conditions: fd.getAll("conditions"), towns: fd.getAll("towns"),
    zones: fd.getAll("zones"), features: fd.getAll("features"), kinds: fd.getAll("kinds"),
    keywords: (fd.get("keywords") || "").trim() || null, include_noprice: form.elements.include_noprice.checked };
  for (const k of Object.keys(filters)) if (filters[k] === null || (Array.isArray(filters[k]) && !filters[k].length)) delete filters[k];
  return { enabled: form.elements.enabled.checked, events: fd.getAll("events"), max_single: Number(fd.get("max_single")) || 5, filters };
}

function notifyMatches(r, f) {
  if (!r.price && f.include_noprice === false) return false;
  if (f.price_min && r.price && r.price < f.price_min) return false;
  if (f.price_max && r.price && r.price > f.price_max) return false;
  if (f.mq_min && (!r.mq || r.mq < f.mq_min)) return false;
  if (f.mq_max && r.mq && r.mq > f.mq_max) return false;
  if (f.rooms_min && (!roomsOf(r) || roomsOf(r) < f.rooms_min)) return false;
  if (f.types?.length && !f.types.includes(r.type || "altro")) return false;
  if (f.conditions?.length && !f.conditions.includes(r.condition || "nd")) return false;
  if (f.towns?.length && !f.towns.includes(r.town)) return false;
  if (f.zones?.length && r.town === "Trieste" && !f.zones.includes(r.zone || "nd")) return false;
  if (f.features?.some(x => r.features?.[x] !== true)) return false;
  if (f.kinds?.length && !f.kinds.includes(r.kind)) return false;
  if (f.keywords) {
    const blob = [r.title, r.description, r.address, r.zone].join(" ").toLowerCase();
    if (!f.keywords.toLowerCase().split(/\s+/).every(w => blob.includes(w))) return false;
  }
  return true;
}

function describeNotify(cfg) {
  if (cfg.enabled === false) return "Notifiche sospese.";
  const f = cfg.filters || {}, b = [];
  if (f.price_min || f.price_max) b.push("prezzo " + (f.price_min ? "da " + eur(f.price_min) + " " : "") + (f.price_max ? "fino a " + eur(f.price_max) : ""));
  if (f.mq_min || f.mq_max) b.push("superficie " + (f.mq_min ? "da " + f.mq_min + " " : "") + (f.mq_max ? "fino a " + f.mq_max + " " : "") + "mq");
  if (f.rooms_min) b.push(`almeno ${f.rooms_min} locali`);
  if (f.towns?.length) b.push("comuni: " + f.towns.join(", "));
  if (f.zones?.length) b.push("zone: " + f.zones.map(z => z === "nd" ? "non indicata" : z).join(", "));
  if (f.features?.length) b.push("con " + f.features.map(x => FEATURES[x].toLowerCase()).join(", "));
  if (f.types?.length) b.push(f.types.map(t => TYPE_LABELS[t]).join(", "));
  return (b.length ? b.join(" · ") : "nessun limite") + ". Avvisi per: " + (cfg.events || []).map(e => NEV[e].toLowerCase()).join(", ") + ".";
}

function updateNotifyPreview() {
  const cfg = readNotifyForm();
  const n = houses(false).filter(h => h.members.some(m => notifyMatches(m, cfg.filters))).length;
  const oldest = S.events.length ? new Date(S.events[S.events.length - 1].ts) : new Date();
  const days = Math.max(1, (Date.now() - oldest) / 864e5);
  const recent = S.events.filter(e => e.type === "new" && S.byId.get(e.id) && notifyMatches(S.byId.get(e.id), cfg.filters)).length;
  $("#n-preview").textContent = `Con questi limiti corrispondono ${n.toLocaleString("it-IT")} immobili oggi in vendita` +
    (S.events.length ? ` · circa ${(Math.round(recent / days * 10) / 10).toLocaleString("it-IT")} nuovi annunci al giorno.` : ".");
}

function renderNotify() {
  const saved = S.notify || NDEFAULT;
  $("#n-current").innerHTML = `<b>Impostazioni attuali:</b> ${esc(describeNotify(saved))}`;
  if (!NDRAFT) { NDRAFT = true; fillNotifyForm(saved); }
  updateNotifyPreview();
}

function copyDashboardFilters() {
  const cur = readNotifyForm();
  fillNotifyForm({ ...cur, filters: {
    price_min: F.pmin ? Number(F.pmin) : null, price_max: F.pmax ? Number(F.pmax) : null,
    mq_min: F.mqmin ? Number(F.mqmin) : null, mq_max: F.mqmax ? Number(F.mqmax) : null, rooms_min: F.rooms ? Number(F.rooms) : null,
    types: F.types, conditions: F.conditions, towns: F.towns, zones: F.zones, features: F.features, kinds: F.kinds,
    keywords: F.q || null, include_noprice: F.noprice } });
  updateNotifyPreview();
}

function saveNotify() {
  const cfg = readNotifyForm();
  if (!cfg.events.length && cfg.enabled) { alert("Scegli almeno un tipo di novità, oppure disattiva le notifiche."); return; }
  const { owner, repo } = repoInfo();
  const when = new Date().toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const body = "Richiesta inviata dalla dashboard. Tocca **Create** per salvarla: verrà applicata in automatico.\n\n" +
    describeNotify(cfg) + "\n\n```json\n" + JSON.stringify(cfg) + "\n```";
  const url = `https://github.com/${owner}/${repo}/issues/new?title=${encodeURIComponent("Impostazioni notifiche " + when)}&body=${encodeURIComponent(body)}`;
  window.open(url, "_blank", "noopener");
}

// ------------------------------------------------------------------ preferiti su più dispositivi
// I dispositivi "collegati" leggono docs/favorites.json; chi apre il link senza collegarsi ha solo i propri preferiti.
// Si salvano come codici brevi (hash dell'id della casa) tramite una issue GitHub, come le impostazioni notifiche.

const favSync = { get: () => store.get("favsync", { linked: false, base: null }), set: v => store.set("favsync", v) };
const fnv = s => { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; } return h.toString(16).padStart(8, "0"); };
const codesOf = gids => [...new Set([...gids].map(fnv))].sort();
const sameCodes = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

async function loadRemoteFavs() {
  try { const r = await fetch(`favorites.json?t=${Date.now()}`); return r.ok ? await r.json() : null; } catch { return null; }
}

function applyRemoteFavs(linkNow) {
  const st = favSync.get();
  if (!st.linked && !linkNow) return;
  const remote = S.remoteFavs || { updated: null, favs: [] };
  if (linkNow || !st.base || st.base.updated !== remote.updated) {
    const want = new Set(remote.favs || []);
    const gids = [...S.groups.keys()].filter(g => want.has(fnv(g)));
    const untouched = st.base && sameCodes(codesOf(S.favs), st.base.codes);
    if (untouched) S.favs = new Set(gids); else gids.forEach(g => S.favs.add(g));
    store.set("favs", [...S.favs]);
    st.base = { updated: remote.updated, codes: codesOf(gids) };
  }
  st.linked = true;
  favSync.set(st);
}

const favDirty = () => { const st = favSync.get(); return st.linked && !sameCodes(codesOf(S.favs), st.base?.codes || []); };

function updateFavCount() {
  $("#fav-count").textContent = (S.favs.size || "") + (favDirty() ? " •" : "");
}

function renderFavSync() {
  const st = favSync.get(), box = $("#fav-sync");
  if (!st.linked) {
    $("#fav-note").textContent = "I preferiti restano salvati su questo dispositivo.";
    box.innerHTML = `<p>Usi la dashboard sia sul telefono sia sul PC? Collega questo dispositivo per avere gli stessi preferiti ovunque.
      <span class="muted small">Riservato al proprietario della dashboard: chi riceve il link tiene i propri preferiti.</span></p>
      <div class="btns"><button class="btn ghost" data-favsync="link">Collega questo dispositivo</button></div>`;
    return;
  }
  $("#fav-note").textContent = "Dispositivo collegato: dopo aver aggiunto o tolto preferiti premi «Salva su tutti i dispositivi».";
  if (!favDirty()) {
    box.innerHTML = `<p>✓ Preferiti uguali su tutti i tuoi dispositivi collegati.</p>
      <div class="btns"><button class="btn ghost" data-favsync="unlink">Scollega</button></div>`;
    return;
  }
  const pending = st.pending && Date.now() - st.pending < 30 * 60e3;
  box.innerHTML = `<p><b>Preferiti modificati su questo dispositivo.</b> ${pending
    ? "Salvataggio inviato: se hai toccato <i>Create</i> su GitHub, entro qualche minuto compaiono anche sugli altri dispositivi."
    : "Salvali per vederli anche sugli altri dispositivi."}</p>
    <div class="btns"><button class="btn" data-favsync="save">Salva su tutti i dispositivi</button></div>`;
}

function saveFavs() {
  const codes = codesOf(S.favs), { owner, repo } = repoInfo();
  const when = new Date().toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const body = "Richiesta inviata dalla dashboard. Tocca **Create** per salvarla: verrà applicata in automatico.\n\n" +
    `${codes.length} preferiti.\n\n` + "```json\n" + JSON.stringify({ favs: codes }) + "\n```";
  window.open(`https://github.com/${owner}/${repo}/issues/new?title=${encodeURIComponent("Preferiti " + when)}&body=${encodeURIComponent(body)}`, "_blank", "noopener");
  const st = favSync.get();
  st.pending = Date.now();
  favSync.set(st);
  renderFavSync();
}

function favSyncAction(what) {
  if (what === "link") applyRemoteFavs(true);
  else if (what === "unlink") { const st = favSync.get(); st.linked = false; st.base = null; favSync.set(st); }
  else if (what === "save") { saveFavs(); return; }
  renderFavs();
}

// ------------------------------------------------------------------ navigazione

function showTab() {
  if ($("#detail").open) $("#detail").close();
  const tab = (location.hash.slice(1) || "annunci").split("/")[0];
  const valid = ["novita", "annunci", "mappa", "preferiti", "prezzi", "notifiche", "fonti"].includes(tab) ? tab : "annunci";
  document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("active", a.dataset.tab === valid));
  document.querySelectorAll(".tab").forEach(s => s.hidden = s.id !== "tab-" + valid);
  $("#filters").hidden = valid === "fonti" || valid === "notifiche" || valid === "prezzi";
  render(valid);
}

function render(tab) {
  tab = (tab || location.hash.slice(1) || "annunci").split("/")[0];
  if (tab === "novita") renderEvents();
  else if (tab === "mappa") renderMap();
  else if (tab === "preferiti") renderFavs();
  else if (tab === "fonti") renderSources();
  else if (tab === "prezzi") renderPrices();
  else if (tab === "notifiche") renderNotify();
  else renderGrid();
  updateFavCount();
}

function toggleFav(gid) {
  S.favs.has(gid) ? S.favs.delete(gid) : S.favs.add(gid);
  store.set("favs", [...S.favs]);
  document.querySelectorAll(`[data-fav="${CSS.escape(gid)}"]`).forEach(b => b.classList.toggle("on", S.favs.has(gid)));
  updateFavCount();
}

function bind() {
  window.addEventListener("hashchange", showTab);
  let t;
  $("#filter-form").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { readForm(); S.shown = 60; render(); }, 200); });
  $("#reset").addEventListener("click", () => { F = { ...DEFAULT_F }; store.set("filters", F); buildFilters(); render(); });
  document.querySelectorAll(".sort-top").forEach(el => el.addEventListener("change", e => {
    F.sort = e.target.value; store.set("filters", F); S.shown = 60; syncSort(); render();
  }));
  $("#filters-toggle").addEventListener("click", e => {
    const open = $("#filters").classList.toggle("open");
    e.currentTarget.setAttribute("aria-expanded", open);
  });
  $("#more").addEventListener("click", () => { S.shown += 60; renderGrid(); });
  $("#notify-form").addEventListener("input", () => updateNotifyPreview());
  $("#n-copy").addEventListener("click", copyDashboardFilters);
  $("#n-save").addEventListener("click", saveNotify);
  $("#ev-days").addEventListener("change", renderEvents);
  $("#ev-types").addEventListener("change", () => {
    store.set("evtypes", [...document.querySelectorAll("#ev-types input:checked")].map(i => i.value));
    renderEvents();
  });
  document.addEventListener("click", e => {
    const fs = e.target.closest("[data-favsync]");
    if (fs) { favSyncAction(fs.dataset.favsync); return; }
    const fav = e.target.closest("[data-fav]");
    if (fav) { e.stopPropagation(); toggleFav(fav.dataset.fav); if (location.hash === "#preferiti") renderFavs(); return; }
    if (e.target.closest("[data-since]")) { ONLY_SINCE = !ONLY_SINCE; S.shown = 60; renderGrid(); return; }
    const st = e.target.closest("[data-status]");
    if (st) { setStatus(st.dataset.gid, st.dataset.status); return; }
    const fv = e.target.closest("[data-favview]");
    if (fv) { store.set("favview", fv.dataset.favview); renderFavs(); return; }
    const zr = e.target.closest("tr[data-zone]");
    if (zr) { history.replaceState(null, "", "#prezzi/" + encodeURIComponent(zr.dataset.zone)); renderPrices(); return; }
    const open = e.target.closest("[data-open]");
    if (open) { e.preventDefault(); openDetail(open.dataset.open); return; }
    if (e.target.closest(".close")) { $("#detail").close(); return; }
    const c = e.target.closest(".card, .ev");
    if (c) openDetail(c.dataset.gid);
  });
  $("#detail").addEventListener("click", e => { if (e.target === $("#detail")) $("#detail").close(); });
  $("#detail").addEventListener("input", e => {
    const nt = e.target.closest("[data-note]");
    if (nt) { const n = notes.get(nt.dataset.note); n.note = nt.value.trim() ? nt.value : ""; notes.set(nt.dataset.note, n); return; }
    const cf = e.target.closest(".calc-form");
    if (cf && S.openRep) fillCalc(cf, S.openRep);
  });
  document.addEventListener("toggle", e => { if (e.target.classList?.contains("calc")) store.set("calcOpen", e.target.open); }, true);
  $("#fav-hide").addEventListener("change", e => { store.set("favhide", e.target.checked); renderFavs(); });
  document.addEventListener("pointermove", chartHover);
  document.addEventListener("pointerout", chartLeave);
}

(async function init() {
  bind();
  // installabile come app e consultabile anche senza connessione (ultima versione scaricata)
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  try {
    await load();
    S.remoteFavs = await loadRemoteFavs();
    applyRemoteFavs(false);
  } catch (e) {
    $("#updated").textContent = "Dati non ancora disponibili: il primo controllo automatico non è ancora terminato.";
    showTab();
    return;
  }
  const act = S.listings.filter(r => r.status === "active").length;
  $("#updated").textContent = (navigator.onLine ? "" : "Sei offline: dati salvati · ") + `${act.toLocaleString("it-IT")} annunci da ${Object.keys(S.sources).length} fonti · aggiornato ${new Date(S.updated).toLocaleString("it-IT", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
  buildFilters();
  if (!location.hash) history.replaceState(null, "", "#annunci");
  showTab();
  // la ricerca per parole cerca anche nelle descrizioni: ripeti appena sono disponibili
  loadDetails().then(() => { if (F.q || location.hash.startsWith("#notifiche")) render(); });
})();
