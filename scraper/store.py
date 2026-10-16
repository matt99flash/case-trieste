"""Archivio degli annunci (docs/data/*.json) e calcolo delle novità tra un giro e l'altro."""
import json
import os
from datetime import datetime, timedelta, timezone

from .clean import clean_record

# CASE_DATA_DIR permette prove in una cartella separata senza toccare i dati veri
DATA_DIR = os.environ.get("CASE_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "data")
LISTINGS_FILE = os.path.join(DATA_DIR, "listings.json")
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")
SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")
# testi e foto in un file a parte: la dashboard mostra subito l'elenco (leggero) e li carica dopo
DETAILS_FILE = os.path.join(DATA_DIR, "details.json")
HEAVY_FIELDS = ("description", "images")

MISSING_RUNS_TO_REMOVE = 2      # giri consecutivi senza vederlo prima di considerarlo "ritirato/venduto"
MIN_RATIO_OK = 0.5              # se una fonte restituisce meno della metà del solito, il giro è sospetto
EMPTY_ACCEPT_RUNS = 12          # una fonte piccola che risulta vuota è sospetta; dopo ~1 giorno e mezzo si accetta
KEEP_REMOVED_DAYS = 120
# qualità dei dati per fonte: quota di annunci con prezzo/superficie/foto. Se crolla rispetto al solito
# il sito è probabilmente cambiato anche se gli annunci si leggono ancora (guasto "silenzioso")
QUALITY_FIELDS = {"price": "prezzo", "mq": "superficie", "images": "foto"}
QUALITY_MIN_RESULTS = 5
KEEP_EVENTS_DAYS = 90
DETAIL_REFRESH_DAYS = 10

# Campi che vengono dall'annuncio (il resto è metadato dell'archivio)
LISTING_FIELDS = ["source", "ref", "url", "title", "price", "mq", "rooms", "bedrooms", "bathrooms", "type", "condition",
                  "town", "zone", "address", "lat", "lon", "floor", "energy", "description", "images", "features",
                  "sold", "private", "agency", "geo"]
TEXT_FIELDS = ("rooms", "bedrooms", "floor")   # possono venire dalla descrizione (clean.fill_from_text)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _dump(path, obj, one_per_line_key=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if one_per_line_key:
            # una riga per annuncio: diff git piccoli e leggibili
            items = obj[one_per_line_key]
            head = {k: v for k, v in obj.items() if k != one_per_line_key}
            f.write("{\n")
            for k, v in head.items():
                f.write(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n")
            f.write(f"{json.dumps(one_per_line_key)}: [\n")
            f.write(",\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in items))
            f.write("\n]}\n")
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _merge_txt(rec: dict, d: dict):
    """Campi ricavati dalla descrizione (clean.fill_from_text): se il giro ha letto il valore dal sito il segno va
    tolto, se l'ha ricavato dal testo va messo, se non l'ha letto affatto resta com'era."""
    txt = set(rec.get("txt") or [])
    for k in TEXT_FIELDS:
        if d.get(k):
            txt.discard(k)
            if k in (d.get("txt") or []):
                txt.add(k)
    if txt:
        rec["txt"] = sorted(txt)
    else:
        rec.pop("txt", None)


class Store:
    def __init__(self):
        raw = _load(LISTINGS_FILE, {"listings": []})
        details = {x["id"]: x for x in _load(DETAILS_FILE, {"details": []}).get("details", [])}
        for x in raw.get("listings", []):
            for k in HEAVY_FIELDS:
                if k in details.get(x["id"], {}):
                    x[k] = details[x["id"]][k]
        self.listings: dict[str, dict] = {x["id"]: clean_record(x) for x in raw.get("listings", [])}
        ev = _load(EVENTS_FILE, {"events": []})
        self.events: list[dict] = ev.get("events", [])
        self.weekly_at: str | None = ev.get("weekly_at")   # ultimo riepilogo settimanale inviato
        self.weekly_stats: dict | None = ev.get("weekly_stats")
        self.sources: dict[str, dict] = _load(SOURCES_FILE, {"sources": {}}).get("sources", {})
        self.new_events: list[dict] = []
        self.ts = now_iso()

    # ---- aiuto per gli adattatori: evitare di riscaricare dettagli già noti ----
    def known(self, lid: str) -> dict | None:
        return self.listings.get(lid)

    def needs_detail(self, lid: str, index_price: int | None = None) -> bool:
        rec = self.listings.get(lid)
        if not rec or not rec.get("detail_at"):
            return True
        if index_price is not None and rec.get("price") != index_price:
            return True
        age = datetime.now(timezone.utc) - datetime.fromisoformat(rec["detail_at"])
        return age > timedelta(days=DETAIL_REFRESH_DAYS)

    def _event(self, etype: str, rec: dict, **extra):
        ev = {"ts": self.ts, "type": etype, "id": rec["id"], "source": rec["source"], **extra}
        self.new_events.append(ev)

    # ---- aggiornamento di una fonte ----
    def apply_source(self, source_id: str, name: str, results: list[dict] | None, error: str | None,
                     detail_ids: set | None = None, stats: dict | None = None):
        meta = self.sources.setdefault(source_id, {"name": name})
        meta["name"] = name
        meta["last_run"] = self.ts
        prev_active = [r for r in self.listings.values() if r["source"] == source_id and r["status"] == "active"]
        bootstrap = not meta.get("last_ok")   # primo giro riuscito: nessuna notifica

        if results is None:
            meta["last_error"] = error
            meta["fail_streak"] = meta.get("fail_streak", 0) + 1
            return
        suspicious = len(prev_active) >= 10 and len(results) < MIN_RATIO_OK * len(prev_active)
        # fonte piccola che all'improvviso risulta vuota: quasi sempre è il sito che non ha risposto bene
        if prev_active and not results and meta.get("fail_streak", 0) < EMPTY_ACCEPT_RUNS:
            suspicious = True
        meta["last_count"] = len(results)
        meta["stats"] = stats or {}
        self._check_quality(meta, results)
        if suspicious:
            meta["last_error"] = f"sospetto: {len(results)} annunci contro {len(prev_active)} del giro precedente"
            meta["fail_streak"] = meta.get("fail_streak", 0) + 1
        else:
            meta["last_ok"] = self.ts
            meta.setdefault("first_ok", self.ts)
            meta["last_error"] = error  # eventuali errori parziali
            meta["fail_streak"] = 0

        seen = set()
        for d in results:
            d = clean_record(d)
            lid = d["id"]
            seen.add(lid)
            rec = self.listings.get(lid)
            if rec is None:
                rec = {"id": lid, "first_seen": self.ts, "price_history": [], "status": "active"}
                rec.update({k: d.get(k) for k in LISTING_FIELDS})
                if d.get("txt"):
                    rec["txt"] = d["txt"]
                if d.get("price"):
                    rec["price_history"].append([self.ts, d["price"]])
                self.listings[lid] = rec
                if not bootstrap:
                    self._event("new", rec, price=d.get("price"))
            else:
                old_price, was_status, was_sold = rec.get("price"), rec.get("status"), rec.get("sold")
                full = lid in (detail_ids or set())
                for k in LISTING_FIELDS:
                    v = d.get(k)
                    # non cancellare dati noti se il giro corrente non li ha letti
                    if v in (None, [], {}, "") and k not in ("sold", "price"):
                        continue
                    # un giro "solo elenco" ha spesso dati ridotti (1 foto, testo tagliato): non peggiorare la scheda
                    if not full and k in ("images", "description") and rec.get(k) and len(v or "") < len(rec[k]):
                        continue
                    if not full and k == "features" and rec.get(k):
                        v = {**rec[k], **v}
                    rec[k] = v
                _merge_txt(rec, d)
                if d.get("price") is None and old_price is not None and lid not in (detail_ids or set()):
                    rec["price"] = old_price  # prezzo non letto in questo giro: mantieni
                new_price = rec.get("price")
                if was_status != "active":
                    rec["status"] = "active"
                    rec.pop("removed_at", None)
                    self._event("back", rec, price=new_price)
                if old_price and new_price and old_price != new_price:
                    rec["price_history"].append([self.ts, new_price])
                    self._event("price_down" if new_price < old_price else "price_up", rec,
                                old_price=old_price, price=new_price)
                elif new_price and not rec["price_history"]:
                    rec["price_history"].append([self.ts, new_price])
                if rec.get("sold") and not was_sold:
                    self._event("sold", rec, price=new_price)
                if d.get("_nogeo"):
                    # la posizione data dalla fonte era la sede dell'agenzia: via quella salvata e la zona che ne derivava
                    rec["lat"] = rec["lon"] = rec["geo"] = None
                    rec["zone"] = d.get("zone")
            # solo la data: aggiornare l'ora a ogni giro riscriverebbe tutto l'archivio a ogni salvataggio
            rec["last_seen"] = self.ts[:10]
            rec["missing_runs"] = 0
            if lid in (detail_ids or set()):
                rec["detail_at"] = self.ts

        if suspicious:
            return
        for rec in prev_active:
            if rec["id"] in seen:
                continue
            rec["missing_runs"] = rec.get("missing_runs", 0) + 1
            if rec["missing_runs"] >= MISSING_RUNS_TO_REMOVE:
                rec["status"] = "removed"
                rec["removed_at"] = self.ts
                self._event("removed", rec, price=rec.get("price"))

    def _check_quality(self, meta: dict, results: list[dict]):
        if len(results) < QUALITY_MIN_RESULTS:
            return
        now = {k: sum(1 for d in results if d.get(k)) / len(results) for k in QUALITY_FIELDS}
        base = meta.get("quality")
        issues = [f"{label} letto per il {round(now[k] * 100)}% degli annunci (di solito {round(base[k] * 100)}%)"
                  for k, label in QUALITY_FIELDS.items()
                  if base and base.get(k, 0) >= 0.5 and now[k] < 0.5 * base[k]]
        if issues:
            meta["quality_issue"] = "; ".join(issues)
            meta["quality_streak"] = meta.get("quality_streak", 0) + 1
            return   # il riferimento "di solito" non si abbassa mentre il problema dura
        meta.pop("quality_issue", None)
        meta["quality_streak"] = 0
        # media mobile: si adatta piano piano ai cambiamenti normali della fonte
        meta["quality"] = {k: round(0.8 * base.get(k, now[k]) + 0.2 * now[k], 3) if base else round(now[k], 3)
                           for k in QUALITY_FIELDS}

    def disable_missing_sources(self, active_ids: set):
        """Fonti tolte dalla configurazione: i loro annunci escono dall'archivio senza generare notifiche."""
        for lid in [k for k, r in self.listings.items() if r["source"] not in active_ids]:
            del self.listings[lid]
        for sid in [s for s in self.sources if s not in active_ids]:
            del self.sources[sid]

    def prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_REMOVED_DAYS)
        for lid in [k for k, r in self.listings.items()
                    if r["status"] == "removed" and datetime.fromisoformat(r["removed_at"]) < cutoff]:
            del self.listings[lid]
        ecut = datetime.now(timezone.utc) - timedelta(days=KEEP_EVENTS_DAYS)
        self.events = [e for e in self.events if datetime.fromisoformat(e["ts"]) >= ecut]

    def save(self):
        self.events.extend(self.new_events)
        self.prune()
        listings = sorted(self.listings.values(), key=lambda r: r["id"])
        light, details = [], []
        for r in listings:
            light.append({**{k: v for k, v in r.items() if k not in HEAVY_FIELDS}, "images": (r.get("images") or [])[:1]})
            if r.get("description") or len(r.get("images") or []) > 1:
                details.append({"id": r["id"], **{k: r.get(k) for k in HEAVY_FIELDS}})
        _dump(LISTINGS_FILE, {"updated": self.ts, "listings": light}, one_per_line_key="listings")
        _dump(DETAILS_FILE, {"updated": self.ts, "details": details}, one_per_line_key="details")
        _dump(EVENTS_FILE, {"updated": self.ts, "weekly_at": self.weekly_at,
                            "weekly_stats": self.weekly_stats, "events": self.events})
        _dump(SOURCES_FILE, {"updated": self.ts, "sources": self.sources})
