"""Translation from MusicBrainz JSON payloads to the SDK models.

These builders are shared by search, album, artist, track and playlist segments, so the
mapping lives in one place. They are deliberately defensive about optional arrays in the
upstream payload: MusicBrainz omits keys rather than sending empty ones, and a missing
optional block must degrade, not crash.
"""

from typing import Any, Dict, Iterable, List, Optional

from musicare_metadata_plugin_sdk import Album, AlbumType, Artist, Image, Track

from .providers import (
    COVER_SIZES,
    RELEASE_GROUP_COVER_SIZES,
    cover_url,
    external_uri,
)

_ALBUM_TYPE_BY_PRIMARY = {
    "single": AlbumType.SINGLE,
    "compilation": AlbumType.COMPILATION,
}


def _album_type(primary_type: Any) -> AlbumType:
    return _ALBUM_TYPE_BY_PRIMARY.get(str(primary_type or "").lower(), AlbumType.ALBUM)


def _release_date(value: Any) -> str:
    return "" if value is None else str(value)


def _credits(entries: Any) -> List[Artist]:
    """Build the artist list from a MusicBrainz ``artist-credit`` array."""
    artists: List[Artist] = []
    if not isinstance(entries, list):
        return artists
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        artist = entry.get("artist")
        if not isinstance(artist, dict) or not artist.get("id"):
            continue
        mbid = str(artist["id"])
        artists.append(
            Artist(
                id=mbid,
                name=str(artist.get("name") or ""),
                external_uri=external_uri("artist", mbid),
            )
        )
    return artists


def _genres(tags: Any) -> Optional[List[str]]:
    if not isinstance(tags, list):
        return None
    names = [
        str(tag["name"])
        for tag in tags
        if isinstance(tag, dict) and tag.get("name")
    ]
    return names or None


def _tag_names(tags: Any) -> List[str]:
    return _genres(tags) or []


#: Release-group secondary types that identify a reissue, a compilation or another
#: non-studio edition. They rank a release last, so a recording whose first release is a
#: "Summer Hits" collection still gets the cover of its own publication.
_NON_STUDIO_SECONDARY_TYPES = frozenset(
    {
        "compilation",
        "live",
        "dj-mix",
        "remix",
        "interview",
        "mixtape/street",
        "demo",
        "audiobook",
        "spokenword",
    }
)


def _release_date_key(value: Any) -> tuple:
    """Sort key for a MusicBrainz partial date (``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``).

    A missing month or day counts as the end of its period, because that is where
    MusicBrainz places a year-only release relative to the dated ones; treating it as
    January would let an undated edition jump ahead of the actual first release.
    """
    parts = str(value or "").strip().split("-")
    try:
        year = int(parts[0])
    except (ValueError, IndexError):
        return (9999, 12, 31)
    month = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 12
    day = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 31
    return (year, month, day)


def _release_rank(release: Dict[str, Any]) -> tuple:
    group = release.get("release-group")
    group = group if isinstance(group, dict) else {}
    secondary = group.get("secondary-types")
    secondary = secondary if isinstance(secondary, list) else []
    non_studio = any(str(item).lower() in _NON_STUDIO_SECONDARY_TYPES for item in secondary)
    official = 0 if str(release.get("status") or "") == "Official" else 1
    return (
        1 if non_studio else 0,
        official,
        _release_date_key(release.get("date")),
        str(release.get("id") or ""),
    )


