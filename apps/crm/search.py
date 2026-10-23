"""Accent- and case-insensitive search helpers (CRM-01.6, 01.7)."""

import unicodedata

# Greek-to-Latin map for Greeklish matching (CRM-01.7). Digraphs first.
_GREEKLISH_DIGRAPHS = {
    "ου": "ou", "αι": "ai", "ει": "ei", "οι": "oi", "μπ": "b", "ντ": "d",
    "γκ": "g", "γγ": "ng", "τσ": "ts", "τζ": "tz",
}
_GREEKLISH = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y",
    "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
}


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def normalise(text: str) -> str:
    """Lower-case, accent-free, single-spaced."""
    return " ".join(strip_accents(text or "").lower().split())


def to_greeklish(text: str) -> str:
    """Transliterate normalised Greek to Latin for Greeklish matching."""
    out = normalise(text)
    for gr, lat in _GREEKLISH_DIGRAPHS.items():
        out = out.replace(gr, lat)
    return "".join(_GREEKLISH.get(c, c) for c in out)
