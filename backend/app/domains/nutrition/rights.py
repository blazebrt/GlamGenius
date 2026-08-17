"""Deterministic governance gate for food-composition imports."""
from __future__ import annotations

from app.domains.nutrition.models import FoodCompositionDataset


class FoodCompositionImportNotAllowed(Exception):
    """Raised when a dataset has not cleared the explicit import gate."""


def composition_import_allowed(dataset: FoodCompositionDataset) -> bool:
    """Return whether the dataset is explicitly authorized for import."""
    return (
        dataset.rights_status in {"permission_granted", "open_licensed"}
        and dataset.import_status in {"ready_for_import", "imported"}
    )


def assert_composition_import_allowed(dataset: FoodCompositionDataset) -> None:
    """Fail closed unless both rights and lifecycle status are permitted."""
    if not composition_import_allowed(dataset):
        raise FoodCompositionImportNotAllowed(
            f"food-composition import is not allowed for {dataset.dataset_key!r}"
        )
