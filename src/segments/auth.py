"""``IAuth``: the ListenBrainz token flow.

The host drives a small state machine: ``start`` reports what the flow needs now (the form
with the token), ``complete`` delivers the typed value, ``cancel`` abandons it and
``logout`` clears the credentials. The flow is idempotent: ``start`` is also how the host
polls ``status``, so it must not repeat side effects.

The plugin is the only owner of the provider knowledge; the token is validated against
ListenBrainz, stored in the plugin's own data directory and never sent to the host.
"""

from typing import Dict

from musicare_metadata_plugin_sdk import (
    Authenticated,
    AuthAction,
    AuthContext,
    Failed,
    FormInputField,
    IAuth,
    NeedsForm,
    TransportError,
)

from ..credentials import Credentials
from ..listenbrainz import ListenBrainz

_TOKEN_FIELD = FormInputField(id="token", label="ListenBrainz token", is_password=True)


class MusicBrainzAuth(IAuth):
    def __init__(self, lb: ListenBrainz, credentials: Credentials) -> None:
        self._lb = lb
        self._credentials = credentials

    def start(self, ctx: AuthContext) -> AuthAction:
        if self._credentials.load(ctx):
            return Authenticated()
        return NeedsForm([_TOKEN_FIELD])

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
        self._credentials.store(ctx, token)
        return Authenticated()

    def cancel(self, ctx: AuthContext) -> None:
        """No state to roll back: the runtime closes the pending flow."""

    def logout(self, ctx: AuthContext) -> None:
        self._credentials.clear(ctx)

    def is_authenticated(self, ctx: AuthContext) -> bool:
        return self._credentials.load(ctx)
