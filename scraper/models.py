from dataclasses import dataclass, field, asdict

from . import parse_utils as pu
from .zones import detect_town, detect_zone


@dataclass
class Listing:
    source: str                 # id della fonte (config/sources.yaml)
    ref: str                    # codice univoco dell'annuncio presso la fonte
    url: str
    title: str | None = None
    price: int | None = None
    mq: int | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    type: str | None = None
    condition: str | None = None
    town: str | None = None
    zone: str | None = None
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    floor: str | None = None
    energy: str | None = None
    description: str | None = None
    images: list = field(default_factory=list)
    features: dict = field(default_factory=dict)
    sold: bool = False          # la fonte stessa segnala "venduto"/"sotto offerta"
    private: bool = False       # venditore privato (Subito)
    agency: str | None = None   # nome dell'agenzia/ufficio, se la fonte ne raggruppa più d'uno
    geo: str | None = None      # origine della posizione: "fonte" o "indirizzo"

    @property
    def id(self) -> str:
        return f"{self.source}:{self.ref}"

    def finalize(self, default_town: str | None = None) -> "Listing":
        """Completa i campi mancanti deducendoli da titolo/descrizione."""
        text = " ".join(x for x in (self.title, self.address, self.zone, self.description) if x)
        if not self.town:
            self.town = detect_town(self.address, self.title, self.zone) or detect_town(self.description) or default_town
        if self.town == "Trieste" and not self.zone:
            self.zone = detect_zone(self.address, self.title) or detect_zone(self.description)
        elif self.town == "Trieste" and self.zone:
            self.zone = detect_zone(self.zone) or detect_zone(self.address, self.title) or self.zone
        if not self.type or self.type == "altro":
            self.type = pu.detect_type(self.title, self.description)
            # titolo con la sola via ("Via Fabio Severo") ma con camere/bagni: è un'abitazione
            if self.type == "altro" and (self.rooms or self.bedrooms or self.bathrooms):
                self.type = "appartamento"
        if not self.condition:
            self.condition = pu.detect_condition(self.title, self.description)
        if not self.rooms:
            self.rooms = pu.parse_rooms(self.title)
        if not self.mq:
            self.mq = pu.parse_mq(self.title)
        if not self.energy:
            self.energy = pu.detect_energy(self.description)
        feats = pu.detect_features(text)
        for k, v in feats.items():
            if self.features.get(k) is None and v is not None:
                self.features[k] = v
        self.description = pu.clean_text(self.description)
        self.title = pu.clean_text(self.title, 200)
        seen, imgs = set(), []
        for u in self.images:
            if u and u.startswith("http") and u not in seen:
                seen.add(u)
                imgs.append(u)
        self.images = imgs[:12]
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d
