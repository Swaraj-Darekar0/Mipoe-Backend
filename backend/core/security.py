from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from backend.core.config import get_settings


password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
)
settings = get_settings()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_token(subject: str, role: str, username: str, token_type: str, expires_delta: timedelta, extra_claims: dict | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "username": username,
        "type": token_type,
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, role: str, username: str, extra_claims: dict | None = None) -> str:
    return create_token(
        subject=subject,
        role=role,
        username=username,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims=extra_claims,
    )


def create_refresh_token(subject: str, role: str, username: str, extra_claims: dict | None = None) -> str:
    return create_token(
        subject=subject,
        role=role,
        username=username,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
        extra_claims=extra_claims,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def encrypt_data(data: str, key: str | None = None) -> str:
    """Symmetrically encrypt text using a hashed version of the system crypt key."""
    if not data:
        return ""
    from cryptography.fernet import Fernet
    import base64
    import hashlib
    
    crypt_key = key or settings.token_crypt_key or settings.secret_key or "default_mipoe_crypt_key"
    hashed_key = hashlib.sha256(crypt_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(hashed_key)
    f = Fernet(fernet_key)
    return f.encrypt(data.encode()).decode()


def decrypt_data(encrypted_data: str, key: str | None = None) -> str:
    """Symmetrically decrypt cipher text using a hashed version of the system crypt key."""
    if not encrypted_data:
        return ""
    from cryptography.fernet import Fernet
    from cryptography.fernet import InvalidToken
    import base64
    import hashlib
    
    try:
        crypt_key = key or settings.token_crypt_key or settings.secret_key or "default_mipoe_crypt_key"
        hashed_key = hashlib.sha256(crypt_key.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(hashed_key)
        f = Fernet(fernet_key)
        return f.decrypt(encrypted_data.encode()).decode()
    except (InvalidToken, Exception):
        return "[Decryption Error]"

