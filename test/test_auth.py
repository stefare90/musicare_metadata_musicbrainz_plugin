"""``IAuth``: the token state machine, against a stub ListenBrainz."""

import os

from musicare_metadata_plugin_sdk import (
    Account,
    AuthContext,
    AuthStatus,
    Authenticated,
    Failed,
    FormInputField,
    NeedsForm,
    TransportError,
)

from src.service import Credentials
from src.service import ListenBrainz
from src.segments.auth import MusicBrainzAuth

from ._stubs import StubClient


def _ctx(tmp_path):
    return AuthContext(data_dir=str(tmp_path / "data"), plugin_id="test")


def _auth(handler):
    credentials = Credentials()
    return MusicBrainzAuth(ListenBrainz(StubClient(handler), credentials), credentials), credentials


def _no_network(url, params):
    raise AssertionError(f"unexpected request: {url}")


def _stored(ctx, token="stored", user="tester"):
    credentials = Credentials()
    credentials.store(ctx, token, user)
    return credentials


def test_start_asks_for_the_token_when_unauthenticated(tmp_path):
    auth, _ = _auth(_no_network)

    action = auth.start(_ctx(tmp_path))

    assert isinstance(action, NeedsForm)
    assert action.title == "Connect to ListenBrainz"
    assert action.message == "Paste the personal token from your ListenBrainz profile."
    assert action.fields[0].id == "token"
    assert action.fields[0].is_password is True
    assert action.fields[0].help_url == "https://listenbrainz.org/profile/"


def test_start_reports_the_stored_account_without_a_network_call(tmp_path):
    ctx = _ctx(tmp_path)
    _stored(ctx)
    auth, _ = _auth(_no_network)

    action = auth.start(ctx)

    assert isinstance(action, Authenticated)
    assert action.account == Account(id="tester", display_name="tester")


def test_start_is_idempotent(tmp_path):
    ctx = _ctx(tmp_path)
    auth, _ = _auth(_no_network)

    first = auth.start(ctx)
    second = auth.start(ctx)

    # The unauthenticated step is reported again, never a second flow (no state, no file).
    assert isinstance(first, NeedsForm) and isinstance(second, NeedsForm)
    assert ctx.read_json("auth.json") is None
    assert not os.path.exists(ctx.data_dir)

    _stored(ctx)
    assert isinstance(auth.start(ctx), Authenticated)
    assert isinstance(auth.start(ctx), Authenticated)
    assert ctx.read_json("auth.json") == {"token": "stored", "user": "tester"}


def test_status_without_credentials(tmp_path):
    auth, _ = _auth(_no_network)

    status = auth.status(_ctx(tmp_path))

    assert status == AuthStatus(authenticated=False)
    assert status.to_dict() == {"state": "idle", "authenticated": False}


def test_status_reads_the_stored_account_without_a_network_call(tmp_path):
    ctx = _ctx(tmp_path)
    _stored(ctx)
    auth, _ = _auth(_no_network)

    status = auth.status(ctx)

    assert status.authenticated is True
    assert status.account == Account(id="tester", display_name="tester")
    assert status.to_dict() == {
        "state": "idle",
        "authenticated": True,
        "account": {"id": "tester", "display_name": "tester"},
    }


def test_status_keeps_an_unknown_account_unknown(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.write_json("auth.json", {"token": "stored"})
    auth, _ = _auth(_no_network)

    status = auth.status(ctx)

    assert status.authenticated is True
    assert status.account is None


def test_complete_validates_and_stores_the_token_and_the_user(tmp_path):
    ctx = _ctx(tmp_path)
    auth, credentials = _auth(lambda url, params: {"user_name": "tester"})

    action = auth.complete(ctx, {"token": "good"})

    assert isinstance(action, Authenticated)
    assert action.account == Account(id="tester", display_name="tester")
    assert ctx.read_json("auth.json") == {"token": "good", "user": "tester"}
    assert credentials.load(ctx) is True
    assert (credentials.token, credentials.user) == ("good", "tester")


def test_the_credentials_file_is_private(tmp_path):
    ctx = _ctx(tmp_path)
    auth, _ = _auth(lambda url, params: {"user_name": "tester"})

    auth.complete(ctx, {"token": "good"})

    mode = os.stat(os.path.join(ctx.data_dir, "auth.json")).st_mode & 0o777
    assert mode == 0o600


def test_complete_rejects_an_invalid_token(tmp_path):
    ctx = _ctx(tmp_path)
    auth, credentials = _auth(lambda url, params: {})

    action = auth.complete(ctx, {"token": "bad"})

    assert isinstance(action, Failed)
    assert action.code == "auth_required"
    assert action.retryable is False
    assert credentials.is_authenticated is False
    assert ctx.read_json("auth.json") is None


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


def test_logout_clears_the_stored_credentials(tmp_path):
    ctx = _ctx(tmp_path)
    auth, credentials = _auth(_no_network)
    credentials.store(ctx, "stored", "tester")

    auth.logout(ctx)

    assert ctx.read_json("auth.json") is None
    assert credentials.is_authenticated is False
    assert credentials.user == ""
    assert auth.status(ctx) == AuthStatus(authenticated=False)


def test_cancel_keeps_the_stored_credentials(tmp_path):
    ctx = _ctx(tmp_path)
    _stored(ctx)
    auth, _ = _auth(_no_network)

    auth.cancel(ctx)

    assert auth.status(ctx).authenticated is True


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
