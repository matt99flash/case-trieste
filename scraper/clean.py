"""Pulizia dei dati: zone canoniche, caratteristiche con nomi uniformi, superfici impossibili scartate.

Si applica sia agli annunci appena letti sia a tutto l'archivio a ogni giro (è idempotente), così le
correzioni valgono anche per gli annunci già salvati.
"""
import math
import re

from .parse_utils import bedrooms_from_text, floor_from_text, floor_level, rooms_from_text
from .zones import ZONES_TRIESTE, detect_zone, norm, title_town

# nomi alternativi usati da alcuni adattatori -> nome standard
FEATURE_ALIASES = {"giardino": "garden", "ascensore": "elevator", "balcone": "terrace", "terrazzo": "terrace",
                   "box": "garage", "vista_mare": "sea_view"}
RESIDENTIAL = {"appartamento", "attico", "casa", "villa"}
FLAT = {"appartamento", "attico"}
PPM_MAX = 15_000      # €/m² oltre cui la superficie letta è certamente sbagliata (es. 8 m² a 155.000 €)
PPM_MIN = 300         # sotto: quasi sempre è la metratura del terreno/giardino, non dell'abitazione
MQ_MIN = 18
# titoli che iniziano così sono box/posti auto anche se il sito li ha classificati come abitazione
# testi di pagina finiti per errore nel campo indirizzo
JUNK_ADDRESS = {"descrizione", "dettagli", "apri su google maps", "address", "indirizzo", "piani", "calcolatore di rata",
                "caratteristiche", "superficie", "mappa", "posizione", "zona", "localita", "dove si trova"}
BOX_STARTS = ("box", "garage", "posto auto", "posti auto", "posto moto", "posti moto", "autorimessa", "posto macchina")


def canonical_zone(rec: dict) -> str | None:
    zone = rec.get("zone")
    if rec.get("town") != "Trieste" or zone in ZONES_TRIESTE:
        return zone
    if zone:
        found = detect_zone(zone)
        if found:
            return found
        if norm(zone).strip().startswith("centro"):
            return "Centro / Borgo Teresiano"
    # valore non riconosciuto ("Ascensore", "Zona", un indirizzo...): cerca nel resto dell'annuncio
    return detect_zone(rec.get("address"), rec.get("title")) or detect_zone(rec.get("description"))


def clean_address(a):
    if not a:
        return None
    t = norm(a).strip(" :.,-")
    if not re.search(r"[a-z]", t) or t in JUNK_ADDRESS:
        return None
    return a


def clean_floor(f):
    """Tiene il piano solo se è davvero un piano ("2°", "terra") o "su più livelli"; scarta ":" e testi di pagina."""
    if f is None or floor_level(f) is not None or "livell" in norm(str(f)):
        return f
    return None


def fill_from_text(rec: dict):
    """Locali, camere e piano mancanti presi dalla descrizione (solo appartamenti/attici). I campi ricavati così sono
    elencati in rec["txt"], per poterli distinguere (e togliere) se una regola si rivelasse sbagliata."""
    if rec.get("type") not in FLAT or (rec.get("features") or {}).get("_project"):
        return
    txt = set(rec.get("txt") or [])
    text = " ".join(x for x in (rec.get("title"), rec.get("description")) if x)
    if not rec.get("rooms"):
        rec["rooms"] = rooms_from_text(text, rec.get("mq"))
        if rec["rooms"]:
            txt.add("rooms")
    if not rec.get("bedrooms"):
        rec["bedrooms"] = bedrooms_from_text(text)
        if rec["bedrooms"]:
            txt.add("bedrooms")
    if not rec.get("floor"):
        rec["floor"] = floor_from_text(rec.get("description"))
        if rec["floor"]:
            txt.add("floor")
    if txt:
        rec["txt"] = sorted(txt)


TRIESTE_CENTER = (45.6495, 13.7768)


def fix_town(rec: dict):
    """Il sito dice "Trieste" ma il titolo nomina un altro comune della provincia ("Casetta Frazione Sistiana",
    "Villa a Gabrovizza"): vale il titolo. La posizione, se è in città, era quella dell'agenzia o di una via omonima."""
    other = title_town(rec.get("title"))
    if rec.get("town") != "Trieste" or not other or other == "Trieste":
        return
    rec["town"], rec["zone"] = other, None
    if rec.get("lat") is not None and math.hypot((rec["lat"] - TRIESTE_CENTER[0]) * 111, (rec["lon"] - TRIESTE_CENTER[1]) * 78) < 4:
        rec["lat"] = rec["lon"] = rec["geo"] = None
        rec["_nogeo"] = True


def clean_record(rec: dict) -> dict:
    rec["address"] = clean_address(rec.get("address"))
    rec["floor"] = clean_floor(rec.get("floor"))
    feats = rec.get("features")
    if feats:
        for alias, std in FEATURE_ALIASES.items():
            if alias in feats:
                v = feats.pop(alias)
                if feats.get(std) is None or v is True:
                    feats[std] = v
    if rec.get("type") in RESIDENTIAL and norm(rec.get("title") or "").strip().startswith(BOX_STARTS):
        rec["type"] = "box"
    fill_from_text(rec)
    fix_town(rec)
    rec["zone"] = canonical_zone(rec)
    mq, price = rec.get("mq"), rec.get("price")
    if mq and rec.get("type") in RESIDENTIAL and not (feats or {}).get("_project"):
        if mq < MQ_MIN or (price and not PPM_MIN <= price / mq <= PPM_MAX):
            rec["mq"] = None
    return rec
