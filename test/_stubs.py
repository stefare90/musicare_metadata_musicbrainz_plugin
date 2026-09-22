"""Test doubles for the HTTP client and the Wikidata image resolver."""

from musicare_metadata_plugin_sdk import Artist, AuthContext

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz


class StubClient:
    """Routes requests through handlers and records every call.

    ``calls`` entries are ``(kind, url, payload)`` where ``payload`` is the query params
    for a GET and the JSON body for a POST.
    """

    def __init__(self, handler=None, post_handler=None):
        self.handler = handler or (lambda url, params: {})
        self.post_handler = post_handler or (lambda url, body: {})
        self.calls = []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append(("get", url, params))
        return self.handler(url, params)

    def get_json_or_none(self, url, params=None, **kwargs):
        self.calls.append(("optional", url, params))
        return self.handler(url, params)

    def post_json(self, url, params=None, headers=None, body=None, **kwargs):
        self.calls.append(("post", url, body))
        return self.post_handler(url, body)


class StubImages:
    """Stands in for ``WikidataArtistImages`` so tests never touch Wikidata."""

    def __init__(self, images_by_id=None):
        self.images_by_id = images_by_id or {}
        self.requested = []

    def resolve(self, mbids):
        mbids = list(mbids)
        self.requested.append(mbids)
        return {mbid: self.images_by_id.get(mbid, []) for mbid in mbids}

    def enrich(self, artists):
        return [
            Artist(
                id=artist.id,
                name=artist.name,
                external_uri=artist.external_uri,
                images=self.images_by_id.get(artist.id, []),
                genres=artist.genres,
                followers=artist.followers,
            )
            for artist in artists
        ]


def router(responses):
    """Build a handler that matches a URL by suffix, for the simple cases."""

    def handler(url, params):
        for suffix, payload in responses.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"unexpected URL: {url}")

    return handler


def authenticated_lb(client, data_dir, token="token"):
    """Build a ``ListenBrainz`` service already holding a stored token."""
    credentials = Credentials()
    credentials.store(AuthContext(data_dir=str(data_dir), plugin_id="test"), token)
    return ListenBrainz(client, credentials), credentials
