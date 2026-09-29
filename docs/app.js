"use strict";

const TYPE_LABELS = { appartamento: "Appartamento", attico: "Attico / Mansarda", villa: "Villa / Villetta", casa: "Casa indipendente", terreno: "Terreno", box: "Box / Posto auto", commerciale: "Commerciale", altro: "Altro" };
const DEFAULT_TYPES = ["appartamento", "attico", "villa", "casa"];
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
const eur = v => v ? "€ " + Math.round(v).toLocaleString("it-IT") : "Prezzo su richiesta";
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage non disponibile */ } },
};

const S = { listings: [], byId: new Map(), groups: new Map(), events: [], sources: {}, updated: null,
  favs: new Set(store.get("favs", [])), shown: 60, map: null, markers: null };

// ------------------------------------------------------------------ dati

async function load() {
  const get = async f => { const r = await fetch(`data/${f}?t=${Date.now()}`); if (!r.ok) throw new Error(f); return r.json(); };
  const [l, e, s] = await Promise.all([get("listings.json"), get("events.json").catch(() => ({ events: [] })), get("sources.json").catch(() => ({ sources: {} }))]);
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
    out.push({ gid, rep, members: pool, all: members, active: act.length > 0, firstSeen,
      price: Math.min(...pool.map(m => m.price || Infinity)) === Infinity ? null : Math.min(...pool.map(m => m.price || Infinity)) });
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
    noprice: form.elements.noprice.checked, removed: form.elements.removed.checked };
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
  if (rooms && (!r.rooms || r.rooms < rooms)) return false;
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

