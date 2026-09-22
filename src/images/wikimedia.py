"""Wikimedia Commons image URLs.

A Wikidata ``P18`` value is a raw Commons file name; this module turns it into the four
standard sizes (56 / 250 / 500 / 1000) used by the app and the old plugin alike.
"""

import urllib.parse
from typing import List

from musicare_metadata_plugin_sdk import Image

WIDTHS = (56, 250, 500, 1000)
_PREFIX = "https://commons.wikimedia.org/wiki/Special:FilePath/"


def _build(encoded_name: str) -> List[Image]:
    if not encoded_name:
        return []
    return [
        Image(url=f"{_PREFIX}{encoded_name}?width={width}", width=width, height=width)
        for width in WIDTHS
    ]


def from_file_name(file_name: str) -> List[Image]:
    """Build the four sizes from a raw Wikidata ``P18`` file name."""
    if not file_name:
        return []
    return _build(urllib.parse.quote(file_name, safe=""))
