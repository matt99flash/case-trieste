"""Comuni della provincia di Trieste e rioni/località, con parole chiave per riconoscerli nel testo."""
import re
import unicodedata

TOWNS = {
    # rioni e frazioni di Trieste inconfondibili (senza omonimi comuni altrove)
    "Trieste": ["trieste", "opicina", "barcola", "roiano", "servola", "valmaura", "basovizza", "gropada", "padriciano",
                "trebiciano", "banne", "contovello", "borgo san sergio", "cattinara", "longera", "conconello", "gretta",
                "chiadino", "rozzol", "melara", "guardiella", "scorcola", "grignano", "miramare", "ponziana",
                "borgo teresiano", "prosecco di trieste"],
    "Muggia": ["muggia", "aquilinia", "santa barbara", "chiampore", "zindis", "noghere", "lazzaretto", "stramare", "rabuiese"],
    "Duino-Aurisina": ["duino", "aurisina", "sistiana", "visogliano", "san giovanni di duino", "santa croce di aurisina",
                       "ceroglie", "malchina", "slivia", "prepotto", "medeazza", "san pelagio", "villaggio del pescatore", "nabrezina", "devin", "sesljan"],
    "San Dorligo della Valle": ["san dorligo", "dolina", "bagnoli della rosandra", "domio", "mattonaia", "caresana", "prebenico",
                                "sant'antonio in bosco", "borgo grotta gigante", "crogole", "moccò"],
    "Sgonico": ["sgonico", "zgonik", "sales", "samatorza", "gabrovizza", "rupingrande", "rupinpiccolo", "borgo grotta gigante"],
    "Monrupino": ["monrupino", "repen", "repentabor", "fernetti", "zolla", "rupinpiccolo"],
}

# Rioni / località del Comune di Trieste -> etichetta mostrata in dashboard.
# Ogni voce: etichetta: [parole chiave]
ZONES_TRIESTE = {
    "Centro / Borgo Teresiano": ["borgo teresiano", "piazza unità", "piazza unita", "piazza della borsa", "corso italia", "via roma",
                                 "canal grande", "ponterosso", "via mazzini", "via carducci", "piazza goldoni", "via dante",
                                 "via san nicolò", "via san nicolo", "via trento", "via valdirivo", "via cassa di risparmio", "centro città", "centro citta"],
    "Borgo Giuseppino / Cavana": ["borgo giuseppino", "cavana", "piazza hortis", "via cavana", "piazza venezia", "via diaz", "rive"],
    "Città Vecchia / San Giusto": ["città vecchia", "citta vecchia", "cittavecchia", "san giusto", "via della cattedrale", "androna", "via capitolina"],
    "Borgo Franceschino / Giulia": ["borgo franceschino", "via giulia", "largo giardino", "giardino pubblico", "via battisti", "viale xx settembre",
                                    "via della ginnastica", "piazza oberdan", "via fabio severo", "via coroneo", "coroneo", "piazza dalmazia", "via carducci"],
    "San Vito / Campi Elisi": ["san vito", "campi elisi", "via tigor", "via navali", "via ottaviano augusto", "passeggio sant'andrea",
                               "via locchi", "viale romolo gessi", "via belpoggio", "via combi", "sant'andrea", "via del monte"],
    "San Giacomo": ["san giacomo", "via dell'istria", "via dell’istria", "via ponziana", "ponziana", "via settefontane", "settefontane",
                    "campo san giacomo", "via san marco", "via molino a vento", "via vespucci", "via baiamonti", "via cumano"],
    "Barriera": ["barriera", "piazza garibaldi", "via oriani", "largo barriera", "via dell'industria", "via caprin", "via giulia alta"],
    "Chiadino / Rozzol": ["chiadino", "rozzol", "via rossetti", "viale d'annunzio", "via revoltella", "piazzale rosmini", "via marchesetti", "via kandler", "melara"],
    "San Luigi / Guardiella": ["san luigi", "guardiella", "via felluga", "via biasoletto", "via tor san piero", "san giovanni", "via san cilino", "via commerciale alta"],
    "Cologna / Scorcola / Gretta": ["cologna", "scorcola", "gretta", "via commerciale", "via bonomea", "via fiammelli", "via romagna", "via udine", "via bazzoni"],
    "Roiano": ["roiano", "via moreri", "via cordaroli", "largo roiano", "via stock", "via tor san lorenzo", "via boveto"],
    "Barcola / Grignano / Miramare": ["barcola", "grignano", "miramare", "viale miramare", "strada costiera", "costiera", "cedas", "bovedo"],
    "Carso triestino (Opicina e frazioni)": ["opicina", "villa opicina", "banne", "trebiciano", "padriciano", "basovizza", "gropada",
                                            "conconello", "ferlugi", "prosecco", "contovello", "santa croce", "campo sacro", "borgo grotta", "piscianzi", "longera", "cattinara alta", "altipiano", "carso"],
    "Servola / Valmaura / Borgo San Sergio": ["servola", "valmaura", "borgo san sergio", "altura", "chiarbola", "santa maria maddalena",
                                             "poggi sant'anna", "poggi paese", "raute", "via flavia", "via svevo", "via dell'istria bassa", "zona industriale", "cattinara"],
    "Montebello / Rotonda del Boschetto": ["montebello", "rotonda del boschetto", "boschetto", "viale al cacciatore", "via giulia bassa", "ospedale maggiore", "via pascoli", "via del ponte"],
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().replace("’", "'"))


_TOWN_KW = [(t, norm(k)) for t, kws in TOWNS.items() for k in kws]
_ZONE_KW = sorted(((z, norm(k)) for z, kws in ZONES_TRIESTE.items() for k in kws), key=lambda x: -len(x[1]))


def detect_town(*texts) -> str | None:
    """Comune della provincia di Trieste citato nel testo (priorità ai comuni minori, poi Trieste)."""
    blob = " " + norm(" ".join(t for t in texts if t)) + " "
    for town, kw in _TOWN_KW:
        if town != "Trieste" and re.search(r"\b" + re.escape(kw.strip()) + r"\b", blob):
            return town
    for town, kw in _TOWN_KW:
        if town == "Trieste" and re.search(r"\b" + re.escape(kw) + r"\b", blob):
            return "Trieste"
    return None


def detect_zone(*texts) -> str | None:
    blob = " " + norm(" ".join(t for t in texts if t)) + " "
    for zone, kw in _ZONE_KW:
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", blob):
            return zone
    return None


def in_province(town: str | None) -> bool:
    return town in TOWNS
