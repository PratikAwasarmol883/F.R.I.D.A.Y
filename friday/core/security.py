import hashlib
import secrets

TOKEN_PREFIX = "fri_"


def generate_token() -> str:
    """256-bit random device token; shown to the operator once, never stored."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Fast hash is appropriate because the token is high-entropy random, not a password."""
    return hashlib.sha256(token.encode()).hexdigest()
