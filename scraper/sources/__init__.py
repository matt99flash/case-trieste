"""Registro degli adattatori: il campo `adapter` in config/sources.yaml sceglie la classe."""
from .generic import GenericSource

ADAPTERS = {
    "generic": GenericSource,
}


def register(name):
    def deco(cls):
        ADAPTERS[name] = cls
        return cls
    return deco


def load_all():
    """Importa i moduli degli adattatori specifici (si registrano da soli)."""
    import importlib
    import pkgutil
    import os
    for m in pkgutil.iter_modules([os.path.dirname(__file__)]):
        if m.name not in ("base", "generic"):
            importlib.import_module(f"{__name__}.{m.name}")
    return ADAPTERS
