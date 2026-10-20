"""Turn file bytes into text the model can use. One module per format family; all share `Extraction`."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Extraction:
    text: str
    extractor: str
    unit: str = "line"  # what citations refer to: line, page, sheet, section
    units_total: int | None = None
    unit_range: tuple[int, int] | None = None  # which units are included, 1-based inclusive
    meta: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    continue_hint: str | None = None


class ExtractError(Exception):
    """The file is the right type but cannot be read (corrupt, encrypted, too complex). Message is model-safe."""
