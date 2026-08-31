"""The attribution ODbL requires, in one place.

ODbL obliges anyone publicly using an ODbL database to keep the attribution
visible. Defining the wording once means it cannot drift between the app, the
API and the exported dataset.
"""
from __future__ import annotations

ATTRIBUTION_TEXT = (
    "Contains information from Open Food Facts, made available under the "
    "Open Database License (ODbL)"
)
LICENSE_NAME = "Open Database License (ODbL) v1.0"
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
SOURCE_URL = "https://world.openfoodfacts.org/"


def attribution() -> dict[str, str]:
    """The block every surface showing Open Food Facts data must render."""
    return {
        "text": ATTRIBUTION_TEXT,
        "license": LICENSE_NAME,
        "license_url": LICENSE_URL,
        "source_url": SOURCE_URL,
    }
