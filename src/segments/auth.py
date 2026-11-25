"""``IAuth``: the ListenBrainz token flow.

The host drives the shared auth state machine: ``status`` reports the stored credentials,
``start`` asks what the flow needs now, ``complete`` delivers the values the user typed,
``cancel`` abandons it and ``logout`` clears the credentials. ``start`` is idempotent (the
runtime re-asks it while a step is pending), so it must not repeat side effects.

``status`` answers from ``auth.json`` alone: it must not put a ``validate-token`` request on
the host's startup path, so a token revoked server-side looks healthy until the first
authenticated call fails with ``auth_required``.

The plugin is the only owner of the provider knowledge; the token is validated against
ListenBrainz, stored in the plugin's private data directory and never sent to the host.
"""

from typing import Dict, Optional

from musicare_metadata_plugin_sdk import (
    Account,
    AuthAction,
    AuthContext,
    AuthStatus,
    Authenticated,
    Failed,
    FormInputField,
    IAuth,
    NeedsForm,
    TransportError,
)

from ..service import Credentials
from ..service import ListenBrainz

_TOKEN_HELP_URL = "https://listenbrainz.org/profile/"
_TOKEN_FORM_TITLE = "Connect to ListenBrainz"
_TOKEN_FORM_MESSAGE = "Paste the personal token from your ListenBrainz profile."

_TOKEN_FIELD = FormInputField(
    id="token",
    label="ListenBrainz token",
    is_password=True,
    help_url=_TOKEN_HELP_URL,
)


class MusicBrainzAuth(IAuth):
    def __init__(self, lb: ListenBrainz, credentials: Credentials) -> None:
        self._lb = lb
        self._credentials = credentials

    def status(self, ctx: AuthContext) -> AuthStatus:
        if not self._credentials.load(ctx):
            return AuthStatus(authenticated=False)
        # An ``auth.json`` from before the account name was stored has a token but no
        # user: the account stays unknown rather than being guessed, and the contract
        # accepts "authenticated without an account".
        return AuthStatus(authenticated=True, account=self._account())

    def start(self, ctx: AuthContext) -> AuthAction:
        if self._credentials.load(ctx):
            return Authenticated(self._account())
        return NeedsForm(
            [_TOKEN_FIELD], title=_TOKEN_FORM_TITLE, message=_TOKEN_FORM_MESSAGE
        )

    def complete(self, ctx: AuthContext, values: Dict[str, str]) -> AuthAction:
        token = str((values or {}).get("token") or "").strip()
        if not token:
            return Failed("No token provided", code="auth_required")
        try:
            user_name = self._lb.validate_token(token)
        except TransportError as error:
            return Failed(str(error), code="transport_error", retryable=True)
        if not user_name:
            return Failed("ListenBrainz rejected the token", code="auth_required")
        self._credentials.store(ctx, token, user_name)
        return Authenticated(self._account())

    def cancel(self, ctx: AuthContext) -> None:
        """No state to roll back: the runtime closes the pending flow."""

    def logout(self, ctx: AuthContext) -> None:
        self._credentials.clear(ctx)

    def _account(self) -> Optional[Account]:
        user = self._credentials.user
        return Account(id=user, display_name=user) if user else None
