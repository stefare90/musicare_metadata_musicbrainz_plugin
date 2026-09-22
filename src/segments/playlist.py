"""``IPlaylist``: ListenBrainz playlists, including the synthetic radio playlists.

Two playlist ids are not real ListenBrainz documents but radios the browse sections
produce: ``radio:artist:<name>`` and ``radio:tag:<tag>``. They are materialised here from
the ``lb-radio`` endpoint, so ``get_playlist`` and ``tracks`` treat them like any other.
"""

from typing import Any, Dict, List, Optional

from musicare_metadata_plugin_sdk import (
    IPlaylist,
    Image,
    NotFoundError,
    PaginatedResult,
    Playlist,
    Track,
    User,
)

from .. import jspf
from ..http import HttpClient
from ..listenbrainz import ListenBrainz
from ..providers import (
    LISTENBRAINZ_SITE,
    MOOD_PLAYLISTS,
    MUSICBRAINZ_API,
    cover_url,
    external_uri,
)

_JSPF_PLAYLIST_EXTENSION = "https://musicbrainz.org/doc/jspf#playlist"
_MOOD_TITLES = dict(MOOD_PLAYLISTS)
LISTENBRAINZ_OWNER = User(
    id="listenbrainz", name="ListenBrainz", external_uri="https://listenbrainz.org"
)


