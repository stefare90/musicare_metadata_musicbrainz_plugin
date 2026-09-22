"""``ITrack``: track details and ListenBrainz radio.

Radio takes the recording's artists and tags, asks the ListenBrainz ``lb-radio`` endpoint
for a JSPF playlist, and resolves the returned recording MBIDs back through MusicBrainz so
every track carries a complete album.
"""

from typing import Any, Dict, List

from musicare_metadata_plugin_sdk import ITrack, Track

from ..http import HttpClient
from ..mapping import build_track, recording_artist_ids, recording_tag_names
from ..providers import LISTENBRAINZ_API, MUSICBRAINZ_API

_TRACK_INCLUDES = "artist-credits+releases+release-groups+isrcs"


class MusicBrainzTrack(ITrack):
    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def get_track(self, id: str) -> Track:
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}recording/{id}",
            params={"fmt": "json", "inc": _TRACK_INCLUDES},
        )
        return build_track(data)

    def radio(self, id: str) -> List[Track]:
        seed = self._client.get_json(
            f"{MUSICBRAINZ_API}recording/{id}",
            params={"fmt": "json", "inc": "artist-credits+tags"},
        )
        prompt = self._radio_prompt(seed)
        radio_data = self._client.get_json(
            f"{LISTENBRAINZ_API}explore/lb-radio",
            params={"prompt": prompt, "mode": "easy"},
        )
        recording_ids = self._recording_ids(radio_data)
        if not recording_ids:
            return []
        recordings = self._client.get_json(
            f"{MUSICBRAINZ_API}recording",
            params={
                "fmt": "json",
                "query": " OR ".join(recording_ids),
                "inc": _TRACK_INCLUDES,
            },
        )
        if not isinstance(recordings, dict):
            return []
        return [
            build_track(recording)
            for recording in recordings.get("recordings") or []
            if isinstance(recording, dict)
        ]

    @staticmethod
    def _radio_prompt(seed: Dict[str, Any]) -> str:
        query = " ".join(f"artist:({mbid})" for mbid in recording_artist_ids(seed))
        tags = recording_tag_names(seed)
        if tags:
            query += f" tag:({','.join(tags)}):2:easy"
        return query

    @staticmethod
    def _recording_ids(radio_data: Any) -> List[str]:
        playlist = (((radio_data or {}).get("payload") or {}).get("jspf") or {}).get("playlist")
        tracks = playlist.get("track") if isinstance(playlist, dict) else None
        identifiers: List[str] = []
        for track in tracks or []:
            if not isinstance(track, dict):
                continue
            values = track.get("identifier")
            if isinstance(values, list) and values:
                identifiers.append(f"rid:{str(values[0]).rsplit('/', 1)[-1]}")
        return identifiers
