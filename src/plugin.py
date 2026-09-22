"""Aggregate metadata plugin for MusicBrainz, ListenBrainz, Wikidata and Wikimedia.

The class only wires the segments together: each interface has its own module under
``segments/``, and the properties a plugin does not implement are left to the base class,
whose methods raise ``NotImplementedError`` (the runtime maps that to ``unsupported``).
"""

from musicare_metadata_plugin_sdk import (
    BaseMetadataPlugin,
    ICore,
    ISearch,
)

from .credentials import Credentials
from .http import HttpClient
from .images.wikidata import WikidataArtistImages
from .segments.core import MusicBrainzCore
from .segments.search import MusicBrainzSearch

PLUGIN_ID = "org.musicare.metadata.musicbrainz"
PLUGIN_NAME = "MusicBrainz & ListenBrainz"
PLUGIN_VERSION = "1.0.0"


class MusicBrainzPlugin(BaseMetadataPlugin):
    def __init__(self) -> None:
        client = HttpClient()
        self._images = WikidataArtistImages(client)
        self._credentials = Credentials()
        self._core = MusicBrainzCore()
        self._search = MusicBrainzSearch(client, self._images)

    @property
    def id(self) -> str:
        return PLUGIN_ID

    @property
    def name(self) -> str:
        return PLUGIN_NAME

    @property
    def version(self) -> str:
        return PLUGIN_VERSION

    @property
    def core(self) -> ICore:
        return self._core

    @property
    def search(self) -> ISearch:
        return self._search
