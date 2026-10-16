"""Prove delle regole di lettura (via, piano, doppioni) con casi reali visti negli annunci.

Uso: python -m unittest discover tests
"""
import unittest

from scraper.clean import clean_address, clean_floor, fill_from_text
from scraper.dedup import assign_groups
from scraper.geocode import enrich, street_of
from scraper.parse_utils import bedrooms_from_text, floor_from_text, floor_level, rooms_from_text
from scraper.zones import infer_trieste


class Via(unittest.TestCase):
    CASI = [
        # (indirizzo, titolo, via attesa)
        ("26, Via Karl Ludwig von Bruck, Campi Elisi, Trieste, Friuli-Venezia Giulia, 34144, Italia", None, "Via Karl Ludwig von Bruck 26"),
        ("Trieste - Via Capodistria,", None, "Via Capodistria"),
        ("via Frescobaldi 25 Trieste Trieste Italy", None, "via Frescobaldi 25"),
        ("Via Carlo e Gian Stuparich 19, Trieste", None, "Via Carlo e Gian Stuparich 19"),
        ("Calle dei Lauri, 8", None, "Calle dei Lauri 8"),
        ("Giacomo Puccini 68", None, "via Giacomo Puccini 68"),
        (":", "Appartamento via Pirano, Trieste", "via Pirano"),
        (None, "NAVALI (via)", "via NAVALI"),
        (None, "Via Matteotti Posto auto coperto", "Via Matteotti"),
        (None, "Via Giulia in stabile d’epoca", "Via Giulia"),
        (None, "ZONA VIALE XX SETTEMBRE | ECLETTICA MANSARDA | € 120.000", "VIALE XX SETTEMBRE"),
        (None, "VIA VITALBA STRADA COSTIERA TRIESTE – Casa Coral Immobiliare", "VIA VITALBA"),
        (None, "Monolocale via Enrico Toti 23, Largo Barriera – Ospedale Maggiore, Trieste", "via Enrico Toti 23"),
        (None, "Via del Campo Marzio 5", "Via del Campo Marzio 5"),
        (None, "Appartamento 3 locali via Rossetti 80 mq", "via Rossetti"),
        (None, "ZONA VIALE | Elegante appartamento", None),
        (None, "BORGO TERESIANO | ELEGANTE MANSARDA", None),   # rione, non via
    ]

    def test_casi(self):
        for addr, title, atteso in self.CASI:
            with self.subTest(addr or title):
                self.assertEqual(street_of(addr, title, None, "Trieste"), atteso)

    def test_descrizione_solo_se_e_la_posizione(self):
        self.assertEqual(street_of(None, "Trilocale", "Appartamento situato in Via Puccini, al primo piano", "Trieste"), "Via Puccini")
        self.assertIsNone(street_of(None, "Trilocale", "A due passi da via Roma, luminoso", "Trieste"))


class Piano(unittest.TestCase):
    def test_livelli(self):
        for testo, atteso in [("2°", 2), ("Secondo piano", 2), ("Piano terra", 0), ("T", 0), ("Ammezzato", 0),
                              ("-1°", -1), ("1 di 4 con ascensore (piano basso)", 1), ("su più livelli", None), (":", None)]:
            with self.subTest(testo):
                self.assertEqual(floor_level(testo), atteso)

    def test_pulizia(self):
        self.assertIsNone(clean_floor("Autonomo, a Radiatori"))
        self.assertEqual(clean_floor("Su due livelli"), "Su due livelli")
        self.assertIsNone(clean_address("Apri su Google Maps"))
        self.assertEqual(clean_address("Valmaura 33"), "Valmaura 33")


class DallaDescrizione(unittest.TestCase):
    def test_piano(self):
        for testo, atteso in [
            ("Appartamento situato al secondo piano con ascensore", "2"),
            ("situata al 5° piano di uno stabile d'epoca", "5"),
            ("al piano terra di una palazzina del 1910", "terra"),
            ("si trova al terzo piano di uno stabile di cinque piani; cantina al piano terra", "3"),
            ("al primo piano la zona notte, al piano terra la zona giorno", None),      # casa su due piani
            ("al secondo piano. Completa la proprietà un garage al piano terra", "2"),
            ("al primo piano con soppalco, collegato da scala interna", None),
            ("al terzo piano con vista. Oppure un'altra al primo piano", None),          # due piani diversi
        ]:
            with self.subTest(testo):
                self.assertEqual(floor_from_text(testo), atteso)

    def test_locali_e_camere(self):
        self.assertEqual(rooms_from_text("Proponiamo un luminoso trilocale con poggiolo", 70), 3)
        self.assertEqual(rooms_from_text("Bilocale con possibilità di ricavare un trilocale", 60), 2)
        self.assertIsNone(rooms_from_text("monolocale", 150))                           # superficie incompatibile
        self.assertIsNone(rooms_from_text("2 locali commerciali al piano terra", 80))
        self.assertEqual(bedrooms_from_text("zona notte composta da due camere matrimoniali"), 2)
        self.assertEqual(bedrooms_from_text("elegante bicamere ristrutturato"), 2)
        self.assertIsNone(bedrooms_from_text("due camere matrimoniali e una singola"))
        self.assertIsNone(bedrooms_from_text("possibilità di ricavare una camera"))

    def test_solo_appartamenti(self):
        villa = {"type": "villa", "description": "al primo piano la zona notte con tre camere"}
        fill_from_text(villa)
        self.assertNotIn("floor", villa)
        app = {"type": "appartamento", "title": "Trilocale", "description": "situato al quarto piano", "rooms": None}
        fill_from_text(app)
        self.assertEqual((app["rooms"], app["floor"], app["txt"]), (3, "4", ["floor", "rooms"]))


