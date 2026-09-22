"""MusicBrainz payload builders shared by the offline tests."""

from typing import Any, Dict, List, Optional

ARTIST_MBID = "artist-1"
ARTIST_NAME = "Radiohead"


def credit(mbid: str = ARTIST_MBID, name: str = ARTIST_NAME) -> Dict[str, Any]:
    return {"artist": {"id": mbid, "name": name}}


def release_payload(
    release_id: str = "release-1",
    group_id: str = "group-1",
    title: str = "OK Computer",
    date: str = "1997-05-28",
    primary_type: str = "Album",
    front: bool = False,
    tracks: int = 12,
    artist_mbid: str = ARTIST_MBID,
    artist_name: str = ARTIST_NAME,
    with_release_group: bool = True,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": release_id,
        "title": title,
        "date": date,
        "artist-credit": [credit(artist_mbid, artist_name)],
        "media": [{"track-count": tracks}, {"track-count": 1}],
    }
    if with_release_group:
        payload["release-group"] = {"id": group_id, "primary-type": primary_type}
    if front:
        payload["cover-art-archive"] = {"front": True}
    return payload


def recording_payload(
    recording_id: str = "recording-1",
    title: str = "Paranoid Android",
    length: Optional[int] = 383000,
    artist_mbid: str = ARTIST_MBID,
    artist_name: str = ARTIST_NAME,
    releases: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": recording_id,
        "title": title,
        "length": length,
        "artist-credit": [credit(artist_mbid, artist_name)],
    }
    if releases is not None:
        payload["releases"] = releases
    if tags is not None:
        payload["tags"] = [{"name": tag} for tag in tags]
    return payload


def release_group_payload(
    group_id: str = "group-1",
    title: str = "OK Computer",
    date: str = "1997-05-28",
    primary_type: str = "Album",
    artist_mbid: str = ARTIST_MBID,
    artist_name: str = ARTIST_NAME,
) -> Dict[str, Any]:
    return {
        "id": group_id,
        "title": title,
        "first-release-date": date,
        "primary-type": primary_type,
        "artist-credit": [credit(artist_mbid, artist_name)],
    }


def artist_payload(
    mbid: str = ARTIST_MBID,
    name: str = ARTIST_NAME,
    genres: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"id": mbid, "name": name}
    if genres is not None:
        payload["genres"] = [{"name": genre} for genre in genres]
    if tags is not None:
        payload["tags"] = [{"name": tag} for tag in tags]
    return payload
