"""``IAuth``: the token state machine, against a stub ListenBrainz."""

from musicare_metadata_plugin_sdk import (
    Authenticated,
    AuthContext,
    Failed,
    FormInputField,
    NeedsForm,
    TransportError,
)

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz
from src.segments.auth import MusicBrainzAuth

from ._stubs import StubClient


def _ctx(tmp_path):
    return AuthContext(data_dir=str(tmp_path / "data"), plugin_id="test")


def _auth(handler):
    credentials = Credentials()
    return MusicBrainzAuth(ListenBrainz(StubClient(handler), credentials), credentials), credentials


def test_start_asks_for_the_token_when_unauthenticated(tmp_path):
    auth, _ = _auth(lambda url, params: {})

    action = auth.start(_ctx(tmp_path))

    assert isinstance(action, NeedsForm)
    assert action.title == "Connect to ListenBrainz"
    assert action.message == "Paste the personal token from your ListenBrainz profile."
    assert action.fields[0].id == "token"
    assert action.fields[0].is_password is True
    assert action.fields[0].help_url == "https://listenbrainz.org/profile/"


def test_start_reports_authenticated_when_a_token_is_stored(tmp_path):
    ctx = _ctx(tmp_path)
    auth, credentials = _auth(lambda url, params: {})
    credentials.store(ctx, "stored")

    assert isinstance(auth.start(ctx), Authenticated)
    assert auth.is_authenticated(ctx) is True


def test_complete_validates_and_stores_the_token(tmp_path):
    ctx = _ctx(tmp_path)
    auth, credentials = _auth(lambda url, params: {"user_name": "tester"})

    action = auth.complete(ctx, {"token": "good"})

    assert isinstance(action, Authenticated)
    assert credentials.load(ctx) is True
    assert credentials.token == "good"


def test_complete_rejects_an_invalid_token(tmp_path):
    auth, credentials = _auth(lambda url, params: {})

    action = auth.complete(_ctx(tmp_path), {"token": "bad"})

    assert isinstance(action, Failed)
    assert action.code == "auth_required"
    assert not isinstance(action, NeedsForm)
    assert credentials.is_authenticated is False


def test_complete_reports_a_transport_failure_as_retryable(tmp_path):
    def handler(url, params):
        raise TransportError("no route to host")

    auth, _ = _auth(handler)

    action = auth.complete(_ctx(tmp_path), {"token": "good"})

    assert isinstance(action, Failed)
    assert action.code == "transport_error"
    assert action.retryable is True


def test_complete_without_a_token_fails(tmp_path):
    auth, _ = _auth(lambda url, params: {"user_name": "tester"})

    action = auth.complete(_ctx(tmp_path), {"token": "  "})

    assert isinstance(action, Failed)
    assert action.code == "auth_required"


def test_logout_clears_the_stored_token(tmp_path):
    ctx = _ctx(tmp_path)
    auth, credentials = _auth(lambda url, params: {})
    credentials.store(ctx, "stored")

    auth.logout(ctx)

    assert auth.is_authenticated(ctx) is False


def test_form_copy_is_optional_in_the_model():
    field = FormInputField(id="token", label="ListenBrainz token")
    form = NeedsForm([field])

    assert form.title is None
    assert form.message is None
    assert field.help_url is None
    assert field.to_dict() == {
        "id": "token",
        "label": "ListenBrainz token",
        "is_password": False,
    }