def _ann(id_, source, price, mq, **kw):
    return {"id": id_, "source": source, "price": price, "mq": mq, "status": "active", "town": "Trieste",
            "type": "appartamento", "first_seen": "2026-10-01", **kw}


class Doppioni(unittest.TestCase):
    def test_stessa_casa_piano_scritto_diverso(self):
        L = {x["id"]: x for x in [
            _ann("a", "s1", 460000, 97, floor="Secondo piano", zone="San Vito / Campi Elisi"),
            _ann("b", "s2", 470000, 97, floor="2° piano", rooms=3, zone="San Vito / Campi Elisi"),
        ]}
        assign_groups(L)
        self.assertEqual(L["a"]["group"], L["b"]["group"])

    def test_vie_diverse_non_sono_la_stessa_casa(self):
        L = {x["id"]: x for x in [
            _ann("a", "s1", 175000, 80, title="Appartamento via Cividale", zone="Cologna / Scorcola / Gretta"),
            _ann("b", "s2", 175000, 80, title="Appartamento in Via Favetti", zone="Cologna / Scorcola / Gretta"),
        ]}
        assign_groups(L)
        self.assertNotEqual(L["a"]["group"], L["b"]["group"])

    def test_niente_catene_tra_annunci_della_stessa_agenzia(self):
        # due bilocali diversi della stessa agenzia, entrambi simili a un terzo annuncio: non vanno uniti tra loro
        L = {x["id"]: x for x in [
            _ann("a", "tecno", 230000, 48, floor="1", zone="Centro / Borgo Teresiano"),
            _ann("b", "tecno", 240000, 48, floor="2", zone="Centro / Borgo Teresiano"),
            _ann("c", "gallery", 235000, 48, zone="Centro / Borgo Teresiano"),
        ]}
        assign_groups(L)
        self.assertNotEqual(L["a"]["group"], L["b"]["group"])


class Posizione(unittest.TestCase):
    class NoGeo:   # nessuna richiesta in rete durante le prove
        def lookup(self, street, town):
            return None

    def test_punto_sede_agenzia_scartato(self):
        # Trieste Villas: tutti gli annunci con l'indirizzo dell'agenzia, ma i titoli parlano di posti diversi
        titoli = ["Villa Strada Costiera", "Attico a Barcola", "Villa via Buonarroti", "Trilocale a Duino",
                  "Appartamento San Vito", "Casa a Opicina", "Bilocale via Giulia"]
        ann = [{"id": str(i), "town": "Trieste", "lat": 45.64670, "lon": 13.76316, "title": t, "address": "Via Torino 34"}
               for i, t in enumerate(titoli)]
        enrich(ann, self.NoGeo())
        self.assertTrue(all(a["lat"] is None and a.get("_nogeo") for a in ann))

    def test_cantiere_nello_stesso_punto_resta(self):
        ann = [{"id": str(i), "town": "Trieste", "lat": 45.64218, "lon": 13.7654, "title": f"San Vito nuovo complesso {i}"}
               for i in range(8)]
        enrich(ann, self.NoGeo())
        self.assertTrue(all(a["lat"] == 45.64218 for a in ann))


class Qualita(unittest.TestCase):
    def test_crollo_prezzi_segnalato_dopo_riferimento(self):
        from scraper.store import Store
        st = Store.__new__(Store)
        meta = {}
        ok = [{"price": 100000, "mq": 50, "images": ["x"]}] * 10
        st._check_quality(meta, ok)
        self.assertNotIn("quality_issue", meta)
        rotto = [{"price": None, "mq": 50, "images": ["x"]}] * 10
        st._check_quality(meta, rotto)
        st._check_quality(meta, rotto)
        self.assertIn("prezzo", meta["quality_issue"])
        self.assertEqual(meta["quality_streak"], 2)
        self.assertEqual(meta["quality"]["price"], 1.0)   # il riferimento non si abbassa
        st._check_quality(meta, ok)
        self.assertNotIn("quality_issue", meta)


class Comune(unittest.TestCase):
    def test_in_grado_non_e_grado(self):
        self.assertTrue(infer_trieste("Appartamento (TS)", None, None, "in grado di soddisfare"))


if __name__ == "__main__":
    unittest.main()
