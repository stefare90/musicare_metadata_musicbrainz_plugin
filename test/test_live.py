"""Live provider tests (network required): ``pytest -m live``.

These are excluded from the default run by ``pytest.ini``. They exercise the real
MusicBrainz, ListenBrainz and Wikidata endpoints through the same entry point the runtime
uses, so they are the last check before the end-to-end harness.
"""

import time

import pytest

from musicare_metadata_plugin_sdk import RateLimitedError

from src.http import HttpClient
from src.images.wikidata import WikidataArtistImages
from src.main import get_plugin

pytestmark = pytest.mark.live

RADIOHEAD_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


def _retry(call, attempts: int = 3):
    """Retry a live call when the provider throttles, with exponential backoff."""
    for attempt in range(attempts):
        try:
            return call()
        except RateLimitedError:
            if attempt == attempts - 1:
                raise
            time.sleep(3 * (attempt + 1))


def test_search_details_round_trip():
    _retry(_search_details_round_trip)


def _search_details_round_trip():
    plugin = get_plugin()

    tracks = plugin.search.tracks("radiohead", limit=5)
    assert tracks.items
    assert plugin.track.get_track(tracks.items[0].id).id == tracks.items[0].id

    albums = plugin.search.albums("radiohead", limit=5)
    assert albums.items
    assert plugin.album.get_album(albums.items[0].id).name

    artists = plugin.search.artists("radiohead", limit=5)
    assert artists.items
    assert plugin.artist.get_artist(artists.items[0].id).name


def test_album_tracks_are_complete():
    plugin = get_plugin()
    album = plugin.search.albums("radiohead", limit=1).items[0]

    page = plugin.album.tracks(album.id, limit=5)

    assert page.items
    assert all(track.album.id == album.id for track in page.items)


def test_artist_has_images_from_wikidata():
    artist = get_plugin().artist.get_artist(RADIOHEAD_MBID)

    assert artist.name == "Radiohead"
    assert artist.images
    assert artist.images[0].url.startswith("https://commons.wikimedia.org/")


def test_wikidata_maps_a_musicbrainz_id_to_the_commons_sizes():
    images = WikidataArtistImages(HttpClient(rate_limits={})).resolve([RADIOHEAD_MBID])

    assert [image.width for image in images[RADIOHEAD_MBID]] == [56, 250, 500, 1000]
    assert images[RADIOHEAD_MBID][0].url.startswith("https://commons.wikimedia.org/")


def test_browse_is_available_without_authentication():
    from musicare_metadata_plugin_sdk import AuthContext

    plugin = get_plugin()

    assert [section.id for section in plugin.browse.sections().items] == [
        "created_for_you",
        "top_artist_radios",
        "mood_playlists",
    ]
    assert plugin.browse.section_items("mood_playlists").items
    context = AuthContext(data_dir="/tmp/musicare-metadata-live", plugin_id=plugin.id)
    assert plugin.auth.is_authenticated(context) is False
