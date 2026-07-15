"""Typed internal model. Everything downstream of loading sees these objects,
never raw YAML dicts — so a change to on-disk format touches only the loader.
(This is the "don't feed raw YAML to projections" decision, minus an AST layer
the single flat format doesn't need.)"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TimeSpec:
    """An EDTF-subset instant/range. earliest/latest are integer years
    (negative = BC, astronomical numbering). Either bound may be None (unknown)."""
    raw: str
    earliest: int | None
    latest: int | None
    uncertain: bool

    def key(self) -> float:
        """Sort/representative year: earliest bound, then latest, then very old."""
        if self.earliest is not None:
            return self.earliest
        if self.latest is not None:
            return self.latest
        return float("-inf")


def parse_edtf(s: str) -> TimeSpec:
    """Parse the EDTF (ISO 8601-2) subset the data actually uses:
    single year "-2166", uncertain "-2166?", interval "-2100/-2050", "unknown".
    ponytail: unspecified-digit forms ("-20XX") and seasons raise NotImplemented
    rather than guess — add them when real data needs them."""
    raw = s
    s = s.strip()
    if s in ("", "unknown", "..", "/"):
        return TimeSpec(raw, None, None, True)
    if "/" in s:  # interval A/B (open ends allowed)
        a, b = s.split("/", 1)
        lo = parse_edtf(a) if a else TimeSpec(raw, None, None, True)
        hi = parse_edtf(b) if b else TimeSpec(raw, None, None, True)
        return TimeSpec(raw, lo.earliest, hi.latest, lo.uncertain or hi.uncertain)
    uncertain = s.endswith("?") or s.endswith("~")
    core = s.rstrip("?~")
    if "X" in core.upper():
        raise NotImplementedError(f"EDTF unspecified-digit form not supported yet: {raw!r}")
    year = int(core)
    return TimeSpec(raw, year, year, uncertain)


@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    subtype: str | None
    geometry: str | None
    immutable: dict[str, Any]
    sources: tuple[str, ...] = ()


@dataclass
class Event:
    id: str
    type: str
    time_raw: dict[str, Any]      # {edtf: ...} or {variants: [...]}
    sources: list[str]
    confidence: str
    models: list[str] | None      # None = all models
    requires: list[str]           # explicit dependency edges (escape hatch)
    payload: dict[str, Any]       # type-specific fields (person, from, to, ...)

    def time_for(self, model: str) -> TimeSpec:
        """Resolve this event's time under a chosen historical model."""
        t = self.time_raw
        if "edtf" in t:
            return parse_edtf(t["edtf"])
        variants = t["variants"]
        for v in variants:
            if v["model"] == model:
                return parse_edtf(v["edtf"])
        return parse_edtf(variants[0]["edtf"])  # model not listed → first variant

    def exists_in(self, model: str) -> bool:
        return self.models is None or model in self.models
