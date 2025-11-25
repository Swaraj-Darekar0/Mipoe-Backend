import os, base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from datetime import datetime

def _get_key(app):
    key_str = app.config['TOKEN_CRYPT_KEY']
    # assume hex or base64 length >32; if not, pad/encode
    if len(key_str) == 64:  # hex 32 bytes
        return bytes.fromhex(key_str)
    try:
        return base64.b64decode(key_str)
    except Exception:
        # fallback: utf-8 bytes padded to 32 bytes
        return key_str.encode().ljust(32, b'0')[:32]

def encrypt_token(app, plaintext: str) -> bytes:
    key = _get_key(app)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return nonce + ct

def decrypt_token(app, blob: bytes) -> str:
    key = _get_key(app)
    aesgcm = AESGCM(key)
    nonce, ct = blob[:12], blob[12:]
    return aesgcm.decrypt(nonce, ct, None).decode() 