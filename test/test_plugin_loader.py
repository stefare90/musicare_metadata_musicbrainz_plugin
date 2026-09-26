"""The entry point the runtime imports must satisfy the SDK contract."""

from musicare_metadata_plugin_sdk import BaseMetadataPlugin, SearchCategory

from src.main import get_plugin


def test_get_plugin_returns_a_metadata_plugin():
    plugin = get_plugin()

    assert isinstance(plugin, BaseMetadataPlugin)
    assert plugin.id == "org.musicare.metadata.musicbrainz"
    assert plugin.name == "MusicBrainz & ListenBrainz"
    assert plugin.version == "1.2.0"


def test_implemented_interfaces_behave_without_network():
    plugin = get_plugin()

    assert plugin.core.support().startswith("https://")
    assert plugin.search.chips() == [
        SearchCategory.TRACKS,
        SearchCategory.ALBUMS,
        SearchCategory.ARTISTS,
        SearchCategory.PLAYLISTS,
    ]
