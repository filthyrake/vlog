"""
Password hashing and validation utilities.

Uses argon2id for password hashing (same as worker API keys).
OWASP recommended parameters for memory-hard, GPU-resistant hashing.

For session/API tokens, uses SHA-256 for faster verification.
Tokens are already cryptographically random (256+ bits entropy),
so expensive memory-hard hashing provides no additional security.
"""

import hashlib
import hmac
import secrets
from typing import Tuple

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from config import PASSWORD_MIN_LENGTH

# Argon2id parameters (OWASP recommended minimums)
# These are stored in the hash output, so verification works even if defaults change
_password_hasher = PasswordHasher(
    time_cost=3,  # iterations
    memory_cost=65536,  # 64MB memory
    parallelism=4,  # threads
)


def hash_password(password: str) -> str:
    """
    Hash a password using argon2id.

    Args:
        password: The plaintext password to hash

    Returns:
        The argon2id hash string with embedded salt and parameters
    """
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against a stored hash.

    Args:
        password: The plaintext password to verify
        password_hash: The stored argon2id hash

    Returns:
        True if the password matches, False otherwise
    """
    if not password_hash:
        return False

    try:
        _password_hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        # Malformed hash in database
        return False


def needs_rehash(password_hash: str) -> bool:
    """
    Check if a password hash needs to be rehashed.

    This happens when argon2 parameters have been updated.

    Args:
        password_hash: The stored hash to check

    Returns:
        True if the hash should be updated with new parameters
    """
    if not password_hash:
        return False

    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """
    Validate password meets minimum requirements.

    Args:
        password: The password to validate

    Returns:
        Tuple of (is_valid, error_message)
        If valid, error_message is empty string
    """
    if not password:
        return False, "Password is required"

    if len(password) < PASSWORD_MIN_LENGTH:
        return False, f"Password must be at least {PASSWORD_MIN_LENGTH} characters"

    # Basic complexity check - at least one of each: letter and number/symbol
    has_letter = any(c.isalpha() for c in password)
    has_non_letter = any(not c.isalpha() for c in password)

    if not has_letter or not has_non_letter:
        return False, "Password must contain both letters and numbers/symbols"

    return True, ""


def generate_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token.

    Args:
        length: Number of random bytes (output will be ~1.3x longer due to base64)

    Returns:
        URL-safe base64-encoded token
    """
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """
    Hash a token using argon2id.

    Used for session tokens, refresh tokens, API keys, etc.

    Args:
        token: The plaintext token to hash

    Returns:
        The argon2id hash string
    """
    return _password_hasher.hash(token)


def verify_token(token: str, token_hash: str) -> bool:
    """
    Verify a token against a stored hash.

    Args:
        token: The plaintext token to verify
        token_hash: The stored hash

    Returns:
        True if the token matches, False otherwise
    """
    if not token or not token_hash:
        return False

    try:
        _password_hasher.verify(token_hash, token)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def get_token_prefix(token: str, length: int = 8) -> str:
    """
    Get the first N characters of a token for efficient database lookup.

    Args:
        token: The token to get prefix from
        length: Number of characters to extract (default 8)

    Returns:
        The first N characters of the token
    """
    return token[:length] if len(token) >= length else token


# =============================================================================
# Fast Token Hashing (SHA-256)
# =============================================================================
# For session tokens, refresh tokens, API keys, and other high-entropy tokens.
# These tokens are cryptographically random (256+ bits entropy), so expensive
# memory-hard hashing provides no additional security benefit.
# SHA-256 is ~50,000x faster than argon2id, making it suitable for the hot path.


def hash_token_fast(token: str) -> str:
    """
    Hash a token using SHA-256 (for session/API tokens only).

    This is much faster than argon2id (~0.001ms vs ~50ms) and is appropriate
    for high-entropy random tokens that cannot be brute-forced.

    Args:
        token: The plaintext token to hash

    Returns:
        The SHA-256 hash as a hex string
    """
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token_fast(token: str, token_hash: str) -> bool:
    """
    Verify a token against a SHA-256 hash using constant-time comparison.

    Args:
        token: The plaintext token to verify
        token_hash: The stored SHA-256 hash

    Returns:
        True if the token matches, False otherwise
    """
    if not token or not token_hash:
        return False

    try:
        expected = hash_token_fast(token)
        return hmac.compare_digest(expected, token_hash)
    except Exception:
        return False


def is_sha256_hash(hash_value: str) -> bool:
    """
    Check if a hash is a SHA-256 hash (64 hex characters).

    Used to determine whether to use fast or slow verification during migration.

    Args:
        hash_value: The hash to check

    Returns:
        True if it looks like a SHA-256 hash
    """
    if not hash_value:
        return False
    # SHA-256 produces 64 hex characters, argon2 hashes start with $argon2
    return len(hash_value) == 64 and not hash_value.startswith("$")
