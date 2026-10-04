"""``IUser``: the authenticated profile and the saved library.

ListenBrainz holds the library: liked tracks are feedback entries, while saved albums and
artists are items of two private playlists (``__GYAWUN_ALBUMS__`` / ``__GYAWUN_ARTISTS__``)
that the plugin creates on demand. Those playlists are written with the identifier scheme
of the old plugin so an existing library keeps working.
"""

from typing import Any, Dict, List, Optional

from musicare_metadata_plugin_sdk import (
    Album,
    Artist,
    IUser,
    NotFoundError,
    PaginatedResult,
    Playlist,
    Track,
    UnsupportedError,
    User,
)

from .. import jspf
from ..http import HttpClient
from ..images.wikidata import WikidataArtistImages
from ..listenbrainz import ListenBrainz
from ..mapping import build_album_from_release_group, build_artist, build_track
from ..providers import (
    LISTENBRAINZ_SITE,
    MUSICBRAINZ_API,
    SAVED_ALBUMS_PLAYLIST,
    SAVED_ARTISTS_PLAYLIST,
    SAVED_PLAYLISTS_PLAYLIST,
    external_uri,
)

_RECORDING_INCLUDES = "artist-credits+releases+release-groups+isrcs"
_HIDDEN_PLAYLISTS = (SAVED_ALBUMS_PLAYLIST, SAVED_ARTISTS_PLAYLIST, SAVED_PLAYLISTS_PLAYLIST)