class MusicBrainzPlaylist(IPlaylist):
    def __init__(self, lb: ListenBrainz, client: HttpClient, user: Any) -> None:
        self._lb = lb
        self._client = client
        self._user = user

    # --- reads ---------------------------------------------------------------------

    def _raw_playlist(self, id: str) -> Dict[str, Any]:
        if id.startswith("radio:"):
            return self._radio_metadata(id)
        return self._lb.playlist(id)

    def get_playlist(self, id: str) -> Playlist:
        raw = self._raw_playlist(id)
        if not raw:
            raise NotFoundError(f"Playlist not found: {id}")
        creator = str(raw.get("creator") or "Unknown")
        return Playlist(
            id=id,
            name=str(raw.get("title") or "Untitled Playlist"),
            description=str(raw.get("annotation") or ""),
            external_uri=f"{LISTENBRAINZ_SITE}playlist/{id}",
            owner=User(
                id=creator,
                name=creator,
                external_uri=f"{LISTENBRAINZ_SITE}user/{creator}",
            ),
            images=self._cover_art(raw),
            is_public=self._is_public(raw),
        )

    def tracks(self, id: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Track]:
        if id.startswith("radio:"):
            return self._radio_tracks(id, offset, limit)
        raw = self._lb.playlist(id)
        entries = raw.get("track")
        items: List[Track] = []
        if isinstance(entries, list):
            for entry in entries[offset : offset + limit]:
                if isinstance(entry, dict):
                    track = jspf.build_track(entry)
                    if track is not None:
                        items.append(track)
        total = len(entries) if isinstance(entries, list) else 0
        return PaginatedResult(items=items, total=total, offset=offset, limit=limit)

    # --- writes --------------------------------------------------------------------

    def create_playlist(
        self,
        user_id: str,
        name: str,
        description: Optional[str] = None,
        public: Optional[bool] = None,
        collaborative: Optional[bool] = None,
    ) -> Optional[Playlist]:
        mbid = self._lb.create_playlist(
            name, description or "Created by MusicAre Plugin", bool(public)
        )
        return Playlist(
            id=mbid,
            name=name,
            description=description or "",
            external_uri=f"{LISTENBRAINZ_SITE}playlist/{mbid}",
            owner=User(
                id=user_id,
                name=user_id,
                external_uri=f"{LISTENBRAINZ_SITE}user/{user_id}",
            ),
            images=[],
            collaborative=bool(collaborative),
            is_public=bool(public),
        )

    def update_playlist(
        self,
        playlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        public: Optional[bool] = None,
        collaborative: Optional[bool] = None,
    ) -> None:
        raw = self._raw_playlist(playlist_id)
        if not raw:
            return
        final_name = name if name is not None else str(raw.get("title") or "Untitled Playlist")
        final_description = (
            description if description is not None else str(raw.get("annotation") or "")
        )
        final_public = public if public is not None else self._is_public(raw)
        self._lb.edit_playlist(playlist_id, final_name, final_description, final_public)

    def delete_playlist(self, playlist_id: str) -> None:
        self._user.unsave_playlist(playlist_id)

    def add_tracks(
        self, playlist_id: str, track_ids: List[str], position: Optional[int] = None
    ) -> None:
        jspf_tracks = [self._jspf_track(track_id) for track_id in track_ids]
        self._lb.add_items(playlist_id, [track for track in jspf_tracks if track], position)

    def remove_tracks(self, playlist_id: str, track_ids: List[str]) -> None:
        raw = self._lb.playlist(playlist_id)
        entries = raw.get("track")
        if not isinstance(entries, list):
            return
        indices: List[int] = []
        for index, entry in enumerate(entries):
            identifier = jspf.track_identifier(entry) if isinstance(entry, dict) else None
            if identifier and identifier.rsplit("/", 1)[-1] in track_ids:
                indices.insert(0, index)
        if indices:
            self._lb.delete_items(playlist_id, indices)

    def save(self, playlist_id: str) -> None:
        self._user.save_playlist(playlist_id)

    def unsave(self, playlist_id: str) -> None:
        self._user.unsave_playlist(playlist_id)

    # --- radio ---------------------------------------------------------------------

    @staticmethod
    def _radio_metadata(id: str) -> Dict[str, Any]:
        title, description = "Radio", "Algorithmic recommendation radio"
        if id.startswith("radio:artist:"):
            artist_name = id[len("radio:artist:") :]
            title = f"{artist_name} Radio"
            description = f"Algorithmic recommendation radio based on {artist_name}"
        elif id.startswith("radio:tag:"):
            tag = id[len("radio:tag:") :]
            title = _MOOD_TITLES.get(tag, "Mood")
            description = f"Algorithmic recommendations generated based on the {tag} mood"
        return {
            "title": title,
            "annotation": description,
            "creator": "listenbrainz",
            "identifier": f"{LISTENBRAINZ_SITE}playlist/{id}",
            "track": [],
            "extension": {_JSPF_PLAYLIST_EXTENSION: {"public": False}},
        }

    def _radio_tracks(
        self, id: str, offset: int, limit: int
    ) -> PaginatedResult[Track]:
        if id.startswith("radio:artist:"):
            prompt = f"artist:({id[len('radio:artist:'):]})"
        elif id.startswith("radio:tag:"):
            prompt = f"tag:({id[len('radio:tag:'):]})"
        else:
            prompt = ""
        data = self._lb.radio(prompt)
        playlist = ((data.get("payload") or {}).get("jspf") or {}).get("playlist")
        entries = playlist.get("track") if isinstance(playlist, dict) else None
        items: List[Track] = []
        if isinstance(entries, list):
            for entry in entries[offset : offset + limit]:
                if isinstance(entry, dict):
                    track = jspf.build_track(entry)
                    if track is not None:
                        items.append(track)
        total = len(entries) if isinstance(entries, list) else 0
        return PaginatedResult(items=items, total=total, offset=offset, limit=limit)

    # --- helpers -------------------------------------------------------------------

    def _jspf_track(self, track_id: str) -> Dict[str, Any]:
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}recording/{track_id}",
            params={"fmt": "json", "inc": "artist-credits"},
        )
        title = str(data.get("title") or "Unknown Track") if isinstance(data, dict) else "Unknown Track"
        artist_name = "Unknown Artist"
        credits = data.get("artist-credit") if isinstance(data, dict) else None
        if isinstance(credits, list) and credits and isinstance(credits[0], dict):
            artist = credits[0].get("artist")
            if isinstance(artist, dict) and artist.get("name"):
                artist_name = str(artist["name"])
        return {
            "identifier": external_uri("recording", track_id),
            "title": title,
            "creator": artist_name,
        }

    @staticmethod
    def _is_public(raw: Dict[str, Any]) -> bool:
        extension = raw.get("extension")
        playlist_extension = (
            extension.get(_JSPF_PLAYLIST_EXTENSION) if isinstance(extension, dict) else None
        )
        if not isinstance(playlist_extension, dict):
            return False
        value = playlist_extension.get("public")
        return value is True or str(value) == "true"

    @staticmethod
    def _cover_art(raw: Dict[str, Any]) -> List[Image]:
        images: List[Image] = []
        entries = raw.get("track")
        if not isinstance(entries, list):
            return images
        for entry in entries:
            if len(images) >= 4:
                break
            if not isinstance(entry, dict):
                continue
            album_mbid = jspf.extract_album_mbid(entry)
            if not album_mbid:
                continue
            is_release_group = album_mbid.startswith("rg:")
            clean = album_mbid[3:] if is_release_group else album_mbid
            images.append(
                Image(
                    url=cover_url(
                        "release-group" if is_release_group else "release", clean, 250
                    ),
                    width=250,
                    height=250,
                )
            )
        return images
