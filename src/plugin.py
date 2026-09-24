"""Aggregate metadata plugin for MusicBrainz, ListenBrainz, Wikidata and Wikimedia.

The class only wires the segments together: each interface has its own module under
``segments/``, and the properties a plugin does not implement are left to the base class,
whose methods raise ``NotImplementedError`` (the runtime maps that to ``unsupported``).
"""

from musicare_metadata_plugin_sdk import (
    BaseMetadataPlugin,
    IAlbum,
    IArtist,
    IAuth,
    IBrowse,
    ICore,
    IPlaylist,
    ISearch,
    ITrack,
    IUser,
)

from .credentials import Credentials
from .http import HttpClient
from .images.wikidata import WikidataArtistImages
from .listenbrainz import ListenBrainz
from .segments.album import MusicBrainzAlbum
from .segments.artist import MusicBrainzArtist
from .segments.auth import MusicBrainzAuth
from .segments.browse import MusicBrainzBrowse
from .segments.core import MusicBrainzCore
from .segments.playlist import MusicBrainzPlaylist
from .segments.search import MusicBrainzSearch
from .segments.track import MusicBrainzTrack
from .segments.user import MusicBrainzUser

PLUGIN_ID = "org.musicare.metadata.musicbrainz"
PLUGIN_NAME = "MusicBrainz & ListenBrainz"
PLUGIN_VERSION = "1.1.0"


class MusicBrainzPlugin(BaseMetadataPlugin):
    def __init__(self) -> None:
        client = HttpClient()
        credentials = Credentials()
        images = WikidataArtistImages(client)
        listenbrainz = ListenBrainz(client, credentials)

        self._credentials = credentials
        self._core = MusicBrainzCore()
        self._user = MusicBrainzUser(listenbrainz, client, images)
        self._search = MusicBrainzSearch(client, images, self._user)
        self._auth = MusicBrainzAuth(listenbrainz, credentials)
        self._album = MusicBrainzAlbum(client, self._user)
        self._artist = MusicBrainzArtist(client, images, self._user)
        self._track = MusicBrainzTrack(client, self._user)
        self._playlist = MusicBrainzPlaylist(listenbrainz, client, self._user)
        self._browse = MusicBrainzBrowse(listenbrainz, self._user)

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

    @property
    def playlist(self) -> IPlaylist:
        return self._playlist

    @property
    def user(self) -> IUser:
        return self._user

    @property
    def auth(self) -> IAuth:
        return self._auth

    @property
    def browse(self) -> IBrowse:
        return self._browse
