"""Pulizia dei dati: zone canoniche, caratteristiche con nomi uniformi, superfici impossibili scartate.

Si applica sia agli annunci appena letti sia a tutto l'archivio a ogni giro (è idempotente), così le
correzioni valgono anche per gli annunci già salvati.
"""
from .zones import ZONES_TRIESTE, detect_zone, norm

# nomi alternativi usati da alcuni adattatori -> nome standard
FEATURE_ALIASES = {"giardino": "garden", "ascensore": "elevator", "balcone": "terrace", "terrazzo": "terrace",
                   "box": "garage", "vista_mare": "sea_view"}
RESIDENTIAL = {"appartamento", "attico", "casa", "villa"}
PPM_MAX = 15_000      # €/m² oltre cui la superficie letta è certamente sbagliata (es. 8 m² a 155.000 €)
PPM_MIN = 300         # sotto: quasi sempre è la metratura del terreno/giardino, non dell'abitazione
MQ_MIN = 18
# titoli che iniziano così sono box/posti auto anche se il sito li ha classificati come abitazione
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


def clean_record(rec: dict) -> dict:
    feats = rec.get("features")
    if feats:
        for alias, std in FEATURE_ALIASES.items():
            if alias in feats:
                v = feats.pop(alias)
                if feats.get(std) is None or v is True:
                    feats[std] = v
    if rec.get("type") in RESIDENTIAL and norm(rec.get("title") or "").strip().startswith(BOX_STARTS):
        rec["type"] = "box"
    rec["zone"] = canonical_zone(rec)
    mq, price = rec.get("mq"), rec.get("price")
    if mq and rec.get("type") in RESIDENTIAL and not (feats or {}).get("_project"):
        if mq < MQ_MIN or (price and not PPM_MIN <= price / mq <= PPM_MAX):
            rec["mq"] = None
    return rec
