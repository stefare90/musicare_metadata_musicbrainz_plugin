"""Wikimedia Commons image URLs.

Both image discovery paths funnel through here so the generated URLs stay identical:
a raw Wikidata ``P18`` file name and a SPARQL ``Special:FilePath`` URI produce the same
four sizes (56 / 250 / 500 / 1000).
"""

import urllib.parse
from typing import List

from musicare_metadata_plugin_sdk import Image

WIDTHS = (56, 250, 500, 1000)
_PREFIX = "https://commons.wikimedia.org/wiki/Special:FilePath/"
_MARKER = "Special:FilePath/"


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


def from_image_uri(uri: str) -> List[Image]:
    """Build the four sizes from a SPARQL ``Special:FilePath`` URI.

    The file name is already percent-encoded by Wikidata, so it is used as-is (encoding it
    again would double-escape the name).
    """
    if not uri:
        return []
    working = uri
    if working.startswith("http://"):
        working = "https://" + working[len("http://"):]
    index = working.find(_MARKER)
    if index < 0:
        return []
    encoded_name = working[index + len(_MARKER):]
    query_index = encoded_name.find("?")
    if query_index >= 0:
        encoded_name = encoded_name[:query_index]
    return _build(encoded_name)
