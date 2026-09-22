"""Provider endpoints and the URI conventions shared by every segment."""

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2/"
MUSICBRAINZ_SITE = "https://musicbrainz.org/"
LISTENBRAINZ_API = "https://api.listenbrainz.org/1/"
LISTENBRAINZ_SITE = "https://listenbrainz.org/"
LISTENBRAINZ_LABS = "https://labs.api.listenbrainz.org"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
COVER_ART_ARCHIVE = "https://coverartarchive.org/"

# The two private ListenBrainz playlists the plugin uses as the "saved albums" and
# "saved artists" library (ListenBrainz has no first-class equivalent).
SAVED_ALBUMS_PLAYLIST = "__GYAWUN_ALBUMS__"
SAVED_ARTISTS_PLAYLIST = "__GYAWUN_ARTISTS__"

# Album cover sizes, matching the old plugin so cached artwork stays valid.
COVER_SIZES = (250, 500)
RELEASE_GROUP_COVER_SIZES = (250, 500, 1200)


def external_uri(kind: str, mbid: str) -> str:
    return f"{MUSICBRAINZ_SITE}{kind}/{mbid}"


def cover_url(prefix: str, mbid: str, width: int) -> str:
    return f"{COVER_ART_ARCHIVE}{prefix}/{mbid}/front-{width}.jpg"
