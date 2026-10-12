"""The initial handshake of a box with the library (docs/developer/library.md, section 4.14).

Both sides prove that they know the bootstrap password without sending it: each sends a random number, and each answers
with a checksum that can only be computed with the password. Both checksums cover the certificate of the library as
the box sees it, so a program between the two, which has to show a certificate of its own, makes both proofs fail.
"""

import hashlib
import hmac
import secrets
import ssl

__all__ = ["fingerprint", "hash_secret", "new_nonce", "new_secret", "proof", "same"]


def new_nonce() -> str:
    return secrets.token_hex(16)


def new_secret() -> str:
    """The key of one box: a long random secret."""
    return secrets.token_urlsafe(48)


def hash_secret(secret: str) -> str:
    """What the library stores of the key of a box. The secret is long and random, so a plain hash is enough."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def fingerprint(certificate: str) -> str:
    """SHA-256 of a certificate (PEM)."""
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()


def proof(password: str, role: str, box: str, nonce_box: str, nonce_library: str, certificate: str) -> str:
    """Checksum that the side ``role`` ("box" or "library") can only compute with the bootstrap password."""
    message = "\n".join([role, box, nonce_box, nonce_library, fingerprint(certificate)])
    return hmac.new(password.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
