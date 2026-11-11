"""Trusted, prepared inline SVGs. Artwork has no scripts, styles, IDs or links."""
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup

POSES = frozenset(("default", "celebrate", "question", "suggestion", "reminder", "award"))


@lru_cache(maxsize=6)
def artwork(pose):
    if pose not in POSES:
        raise ValueError("Unknown mascot pose")
    return Markup((Path(__file__).parent / "static/mascot/animated" / f"{pose}.svg").read_text(encoding="utf-8"))
