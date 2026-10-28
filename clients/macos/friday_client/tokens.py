"""Where the device token lives. Never printed, never part of a repr."""

import os
from typing import Any, Protocol

SERVICE = "friday-client"


class TokenStore(Protocol):
    def get(self) -> str | None: ...


class EnvTokenStore:
    """`FRIDAY_TOKEN`, for development and automation."""

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else dict(os.environ)

    def get(self) -> str | None:
        return self._environ.get("FRIDAY_TOKEN") or None

    def __repr__(self) -> str:
        return "EnvTokenStore()"


class KeyringTokenStore:
    """macOS Keychain through the `keyring` package (optional dependency)."""

    def __init__(self, account: str, keyring_module: Any | None = None) -> None:
        self._account = account
        self._keyring = keyring_module

    def _module(self) -> Any:
        if self._keyring is None:
            import keyring  # lazy: only needed on a real Mac

            self._keyring = keyring
        return self._keyring

    def get(self) -> str | None:
        return self._module().get_password(SERVICE, self._account) or None

    def set(self, token: str) -> None:
        self._module().set_password(SERVICE, self._account, token)

    def delete(self) -> None:
        self._module().delete_password(SERVICE, self._account)

    def __repr__(self) -> str:
        return f"KeyringTokenStore(account={self._account!r})"


class ChainTokenStore:
    """First store that has a token wins."""

    def __init__(self, *stores: TokenStore) -> None:
        self._stores = stores

    def get(self) -> str | None:
        for store in self._stores:
            try:
                token = store.get()
            except Exception:  # a broken keychain must not hide an environment token
                continue
            if token:
                return token
        return None
