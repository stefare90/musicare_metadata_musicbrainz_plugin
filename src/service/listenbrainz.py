"""The ListenBrainz side of the plugin: token, library and playlist operations.

MusicBrainz has no user library, so "saved" tracks, albums and artists live on
ListenBrainz: tracks are feedback entries, while albums and artists are stored as items of
two private playlists the plugin maintains. This service owns the credentials, the cached
username and every call that needs the token; the ``user``, ``playlist``, ``browse`` and
``auth`` segments build on it.
"""

from typing import Any, Dict, List, Optional

import json
import time

from musicare_metadata_plugin_sdk import AuthRequiredError, NotFoundError

from .credentials import Credentials
from ..net import HttpClient, HttpAuthError, HttpStatusError
from ..shared.providers import LISTENBRAINZ_API

_FEEDBACK_SCORE = {True: 1, False: 0}

# A seed with no listens makes `lb-radio` answer 400: the provider has no radio to
# build, which is a fact about the seed, not a failure of the call.
_EMPTY_RADIO_MARKER = "Playlist does not have at least 1 recordings"

# A validated username stays trusted for a day: the daemon lives longer than a
# session, and an account deleted server-side must surface within a reasonable time.
USERNAME_TTL = 24 * 3600.0

# A rejection reason is bounded and single-line: it travels into an error message the
# host may show, so it must not carry a payload dump.
_CAUSE_LIMIT = 200


def _rejection_cause(body: str, token: str) -> str:
    """The provider's own reason for a 401, with the token stripped and bounded.

    ListenBrainz answers ``{"code": 401, "error": "…"}``; a non-JSON body is used as is.
    The token must never surface: the body is the provider's text, not ours to trust.
    """
    text = ""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                text = value
                break
    if not text:
        text = body or ""
    text = " ".join(text.split())
    if token:
        text = text.replace(token, "***")
    return text[:_CAUSE_LIMIT]


class ListenBrainz:
    def __init__(self, client: HttpClient, credentials: Credentials) -> None:
        self._client = client
        self._credentials = credentials
        self._cached_token = ""
        self._cached_username = ""
        self._cached_at = 0.0

    @property
    def token(self) -> str:
        return self._credentials.token

    @property
    def is_authenticated(self) -> bool:
        return self._credentials.is_authenticated

    def require_auth(self) -> None:
        if not self.is_authenticated:
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
        """The authenticated account, or the public ``listenbrainz`` account when anonymous.

        The cache is keyed by the token: a changed token (log out and in as somebody
        else) invalidates it, so the previous account's name is never served, while an
        unchanged token keeps the cache with no extra ``validate-token`` call.
        """
        token = self._credentials.token
        if (
            token
            and token == self._cached_token
            and time.monotonic() - self._cached_at < USERNAME_TTL
        ):
            return self._cached_username
        if self._credentials.is_authenticated:
            name = self.validate_token(token)
            if name:
                self._cached_token = token
                self._cached_username = name
                self._cached_at = time.monotonic()
                return name
        return "listenbrainz"

    def me(self) -> Dict[str, Any]:
        if not self._credentials.is_authenticated:
            return {"user_id": None}
        return {"user_id": self.username()}

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

    def find_playlist(self, title: str) -> str:
        """Id of the user's playlist with this title, or ``""`` when there is none.

        A read-only lookup: listing the library must not create the hidden playlist.
        """
        for entry in self.user_playlists():
            playlist = entry.get("playlist") if isinstance(entry, dict) else None
            if isinstance(playlist, dict) and playlist.get("title") == title:
                identifier = playlist.get("identifier")
                if identifier:
                    return str(identifier).rsplit("/", 1)[-1]
        return ""

    def get_or_create_playlist(self, title: str) -> str:
        self.require_auth()
        existing = self.find_playlist(title)
        if existing:
            return existing
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

    def delete_playlist(self, mbid: str) -> None:
        self._client.post_json(
            f"{LISTENBRAINZ_API}playlist/{mbid}/delete", headers=self._headers(required=True)
        )

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

    def submit_listens(self, body: Dict[str, Any]) -> None:
        """Send a ``submit-listens`` body to the account's listening history.

        A 401 carries the provider's reason in the body (an unverified email is the one
        seen in the field). It travels into the error message because that is the only
        channel the host has: without it a revoked token and a refused account look
        identical. The token is redacted before it is exposed.
        """
        try:
            self._client.post_json(
                f"{LISTENBRAINZ_API}submit-listens",
                headers=self._headers(required=True),
                body=body,
            )
        except HttpAuthError as error:
            cause = _rejection_cause(error.body, self._credentials.token)
            if not cause:
                raise
            raise AuthRequiredError(
                f"ListenBrainz rejected the token (HTTP {error.status}): {cause}"
            ) from error

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

    def top_recordings_for_artist(self, artist_mbid: str) -> Any:
        """Recordings of an artist ordered by real listen count (token required).

        A 404 means ListenBrainz has no popularity data for the artist: an empty answer for
        a section the artist page hides, not a failure of the page.
        """
        try:
            return self._client.get_json(
                f"{LISTENBRAINZ_API}popularity/top-recordings-for-artist/{artist_mbid}",
                headers=self._headers(required=True),
            )
        except NotFoundError:
            return []

    def radio(self, prompt: str) -> Dict[str, Any]:
        try:
            data = self._client.get_json(
                f"{LISTENBRAINZ_API}explore/lb-radio",
                headers=self._headers(required=True),
                params={"prompt": prompt, "mode": "easy"},
            )
        except HttpStatusError as error:
            if error.status == 400 and _EMPTY_RADIO_MARKER in error.body:
                return {}
            raise
        return data if isinstance(data, dict) else {}