function sortHouses(list) {
  const by = {
    recent: (a, b) => b.firstSeen.localeCompare(a.firstSeen),
    price_asc: (a, b) => (a.price || 9e12) - (b.price || 9e12),
    price_desc: (a, b) => (b.price || 0) - (a.price || 0),
    ppm_asc: (a, b) => (a.rep.ppm || 9e12) - (b.rep.ppm || 9e12),
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
    h.active && isNew(h) && `<span class="badge new">Nuovo</span>`,
    pc && pc.pct < 0 && `<span class="badge down">${pc.pct.toFixed(0)}%</span>`,
    pc && pc.pct > 0 && `<span class="badge up">+${pc.pct.toFixed(0)}%</span>`,
    r.sold && `<span class="badge gone">Venduto / trattativa</span>`,
    r.private && `<span class="badge priv">Privato</span>`,
  ].filter(Boolean).join("");
  const others = h.members.length > 1 ? ` · anche su altre ${h.members.length - 1}` : "";
  return `<article class="card ${h.active ? "" : "gone"}" data-gid="${esc(h.gid)}">
    <div class="thumb">${img ? `<img loading="lazy" src="${esc(img)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}<div class="noimg">${img ? "" : "Nessuna foto"}</div>
      <div class="badges">${badges}</div>
      <button class="star ${S.favs.has(h.gid) ? "on" : ""}" data-fav="${esc(h.gid)}" aria-label="Preferito" title="Aggiungi ai preferiti">★</button></div>
    <div class="body">
      <div class="price">${r.features?._project && h.price ? "da " : ""}${eur(h.price)}${pc ? `<span class="old">${eur(pc.first)}</span>` : ""}</div>
      <div class="specs">${esc(TYPE_LABELS[r.type] || "Immobile")}${specs(r) ? " · " + esc(specs(r)) : ""}</div>
      <div class="where">${esc(where(r))}${r.ppm ? ` · ${Math.round(r.ppm).toLocaleString("it-IT")} €/mq` : ""}</div>
      <div class="title">${esc(r.title || "")}</div>
      <div class="src">${esc(r.srcName)}${esc(others)} · online ${ago(h.firstSeen)}</div>
    </div></article>`;
}

function syncSort() {
  document.querySelectorAll(".sort-top").forEach(el => { el.value = F.sort; });
  const f = $("#filter-form");
  if (f) f.elements.sort.value = F.sort;
}

function renderGrid() {
  syncSort();
  const list = sortHouses(houses(F.removed).filter(matchHouse));
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
  const list = sortHouses(houses(true).filter(h => S.favs.has(h.gid)));
  $("#favs").innerHTML = list.length ? list.map(card).join("") : `<div class="empty">Tocca ★ su un annuncio per salvarlo qui. I preferiti restano su questo dispositivo.</div>`;
  $("#fav-count").textContent = S.favs.size || "";
}

function renderSources() {
  const rows = Object.entries(S.sources).sort((a, b) => (b[1].last_count || 0) - (a[1].last_count || 0));
  const active = S.listings.filter(r => r.status === "active");
  const cnt = active.reduce((m, r) => (m[r.source] = (m[r.source] || 0) + 1, m), {});
  const ok = rows.filter(([, s]) => s.last_ok && !s.fail_streak).length;
  $("#sources").innerHTML = `<p class="small">${ok} fonti su ${rows.length} lette correttamente all'ultimo controllo · ${active.length.toLocaleString("it-IT")} annunci attivi.</p>
  <table class="src-table"><thead><tr><th>Fonte</th><th class="n">Annunci</th><th class="hide-m">Ultima lettura riuscita</th><th class="hide-m">Note</th></tr></thead><tbody>${rows.map(([id, s]) => {
    const cls = !s.last_ok ? "err" : s.fail_streak ? "warn" : "";
    return `<tr><td><span class="dot ${cls}"></span>${s.website ? `<a href="${esc(s.website)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)}<br><span class="muted small">${esc(KINDS[s.kind] || s.kind || "")}</span></td>
      <td class="n">${cnt[id] || 0}</td><td class="hide-m">${s.last_ok ? fmtDate(s.last_ok) + " " + new Date(s.last_ok).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "mai"}</td>
      <td class="hide-m small muted">${esc(s.fail_streak ? s.last_error || "" : "")}</td></tr>`;
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
  let exact = 0, approx = 0;
  for (const h of list) {
    const r = h.members.find(m => m.lat) || h.rep;
    let ll = r.lat && r.lon ? [r.lat, r.lon] : null;
    if (ll) exact++;
    else {
      const c = (r.town === "Trieste" && ZONE_CENTER[r.zone]) || TOWN_CENTER[r.town];
      if (!c) continue;
      const seed = [...h.gid].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
      ll = [c[0] + ((seed % 1000) / 1000 - .5) * .012, c[1] + ((Math.floor(seed / 1000) % 1000) / 1000 - .5) * .016];
      approx++;
    }
    const m = L.circleMarker(ll, { radius: 7, weight: 2, color: r.lat ? "#0f6e7c" : "#8a8277", fillOpacity: .75 });
    const img = h.rep.images?.[0];
    m.bindPopup(`${img ? `<img src="${esc(img)}" referrerpolicy="no-referrer" alt="">` : ""}<b>${eur(h.price)}</b><br>${esc(TYPE_LABELS[h.rep.type] || "")} ${esc(specs(h.rep))}<br>${esc(where(h.rep))}<br><a href="#" data-open="${esc(h.gid)}">Apri scheda</a>`);
    S.markers.addLayer(m);
  }
  $("#map-note").textContent = `${exact} immobili con posizione esatta (blu), ${approx} posizionati al centro della zona indicata (grigio).`;
  setTimeout(() => S.map.invalidateSize(), 50);
}

function openDetail(gid) {
  const members = S.groups.get(gid);
  if (!members) return;
  const act = members.filter(m => m.status === "active");
  const list = (act.length ? act : members).slice().sort((a, b) => score(b) - score(a));
  const r = list[0];
  const imgs = [...new Set(list.flatMap(m => m.images || []))].slice(0, 16);
  const facts = [
    [(r.features?._project ? "da " : "") + eur(Math.min(...list.map(m => m.price || Infinity)) === Infinity ? null : Math.min(...list.map(m => m.price || Infinity))), r.features?._project ? "Prezzo minimo del cantiere" : "Prezzo"],
    [r.mq && `${r.mq} mq`, "Superficie"], [r.ppm && `${Math.round(r.ppm).toLocaleString("it-IT")} €/mq`, "Prezzo al mq"],
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
      <div><h3 style="font-size:15px;margin-bottom:8px">Dove è in vendita</h3><div class="offers">${members.map(m => `<div class="offer"><div><b>${esc(m.srcName)}</b>${m.private ? " (privato)" : ""}<br><span class="muted small">${eur(m.price)}${m.ref ? " · rif. " + esc(m.ref) : ""}${m.status !== "active" ? " · non più online dal " + fmtDate(m.removed_at) : ""}</span></div><a class="btn" href="${esc(m.url)}" target="_blank" rel="noopener">Vedi annuncio</a></div>`).join("")}</div></div>
      ${hist.length > 1 ? `<div><h3 style="font-size:15px;margin-bottom:6px">Storico prezzi</h3><table class="hist">${hist.map(([ts, p, s]) => `<tr><td>${fmtDate(ts)}</td><td><b>${eur(p)}</b></td><td class="muted">${esc(s)}</td></tr>`).join("")}</table></div>` : ""}
      ${r.title ? `<div><b>${esc(r.title)}</b></div>` : ""}
      ${r.description ? `<div class="desc">${esc(r.description)}</div>` : ""}
      <p class="muted small">Primo avvistamento: ${fmtDate(firstSeen)}. I dati sono letti in automatico dal sito dell'agenzia: verifica sempre sull'annuncio originale.</p>
    </div>`;
  const dlg = $("#detail");
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
}

// ------------------------------------------------------------------ impostazioni notifiche

const NDEFAULT = { enabled: true, events: ["new", "price_down", "price_up", "removed", "sold"], max_single: 5,
  filters: { types: DEFAULT_TYPES, include_noprice: true } };
const NEV = { new: "Nuovo annuncio", price_down: "Ribasso di prezzo", price_up: "Rialzo di prezzo", removed: "Non più online (venduto/ritirato)", sold: "Segnato come venduto" };
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
  if (f.rooms_min && (!r.rooms || r.rooms < f.rooms_min)) return false;
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

// ------------------------------------------------------------------ navigazione

function showTab() {
  const tab = (location.hash.slice(1) || "annunci").split("/")[0];
  const valid = ["novita", "annunci", "mappa", "preferiti", "notifiche", "fonti"].includes(tab) ? tab : "annunci";
  document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("active", a.dataset.tab === valid));
  document.querySelectorAll(".tab").forEach(s => s.hidden = s.id !== "tab-" + valid);
  $("#filters").hidden = valid === "fonti" || valid === "notifiche";
  render(valid);
}

function render(tab) {
  tab = tab || (location.hash.slice(1) || "annunci");
  if (tab === "novita") renderEvents();
  else if (tab === "mappa") renderMap();
  else if (tab === "preferiti") renderFavs();
  else if (tab === "fonti") renderSources();
  else if (tab === "notifiche") renderNotify();
  else renderGrid();
  $("#fav-count").textContent = S.favs.size || "";
}

function toggleFav(gid) {
  S.favs.has(gid) ? S.favs.delete(gid) : S.favs.add(gid);
  store.set("favs", [...S.favs]);
  document.querySelectorAll(`[data-fav="${CSS.escape(gid)}"]`).forEach(b => b.classList.toggle("on", S.favs.has(gid)));
  $("#fav-count").textContent = S.favs.size || "";
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
    const fav = e.target.closest("[data-fav]");
    if (fav) { e.stopPropagation(); toggleFav(fav.dataset.fav); if (location.hash === "#preferiti") renderFavs(); return; }
    const open = e.target.closest("[data-open]");
    if (open) { e.preventDefault(); openDetail(open.dataset.open); return; }
    if (e.target.closest(".close")) { $("#detail").close(); return; }
    const c = e.target.closest(".card, .ev");
    if (c) openDetail(c.dataset.gid);
  });
  $("#detail").addEventListener("click", e => { if (e.target === $("#detail")) $("#detail").close(); });
}

(async function init() {
  bind();
  try {
    await load();
  } catch (e) {
    $("#updated").textContent = "Dati non ancora disponibili: il primo controllo automatico non è ancora terminato.";
    showTab();
    return;
  }
  const act = S.listings.filter(r => r.status === "active").length;
  $("#updated").textContent = `${act.toLocaleString("it-IT")} annunci da ${Object.keys(S.sources).length} fonti · aggiornato ${new Date(S.updated).toLocaleString("it-IT", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
  buildFilters();
  if (!location.hash) history.replaceState(null, "", "#annunci");
  showTab();
})();
