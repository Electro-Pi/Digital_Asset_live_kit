"""Registry of the seven guideline sources approved for the PoC."""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource
from src.ingestion.sources import aafp, aap, acc_aha, acog, cdc, nice, uspstf

_SOURCES: dict[str, GuidelineSource] = {
    module.SOURCE.name: module.SOURCE
    for module in (uspstf, cdc, nice, acc_aha, aafp, acog, aap)
}


def names() -> list[str]:
    return list(_SOURCES)


def get(name: str) -> GuidelineSource | None:
    return _SOURCES.get(name)


def all_sources() -> list[GuidelineSource]:
    return list(_SOURCES.values())


__all__ = ["all_sources", "get", "names"]
