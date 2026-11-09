"""``IListen``: the account's ListenBrainz listening history.

The event arrives already validated by the runtime (contract 7.0.0), so this segment only
maps it onto ``submit-listens`` and decides the policy: nothing is sent without a token, and
a ``playing_now`` never fails the caller — it is a best-effort hint the app must not pay for.
The HTTP call itself belongs to ``ListenBrainz``, which owns the token header for every
authenticated call.

The contract types the event, not the provider id: ``Track.id`` is the plugin's opaque key,
so a MusicBrainz recording is sent as ``recording_mbid`` while a track that came from
somewhere else is left without one. ListenBrainz identifies a listen by **artist and title**
anyway and looks the MBID up; sending an app id would be worse than sending none.
"""

import uuid
from typing import Any, Dict, Optional

from musicare_metadata_plugin_sdk import (
    IListen,
    ListenEvent,
    ListenKind,
    MetadataPluginError,
    Track,
)

from ..service import ListenBrainz

SUBMISSION_CLIENT = "MusicAre Metadata Plugin"

# ListenBrainz' own convention for several credited artists in one string.
ARTIST_SEPARATOR = " & "

# Albums are addressed as ``rg:<release-group-mbid>`` or ``<release-mbid>`` by this plugin.
_RELEASE_GROUP_PREFIX = "rg"


def _mbid_or_none(value: str) -> Optional[str]:
    """The id if it is a MusicBrainz UUID, else ``None`` (an app id, ``radio:…``, empty)."""
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _album_ids(album_id: str) -> Dict[str, str]:
    """Map the album id onto the release field it actually names, if either is an MBID."""
    prefix, _, rest = album_id.partition(":")
    if prefix == _RELEASE_GROUP_PREFIX:
        group = _mbid_or_none(rest)
        return {"release_group_mbid": group} if group else {}
    release = _mbid_or_none(album_id)
    return {"release_mbid": release} if release else {}


def _track_metadata(track: Track, version: str) -> Dict[str, Any]:
    additional: Dict[str, Any] = {
        "submission_client": SUBMISSION_CLIENT,
        "submission_client_version": version,
    }
    if track.duration_ms > 0:
        additional["duration_ms"] = track.duration_ms
    recording = _mbid_or_none(track.id)
    if recording:
        additional["recording_mbid"] = recording
    additional.update(_album_ids(track.album.id))
    return {
        "artist_name": ARTIST_SEPARATOR.join(artist.name for artist in track.artists),
        "track_name": track.name,
        "release_name": track.album.name,
        "additional_info": additional,
    }


def _payload(event: ListenEvent, version: str) -> Dict[str, Any]:
    listen: Dict[str, Any] = {"track_metadata": _track_metadata(event.track, version)}
    # ListenBrainz rejects ``listened_at`` on ``playing_now``: the field belongs to a
    # counted listen only, whatever the event carries.
    if event.kind is ListenKind.SINGLE:
        listen["listened_at"] = event.listened_at
    return {"listen_type": event.kind.value, "payload": [listen]}


class MusicBrainzListen(IListen):
    def __init__(self, listenbrainz: ListenBrainz, version: str) -> None:
        self._listenbrainz = listenbrainz
        self._version = version

    def record(self, event: ListenEvent) -> None:
        if not self._listenbrainz.is_authenticated:
            return
        try:
            self._listenbrainz.submit_listens(_payload(event, self._version))
        except MetadataPluginError:
            if event.kind is ListenKind.PLAYING_NOW:
                return
            raise
