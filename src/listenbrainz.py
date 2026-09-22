"""The ListenBrainz side of the plugin: token, library and playlist operations.

MusicBrainz has no user library, so "saved" tracks, albums and artists live on
ListenBrainz: tracks are feedback entries, while albums and artists are stored as items of
two private playlists the plugin maintains. This service owns the credentials, the cached
username and every call that needs the token; the ``user``, ``playlist``, ``browse`` and
``auth`` segments build on it.
"""

from typing import Any, Dict, List, Optional

from musicare_metadata_plugin_sdk import AuthRequiredError

from .credentials import Credentials
from .http import HttpClient
from .providers import LISTENBRAINZ_API

_FEEDBACK_SCORE = {True: 1, False: 0}


class ListenBrainz:
    def __init__(self, client: HttpClient, credentials: Credentials) -> None:
        self._client = client
        self._credentials = credentials
        self._username = ""

    @property
    def token(self) -> str:
        return self._credentials.token

    def require_auth(self) -> None:
        if not self._credentials.is_authenticated:
            raise AuthRequiredError("ListenBrainz token required")

    def _headers(self, required: bool = False) -> Dict[str, str]:
        if required:
            self.require_auth()
        return self._credentials.authorization_header() or {}

    def validate_token(self, token: str) -> Optional[str]:
        """Return the account name for a token, or ``None`` when it is rejected."""
        try:
            data = self._client.get_json(
                f"{LISTENBRAINZ_API}validate-token",
                headers={"Authorization": f"Token {token}"},
            )
        except AuthRequiredError:
            return None
        user_name = data.get("user_name") if isinstance(data, dict) else None
        return str(user_name) if user_name else None

    def username(self) -> str:
        """The authenticated account, or the public ``listenbrainz`` account when anonymous."""
        if self._username:
            return self._username
        if self._credentials.is_authenticated:
            name = self.validate_token(self._credentials.token)
            if name:
                self._username = name
        return self._username or "listenbrainz"

    def me(self) -> Dict[str, Any]:
        if not self._credentials.is_authenticated:
            return {"user_id": None}
        return {"user_id": self.username()}

    # --- playlist primitives -------------------------------------------------------

    def user_playlists(self, username: Optional[str] = None) -> List[Any]:
        data = self._client.get_json(
            f"{LISTENBRAINZ_API}user/{username or self.username()}/playlists",
            headers=self._headers(),
        )
        playlists = data.get("playlists") if isinstance(data, dict) else None
        return playlists if isinstance(playlists, list) else []

    def playlist(self, mbid: str) -> Dict[str, Any]:
        data = self._client.get_json(
            f"{LISTENBRAINZ_API}playlist/{mbid}", headers=self._headers()
        )
        playlist = data.get("playlist") if isinstance(data, dict) else None
        return playlist if isinstance(playlist, dict) else {}

    def get_or_create_playlist(self, title: str) -> str:
        self.require_auth()
        for entry in self.user_playlists():
            playlist = entry.get("playlist") if isinstance(entry, dict) else None
            if isinstance(playlist, dict) and playlist.get("title") == title:
                identifier = playlist.get("identifier")
                if identifier:
                    return str(identifier).rsplit("/", 1)[-1]
        data = self._client.post_json(
            f"{LISTENBRAINZ_API}playlist/create",
            headers=self._headers(required=True),
            body={
                "playlist": {
                    "title": title,
                    "annotation": "Private metadata playlist created by MusicAre",
                    "extension": {
                        "https://musicbrainz.org/doc/jspf#playlist": {"public": False}
                    },
                }
            },
        )
        mbid = data.get("playlist_mbid") if isinstance(data, dict) else None
        if not mbid:
            raise AuthRequiredError("ListenBrainz did not return the created playlist")
        return str(mbid)

    def create_playlist(self, title: str, description: str, public: bool) -> str:
        data = self._client.post_json(
            f"{LISTENBRAINZ_API}playlist/create",
            headers=self._headers(required=True),
            body={
                "playlist": {
                    "title": title,
                    "annotation": description,
                    "extension": {
                        "https://musicbrainz.org/doc/jspf#playlist": {"public": public}
                    },
                }
            },
        )
        mbid = data.get("playlist_mbid") if isinstance(data, dict) else None
        if not mbid:
            raise AuthRequiredError("ListenBrainz did not return the created playlist")
        return str(mbid)

    def edit_playlist(self, mbid: str, title: str, description: str, public: bool) -> None:
        self._client.post_json(
            f"{LISTENBRAINZ_API}playlist/edit/{mbid}",
            headers=self._headers(required=True),
            body={
                "playlist": {
                    "title": title,
                    "annotation": description,
                    "extension": {
                        "https://musicbrainz.org/doc/jspf#playlist": {"public": public}
                    },
                }
            },
        )

    def add_items(self, mbid: str, tracks: List[Dict[str, Any]], position: Optional[int] = None) -> None:
        body: Dict[str, Any] = {"playlist": {"track": tracks}}
        if position is not None:
            body["index"] = position
        self._client.post_json(
            f"{LISTENBRAINZ_API}playlist/{mbid}/item/add",
            headers=self._headers(required=True),
            body=body,
        )

    def delete_items(self, mbid: str, indices: List[int]) -> None:
        for index in indices:
            self._client.post_json(
                f"{LISTENBRAINZ_API}playlist/{mbid}/item/delete",
                headers=self._headers(required=True),
                body={"index": index, "count": 1},
            )

    def copy_playlist(self, mbid: str) -> None:
        self._client.post_json(
            f"{LISTENBRAINZ_API}playlist/{mbid}/copy", headers=self._headers(required=True)
        )

    def delete_playlist(self, mbid: str) -> None:
        self._client.post_json(
            f"{LISTENBRAINZ_API}playlist/{mbid}/delete", headers=self._headers(required=True)
        )

    # --- user data -----------------------------------------------------------------

    def feedback(self, username: str, count: int) -> List[Any]:
        data = self._client.get_json(
            f"{LISTENBRAINZ_API}feedback/user/{username}/get-feedback",
            headers=self._headers(),
            params={"score": 1, "count": count, "offset": 0},
        )
        entries = data.get("feedback") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def submit_feedback(self, recording_mbid: str, liked: bool) -> None:
        self._client.post_json(
            f"{LISTENBRAINZ_API}feedback/recording-feedback",
            headers=self._headers(required=True),
            body={"recording_mbid": recording_mbid, "score": _FEEDBACK_SCORE[liked]},
        )

    def stats_artists(self, username: str, count: int) -> List[Any]:
        data = self._client.get_json(
            f"{LISTENBRAINZ_API}stats/user/{username}/artists",
            headers=self._headers(),
            params={"range": "all_time", "count": count},
        )
        payload = data.get("payload") if isinstance(data, dict) else None
        artists = payload.get("artists") if isinstance(payload, dict) else None
        return artists if isinstance(artists, list) else []

    def created_for(self, username: str) -> List[Any]:
        data = self._client.get_json(
            f"{LISTENBRAINZ_API}user/{username}/playlists/createdfor",
            headers=self._headers(),
        )
        playlists = data.get("playlists") if isinstance(data, dict) else None
        return playlists if isinstance(playlists, list) else []

    def radio(self, prompt: str) -> Dict[str, Any]:
        data = self._client.get_json(
            f"{LISTENBRAINZ_API}explore/lb-radio", params={"prompt": prompt, "mode": "easy"}
        )
        return data if isinstance(data, dict) else {}
