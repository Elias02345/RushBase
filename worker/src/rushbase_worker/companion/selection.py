"""The local selection basket (``selection.json`` in the data directory)."""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from rushbase_worker.companion.clips import Clip
from rushbase_worker.config import WorkerSettings
from rushbase_worker.state import write_private

SELECTION_FILE = "selection.json"


class Basket(BaseModel):
    """Ordered list of clips (one entry per asset)."""

    version: int = 1
    items: list[Clip] = Field(default_factory=list)


def basket_path(settings: WorkerSettings) -> Path:
    """Location of the basket file."""
    return settings.data_dir / SELECTION_FILE


def load_basket(settings: WorkerSettings) -> Basket:
    """The stored basket (empty when missing or unreadable)."""
    try:
        return Basket.model_validate_json(basket_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Basket()


def save_basket(settings: WorkerSettings, basket: Basket) -> None:
    """Persist the basket atomically."""
    write_private(basket_path(settings), json.dumps(basket.model_dump(mode="json"), indent=2) + "\n")


def add_clip(basket: Basket, clip: Clip) -> Basket:
    """Add ``clip``; an existing entry of the same asset gets the union of selected shots."""
    for index, existing in enumerate(basket.items):
        if existing.asset_id != clip.asset_id:
            continue
        if existing.selected is None or clip.selected is None:
            merged = None
        else:
            merged = sorted({*existing.selected, *clip.selected})
        basket.items[index] = clip.model_copy(update={"selected": merged})
        return basket
    basket.items.append(clip)
    return basket


def remove_clip(basket: Basket, asset_id: str, shots: list[int] | None = None) -> bool:
    """Remove an asset (or only some of its selected shots); ``True`` if something changed."""
    for index, existing in enumerate(basket.items):
        if existing.asset_id != asset_id:
            continue
        if shots and existing.selected is not None:
            remaining = [s for s in existing.selected if s not in shots]
            if remaining:
                basket.items[index] = existing.model_copy(update={"selected": remaining})
                return True
        del basket.items[index]
        return True
    return False