class MusicBrainzUser(IUser):
    def __init__(
        self, lb: ListenBrainz, client: HttpClient, images: WikidataArtistImages
    ) -> None:
        self._lb = lb
        self._client = client
        self._images = images

    @property
    def token(self) -> str:
        return self._lb.token

    def me(self) -> Dict[str, Any]:
        return self._lb.me()

    # --- saved tracks --------------------------------------------------------------

    def saved_tracks(self, offset: int = 0, limit: int = 20) -> PaginatedResult[Track]:
        self._lb.require_auth()
        feedback = self._lb.feedback(self._lb.username(), offset + limit)
        page = feedback[offset : offset + limit]
        mbids: List[str] = []
        hints: Dict[str, str] = {}
        for entry in page:
            if not isinstance(entry, dict) or not entry.get("recording_mbid"):
                continue
            recording_mbid = str(entry["recording_mbid"])
            mbids.append(recording_mbid)
            release = self._feedback_release_mbid(entry)
            if release:
                hints[recording_mbid] = release
        return PaginatedResult(
            items=self._fetch_recordings(mbids, hints),
            total=len(feedback),
            offset=offset,
            limit=limit,
        )

    def _fetch_recordings(
        self, mbids: List[str], hints: Optional[Dict[str, str]] = None
    ) -> List[Track]:
        if not mbids:
            return []
        release_hints = hints or {}
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}recording",
            params={
                "fmt": "json",
                "query": " OR ".join(f"rid:{mbid}" for mbid in mbids),
                "inc": _RECORDING_INCLUDES,
            },
        )
        if not isinstance(data, dict):
            return []
        return [
            build_track(recording, release_hints.get(str(recording.get("id") or "")))
            for recording in data.get("recordings") or []
            if isinstance(recording, dict)
        ]

    # --- saved albums --------------------------------------------------------------

    def saved_albums(self, offset: int = 0, limit: int = 20) -> PaginatedResult[Album]:
        self._lb.require_auth()
        mbid = self._lb.get_or_create_playlist(SAVED_ALBUMS_PLAYLIST)
        tracks = self._playlist_tracks(mbid)
        items: List[Album] = []
        for track in tracks[offset : offset + limit]:
            album_id = self._track_mbid(track)
            if not album_id:
                continue
            data = self._client.get_json(
                f"{MUSICBRAINZ_API}release-group/{album_id}",
                params={"fmt": "json", "inc": "artist-credits"},
            )
            if isinstance(data, dict):
                items.append(build_album_from_release_group(data))
        return PaginatedResult(items=items, total=len(tracks), offset=offset, limit=limit)

    # --- saved artists -------------------------------------------------------------

    def saved_artists(self, offset: int = 0, limit: int = 20) -> PaginatedResult[Artist]:
        self._lb.require_auth()
        mbid = self._lb.get_or_create_playlist(SAVED_ARTISTS_PLAYLIST)
        tracks = self._playlist_tracks(mbid)
        total = len(tracks)
        items: List[Artist] = []
        for track in tracks[offset : offset + limit]:
            artist_mbid = self._track_mbid(track)
            if not artist_mbid:
                continue
            data = self._client.get_json(
                f"{MUSICBRAINZ_API}artist/{artist_mbid}", params={"fmt": "json"}
            )
            if isinstance(data, dict):
                items.append(build_artist(data))
        if not items and offset == 0:
            stats = self._lb.stats_artists(self._lb.username(), offset + limit)
            total = len(stats)
            items = [
                artist
                for entry in stats[offset : offset + limit]
                if isinstance(entry, dict)
                for artist in [self._artist_from_stats(entry)]
                if artist is not None
            ]
        return PaginatedResult(
            items=self._images.enrich(items), total=total, offset=offset, limit=limit
        )

    @staticmethod
    def _artist_from_stats(entry: Dict[str, Any]) -> Optional[Artist]:
        mbid = entry.get("artist_mbid")
        if not mbid:
            return None
        name = entry.get("artist_name") or entry.get("artist_credit_name") or "Unknown Artist"
        return Artist(
            id=str(mbid), name=str(name), external_uri=external_uri("artist", str(mbid))
        )

    # --- saved playlists -----------------------------------------------------------

    def saved_playlist_items(self) -> List[Playlist]:
        """Every playlist in the library, before pagination (search filters over this list).

        The library is the user's own playlists plus the ones saved by reference in the
        hidden ``__GYAWUN_PLAYLISTS__`` playlist. The hidden playlists themselves are
        skipped, a reference whose playlist has since disappeared is dropped without
        failing the whole list, and the references are only expanded for an authenticated
        account (the anonymous case still lists the curated ``listenbrainz`` playlists).
        """
        username = self._lb.username()
        items: List[Playlist] = []
        seen = set()
        for entry in self._lb.user_playlists(username):
            playlist = entry.get("playlist") if isinstance(entry, dict) else None
            if not isinstance(playlist, dict):
                continue
            if playlist.get("title") in _HIDDEN_PLAYLISTS:
                continue
            identifier = playlist.get("identifier")
            if not identifier:
                continue
            playlist_id = str(identifier).rsplit("/", 1)[-1]
            if playlist_id in seen:
                continue
            seen.add(playlist_id)
            items.append(self._playlist_from_raw(playlist_id, playlist, username))
        if self._lb.token:
            items.extend(self._referenced_playlists(seen))
        return items

    def _referenced_playlists(self, seen: set) -> List[Playlist]:
        """Resolve the saved-playlist references, skipping the vanished ones."""
        mbid = self._lb.find_playlist(SAVED_PLAYLISTS_PLAYLIST)
        if not mbid:
            return []
        items: List[Playlist] = []
        for track in self._playlist_tracks(mbid):
            playlist_id = self._track_mbid(track)
            if not playlist_id or playlist_id in seen:
                continue
            seen.add(playlist_id)
            try:
                raw = self._lb.playlist(playlist_id)
            except NotFoundError:
                continue
            if raw:
                items.append(self._playlist_from_raw(playlist_id, raw, ""))
        return items

    @staticmethod
    def _playlist_from_raw(playlist_id: str, raw: Dict[str, Any], default_owner: str) -> Playlist:
        creator = str(raw.get("creator") or default_owner)
        identifier = raw.get("identifier")
        return Playlist(
            id=playlist_id,
            name=str(raw.get("title") or ""),
            description=str(raw.get("annotation") or ""),
            external_uri=str(identifier or f"{LISTENBRAINZ_SITE}playlist/{playlist_id}"),
            owner=User(
                id=creator,
                name=creator,
                external_uri=f"{LISTENBRAINZ_SITE}user/{creator}",
            ),
            images=[],
            is_public=jspf.playlist_is_public(raw),
        )

    def saved_playlists(self, offset: int = 0, limit: int = 20) -> PaginatedResult[Playlist]:
        items = self.saved_playlist_items()
        return PaginatedResult(
            items=items[offset : offset + limit],
            total=len(items),
            offset=offset,
            limit=limit,
        )

    # --- library mutations ---------------------------------------------------------

    def save_track(self, id: str) -> None:
        self._lb.require_auth()
        self._lb.submit_feedback(id, True)

    def unsave_track(self, id: str) -> None:
        self._lb.require_auth()
        self._lb.submit_feedback(id, False)

    def save_album(self, id: str) -> None:
        self._lb.require_auth()
        clean_id = id[3:] if id.startswith("rg:") else id
        playlist_mbid = self._lb.get_or_create_playlist(SAVED_ALBUMS_PLAYLIST)
        # Saving again must not append a second row to the hidden playlist: it *is* the
        # saved set. (Reads do not de-duplicate, so a duplicate here would stay visible.)
        if clean_id in self._playlist_ids(playlist_mbid):
            return
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}release-group/{clean_id}",
            params={"fmt": "json", "inc": "artist-credits"},
        )
        title = str(data.get("title") or "") if isinstance(data, dict) else ""
        self._lb.add_items(
            playlist_mbid,
            [
                {
                    # The old plugin stored album ids under a recording URI; kept so a
                    # library saved before the migration is still matched by unsave.
                    "identifier": external_uri("recording", clean_id),
                    "title": title,
                    "creator": self._first_credit_name(data),
                }
            ],
        )

    def unsave_album(self, id: str) -> None:
        self._lb.require_auth()
        clean_id = id[3:] if id.startswith("rg:") else id
        playlist_mbid = self._lb.get_or_create_playlist(SAVED_ALBUMS_PLAYLIST)
        self._remove_matching(playlist_mbid, clean_id)

    def save_artist(self, id: str) -> None:
        self._lb.require_auth()
        playlist_mbid = self._lb.get_or_create_playlist(SAVED_ARTISTS_PLAYLIST)
        if id in self._playlist_ids(playlist_mbid):
            return
        data = self._client.get_json(f"{MUSICBRAINZ_API}artist/{id}", params={"fmt": "json"})
        title = str(data.get("name") or "") if isinstance(data, dict) else ""
        self._lb.add_items(
            playlist_mbid,
            [{"identifier": external_uri("recording", id), "title": title, "creator": title}],
        )

    def unsave_artist(self, id: str) -> None:
        self._lb.require_auth()
        playlist_mbid = self._lb.get_or_create_playlist(SAVED_ARTISTS_PLAYLIST)
        self._remove_matching(playlist_mbid, id)

    def save_playlist(self, id: str) -> None:
        self._lb.require_auth()
        if id.startswith("radio:"):
            raise UnsupportedError(
                "Synthetic radios are generated on every read and cannot be saved to the library"
            )
        playlist_mbid = self._lb.get_or_create_playlist(SAVED_PLAYLISTS_PLAYLIST)
        # Saving again must not append a second reference: the hidden playlist *is* the
        # saved set, so a duplicate would stay visible.
        if id in self._playlist_ids(playlist_mbid):
            return
        self._lb.add_items(
            playlist_mbid,
            [
                {
                    # ListenBrainz only accepts a recording URI as an item identifier, so
                    # the playlist id travels the same way album/artist ids do.
                    "identifier": external_uri("recording", id),
                    "title": id,
                }
            ],
        )

    def unsave_playlist(self, id: str) -> None:
        self._lb.require_auth()
        playlist_mbid = self._lb.find_playlist(SAVED_PLAYLISTS_PLAYLIST)
        if not playlist_mbid:
            return
        self._remove_matching(playlist_mbid, id)

    # --- helpers -------------------------------------------------------------------

    @staticmethod
    def _feedback_release_mbid(entry: Dict[str, Any]) -> str:
        """The release a feedback entry points at, when ListenBrainz recorded one.

        The app submits feedback by recording MBID only, so ``track_metadata`` is normally
        ``null``; the ListenBrainz web player does store the mapping, and honouring it is
        what keeps the cover of a liked track on the release that was actually played.
        """
        metadata = entry.get("track_metadata")
        if not isinstance(metadata, dict):
            return ""
        mapping = metadata.get("mbid_mapping")
        additional = metadata.get("additional_info")
        candidates: List[Any] = []
        if isinstance(mapping, dict):
            candidates.extend([mapping.get("caa_release_mbid"), mapping.get("release_mbid")])
        if isinstance(additional, dict):
            candidates.extend(
                [additional.get("caa_release_mbid"), additional.get("release_mbid")]
            )
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return ""

    @staticmethod
    def _first_credit_name(data: Any) -> str:
        credits = data.get("artist-credit") if isinstance(data, dict) else None
        if isinstance(credits, list) and credits and isinstance(credits[0], dict):
            artist = credits[0].get("artist")
            if isinstance(artist, dict) and artist.get("name"):
                return str(artist["name"])
        return "Unknown Artist"

    def _remove_matching(self, playlist_mbid: str, needle: str) -> None:
        indices: List[int] = []
        for index, track in enumerate(self._playlist_tracks(playlist_mbid)):
            identifier = jspf.track_identifier(track) if isinstance(track, dict) else None
            if identifier and needle in identifier:
                indices.insert(0, index)
        if indices:
            self._lb.delete_items(playlist_mbid, indices)

    def _playlist_tracks(self, mbid: str) -> List[Any]:
        tracks = self._lb.playlist(mbid).get("track")
        return tracks if isinstance(tracks, list) else []

    def _playlist_ids(self, mbid: str) -> List[str]:
        return [
            entity_id
            for track in self._playlist_tracks(mbid)
            for entity_id in [self._track_mbid(track)]
            if entity_id
        ]

    @staticmethod
    def _track_mbid(track: Any) -> str:
        identifier = jspf.track_identifier(track) if isinstance(track, dict) else None
        return identifier.rsplit("/", 1)[-1] if identifier else ""