def select_release(releases: Any, release_mbid: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pick the publication a recording's album and cover should come from.

    MusicBrainz orders a recording's ``releases`` differently per endpoint (the bulk
    ``recording?query=…`` search and the direct lookup disagree), so taking ``releases[0]``
    lands on a compilation as often as on the track's own release. The choice is made
    deterministic here: an explicit ``release_mbid`` wins when it is in the list, otherwise
    the earliest release, with non-studio editions ranked last and official prints first.
    """
    if not isinstance(releases, list):
        return None
    candidates = [release for release in releases if isinstance(release, dict)]
    if release_mbid:
        for release in candidates:
            if str(release.get("id") or "") == release_mbid:
                return release
    if not candidates:
        return None
    return min(candidates, key=_release_rank)


def _cover_images(prefix: str, mbid: str, sizes: Iterable[int]) -> List[Image]:
    return [Image(url=cover_url(prefix, mbid, size), width=size, height=size) for size in sizes]


def build_album(release: Dict[str, Any]) -> Album:
    """Build an album from a MusicBrainz *release* payload (the old plugin's ``buildAlbum``)."""
    release_id = str(release.get("id") or "")
    images: List[Image] = []
    cover_art = release.get("cover-art-archive")
    if isinstance(cover_art, dict) and cover_art.get("front") is True and release_id:
        images = _cover_images("release", release_id, COVER_SIZES)

    track_count = 0
    media = release.get("media")
    if isinstance(media, list):
        for medium in media:
            if isinstance(medium, dict) and isinstance(medium.get("track-count"), int):
                track_count += medium["track-count"]

    album_id = release_id
    album_type = AlbumType.ALBUM
    release_group = release.get("release-group")
    if isinstance(release_group, dict):
        group_id = release_group.get("id")
        if group_id:
            album_id = f"rg:{group_id}"
        album_type = _album_type(release_group.get("primary-type"))

    if not images:
        if isinstance(release_group, dict) and release_group.get("id"):
            images = _cover_images("release-group", str(release_group["id"]), COVER_SIZES)
        elif release_id:
            images = _cover_images("release", release_id, COVER_SIZES)

    return Album(
        id=album_id,
        name=str(release.get("title") or ""),
        artists=_credits(release.get("artist-credit")),
        images=images,
        release_date=_release_date(release.get("date")),
        external_uri=external_uri("release", release_id),
        total_tracks=track_count,
        album_type=album_type,
    )


def build_album_from_release_group(group: Dict[str, Any]) -> Album:
    """Build an album from a MusicBrainz *release-group* payload (search/artist discography)."""
    group_id = str(group.get("id") or "")
    return Album(
        id=f"rg:{group_id}",
        name=str(group.get("title") or ""),
        artists=_credits(group.get("artist-credit")),
        images=_cover_images("release-group", group_id, RELEASE_GROUP_COVER_SIZES),
        release_date=_release_date(group.get("first-release-date")),
        external_uri=external_uri("release-group", group_id),
        total_tracks=0,
        album_type=_album_type(group.get("primary-type")),
    )


def build_track(recording: Dict[str, Any], release_mbid: Optional[str] = None) -> Track:
    """Build a track from a MusicBrainz *recording* payload (the old plugin's ``buildTrack``).

    ``release_mbid`` narrows the album to one known publication (e.g. the release a saved
    feedback entry or a listen was recorded against); without it the album is the one
    :func:`select_release` chooses.
    """
    recording_id = str(recording.get("id") or "")
    artists = _credits(recording.get("artist-credit"))
    release = select_release(recording.get("releases"), release_mbid)
    if release is not None:
        album = build_album(release)
    else:
        album = Album(
            id="",
            name="",
            artists=artists,
            images=[],
            release_date="",
            external_uri="",
            total_tracks=0,
            album_type=AlbumType.ALBUM,
        )
    length = recording.get("length")
    return Track(
        id=recording_id,
        name=str(recording.get("title") or ""),
        external_uri=external_uri("recording", recording_id),
        artists=artists,
        album=album,
        duration_ms=length if isinstance(length, int) else 0,
    )


def _popularity_artists(entry: Dict[str, Any]) -> List[Artist]:
    """Build the artist list of a ListenBrainz popularity entry."""
    artists: List[Artist] = []
    entries = entry.get("artists")
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            mbid = item.get("artist_mbid")
            if not mbid:
                continue
            artists.append(
                Artist(
                    id=str(mbid),
                    name=str(item.get("artist_credit_name") or entry.get("artist_name") or ""),
                    external_uri=external_uri("artist", str(mbid)),
                )
            )
    if artists:
        return artists
    names = entry.get("artist_mbids")
    fallback_name = str(entry.get("artist_name") or "")
    if isinstance(names, list):
        return [
            Artist(
                id=str(mbid),
                name=fallback_name,
                external_uri=external_uri("artist", str(mbid)),
            )
            for mbid in names
            if mbid
        ]
    return []


def build_popularity_track(entry: Dict[str, Any]) -> Track:
    """Build a track from a ListenBrainz ``top-recordings-for-artist`` entry.

    The entry already carries everything the contract needs (name, length, artists and the
    Cover Art Archive release), so no MusicBrainz or Wikidata lookup is required.
    """
    recording_id = str(entry.get("recording_mbid") or "")
    artists = _popularity_artists(entry)
    release_mbid = str(entry.get("release_mbid") or "")
    cover_mbid = str(entry.get("caa_release_mbid") or release_mbid)
    album_id = release_mbid or cover_mbid
    return Track(
        id=recording_id,
        name=str(entry.get("recording_name") or ""),
        external_uri=external_uri("recording", recording_id),
        artists=artists,
        album=Album(
            id=album_id,
            name=str(entry.get("release_name") or ""),
            artists=artists,
            images=_cover_images("release", cover_mbid, COVER_SIZES) if cover_mbid else [],
            release_date="",
            external_uri=external_uri("release", album_id) if album_id else "",
            total_tracks=0,
            album_type=AlbumType.ALBUM,
        ),
        duration_ms=entry.get("length") if isinstance(entry.get("length"), int) else 0,
    )


def build_artist(artist: Dict[str, Any], images: Optional[List[Image]] = None) -> Artist:
    """Build an artist from a MusicBrainz *artist* payload."""
    mbid = str(artist.get("id") or "")
    return Artist(
        id=mbid,
        name=str(artist.get("name") or ""),
        external_uri=external_uri("artist", mbid),
        images=list(images or []),
        genres=_genres(artist.get("genres")) or _genres(artist.get("tags")),
        followers=None,
    )


def recording_tag_names(recording: Dict[str, Any]) -> List[str]:
    """Tag names of a recording, used to build a ListenBrainz radio prompt."""
    return _tag_names(recording.get("tags"))


def recording_artist_ids(recording: Dict[str, Any]) -> List[str]:
    """Artist MBIDs of a recording, used to build a ListenBrainz radio prompt."""
    return [artist.id for artist in _credits(recording.get("artist-credit"))]
