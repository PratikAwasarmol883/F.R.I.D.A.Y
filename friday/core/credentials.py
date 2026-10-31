"""Encryption of integration credentials at rest."""

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class CredentialError(Exception):
    """A stored credential could not be decrypted (wrong key or corrupted)."""


class CredentialBox:
    def __init__(self, key: SecretStr) -> None:
        self._fernet = Fernet(key.get_secret_value().encode())

    def seal(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def open(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise CredentialError from exc


def is_valid_key(value: str) -> bool:
    try:
        Fernet(value.encode())
    except (ValueError, TypeError):
        return False
    return True
