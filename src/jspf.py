"""Reading JSPF (JSON Playlist Format) tracks from ListenBrainz.

ListenBrainz playlists and radios are JSPF documents. The interesting fields live in the
``https://musicbrainz.org/doc/jspf#track`` extension: artist MBIDs, the release/release
group, and an optional duration. The old plugin also accepted a few flat fallbacks, kept
here so playlists created by it still parse.
"""

from typing import Any, Dict, List, Optional

from musicare_metadata_plugin_sdk import Album, AlbumType, Artist, Image, Track

from .providers import cover_url, external_uri

MB_TRACK_EXTENSION = "https://musicbrainz.org/doc/jspf#track"
MB_PLAYLIST_EXTENSION = "https://musicbrainz.org/doc/jspf#playlist"
_LENGTH_FIELDS = ("duration_ms", "length")
_TOP_LEVEL_LENGTH_FIELDS = ("duration", "length")


def playlist_is_public(playlist: Dict[str, Any]) -> bool:
    """Whether a JSPF playlist document asks for public visibility.

    A missing extension, or a missing ``public`` key, means private: that is how
    ListenBrainz represents the default and how playlists created before the field
    existed read. The value is coerced because ListenBrainz sometimes serialises the
    flag as the string ``"true"``.
    """
    extension = playlist.get("extension")
    playlist_extension = (
        extension.get(MB_PLAYLIST_EXTENSION) if isinstance(extension, dict) else None
    )
    if not isinstance(playlist_extension, dict):
        return False
    value = playlist_extension.get("public")
    return value is True or str(value) == "true"


def _extension(track: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    extension = track.get("extension")
    if not isinstance(extension, dict):
        return None
    value = extension.get(MB_TRACK_EXTENSION)
    return value if isinstance(value, dict) else None


def _positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def extract_duration_ms(track: Dict[str, Any]) -> int:
    for field in _TOP_LEVEL_LENGTH_FIELDS:
        parsed = _positive_int(track.get(field))
        if parsed is not None:
            return parsed
    extension = _extension(track)
    if extension:
        for field in _LENGTH_FIELDS:
            parsed = _positive_int(extension.get(field))
            if parsed is not None:
                return parsed
    return 0


def extract_artists(track: Dict[str, Any]) -> List[Artist]:
    creator = str(track.get("creator") or "Unknown Artist")
    artists: List[Artist] = []
    extension = _extension(track)
    if extension:
        identifiers = extension.get("artist_identifiers")
        if isinstance(identifiers, list):
            for uri in identifiers:
                mbid = str(uri).rsplit("/", 1)[-1]
                if mbid and mbid != "null":
                    artists.append(
                        Artist(id=mbid, name=creator, external_uri=external_uri("artist", mbid))
                    )
    if not artists:
        artists.append(Artist(id="", name=creator, external_uri=""))
    return artists


def _first_mbid(value: Any) -> str:
    """Extract an MBID from a JSPF identifier value.

    ListenBrainz sends identifiers as a bare MBID or as a MusicBrainz URL, occasionally
    wrapped in a list; a missing, ``"null"`` or empty entry yields ``""``.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        for entry in value:
            mbid = _first_mbid(entry)
            if mbid:
                return mbid
        return ""
    text = str(value).strip()
    if not text or text == "null":
        return ""
    return text.rstrip("/").rsplit("/", 1)[-1]


def extract_album_mbid(track: Dict[str, Any]) -> str:
    extension = _extension(track)
    if not extension:
        return ""
    direct_group = _first_mbid(extension.get("release_group_mbid"))
    if direct_group:
        return f"rg:{direct_group}"
    additional = extension.get("additional_metadata")
    if isinstance(additional, dict):
        caa_mbid = _first_mbid(additional.get("caa_release_mbid"))
        if caa_mbid:
            return caa_mbid
    release = _first_mbid(extension.get("release_mbid"))
    if release:
        return release
    # ListenBrainz radio (`lb-radio`) sends the release only as a URI here, and it is the
    # sole album reference those entries carry — hence the sole source of their cover art.
    return _first_mbid(extension.get("release_identifier"))


def _cover_images(endpoint: str, mbid: str) -> List[Image]:
    return [
        Image(url=cover_url(endpoint, mbid, width), width=width, height=width)
        for width in (250, 500, 1200)
    ]


def extract_images(track: Dict[str, Any], album_mbid: str) -> List[Image]:
    image = track.get("image")
    if image:
        value = str(image)
        if value != "null":
            return [Image(url=value, width=500, height=500)]
    if album_mbid and album_mbid != "null":
        is_release_group = album_mbid.startswith("rg:")
        clean = album_mbid[3:] if is_release_group else album_mbid
        return _cover_images("release-group" if is_release_group else "release", clean)
    return []


def track_identifier(track: Dict[str, Any]) -> Optional[str]:
    identifiers = track.get("identifier")
    if not isinstance(identifiers, list) or not identifiers:
        return None
    return str(identifiers[0])


def build_track(track: Dict[str, Any]) -> Optional[Track]:
    """Build a track from a JSPF entry, or ``None`` when it has no identifier."""
    identifier = track_identifier(track)
    if not identifier:
        return None
    track_id = identifier.rsplit("/", 1)[-1]
    title = str(track.get("title") or "Unknown Track")
    album_name = str(track.get("album") or "")
    artists = extract_artists(track)
    album_mbid = extract_album_mbid(track)
    album_images = extract_images(track, album_mbid)

    is_release_group = album_mbid.startswith("rg:")
    clean = album_mbid[3:] if is_release_group else album_mbid
    endpoint = "release-group" if is_release_group else "release"
    release_uri = external_uri(endpoint, clean) if clean and clean != "null" else ""
    album = Album(
        id=album_mbid,
        name=album_name,
        artists=artists,
        images=album_images,
        release_date="",
        external_uri=release_uri,
        total_tracks=0,
        album_type=AlbumType.ALBUM,
    )
    return Track(
        id=track_id,
        name=title,
        external_uri=external_uri("recording", track_id),
        artists=artists,
        album=album,
        duration_ms=extract_duration_ms(track),
    )
