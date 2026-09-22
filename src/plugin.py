"""Aggregate metadata plugin for MusicBrainz, ListenBrainz, Wikidata and Wikimedia.

The class only wires the segments together: each interface has its own module under
``segments/``, and the properties a plugin does not implement are left to the base class,
whose methods raise ``NotImplementedError`` (the runtime maps that to ``unsupported``).
"""

from musicare_metadata_plugin_sdk import (
    BaseMetadataPlugin,
    IAlbum,
    IArtist,
    ICore,
    ISearch,
    ITrack,
)

from .credentials import Credentials
from .http import HttpClient
from .images.wikidata import WikidataArtistImages
from .segments.album import MusicBrainzAlbum
from .segments.artist import MusicBrainzArtist
from .segments.core import MusicBrainzCore
from .segments.search import MusicBrainzSearch
from .segments.track import MusicBrainzTrack

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
        self._album = MusicBrainzAlbum(client)
        self._artist = MusicBrainzArtist(client, self._images)
        self._track = MusicBrainzTrack(client)

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

    @property
    def album(self) -> IAlbum:
        return self._album

    @property
    def artist(self) -> IArtist:
        return self._artist

    @property
    def track(self) -> ITrack:
        return self._track
