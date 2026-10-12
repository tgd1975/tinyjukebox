"""Self signed certificate for the rest api - created with the openssl command line tool on first start."""

import hashlib
import logging
import os
import socket
import ssl
import subprocess
from collections.abc import Iterable

__all__ = ["create_self_signed_certificate", "create_ssl_context", "ensure_certificate", "fingerprint"]


def _is_ip(name: str) -> bool:
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, name)
        except OSError:
            continue
        return True
    return False


def _subject_alt_names(hostnames: Iterable[str] | None) -> str:
    names = ["localhost", socket.gethostname(), socket.gethostname() + ".local", *(hostnames or [])]
    entries: list[str] = []
    for name in [*names, "127.0.0.1"]:
        entry = f"{'IP' if _is_ip(name) else 'DNS'}:{name}"
        if entry not in entries:
            entries.append(entry)
    return ",".join(entries)


def _create_private_file(filename: str) -> None:
    """Empty file with mode 600 - openssl writes the key into it, so the key is never readable by others.

    (The previous version changed the umask of the whole process, which affected files created by other threads.)
    """
    if os.path.exists(filename):
        os.chmod(filename, 0o600)
    else:
        os.close(os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))


def create_self_signed_certificate(
    certificate_file: str, key_file: str, hostnames: Iterable[str] | None = None, days: int = 3650
) -> None:
    """Create an ec (prime256v1) key and a self signed certificate for this host (valid for ``days`` days).

    :raises RuntimeError: if openssl is missing or fails.
    """
    for filename in (certificate_file, key_file):
        os.makedirs(os.path.dirname(os.path.abspath(filename)), mode=0o700, exist_ok=True)
    _create_private_file(key_file)
    try:
        result = subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:prime256v1",
                "-nodes",
                "-sha256",
                "-days",
                str(days),
                "-subj",
                f"/CN={socket.gethostname()}/O=Tiny Jukebox",
                "-addext",
                f"subjectAltName={_subject_alt_names(hostnames)}",
                "-keyout",
                key_file,
                "-out",
                certificate_file,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("openssl not found - install openssl or provide certificate and private key.") from None
    if result.returncode != 0:
        raise RuntimeError(f"creating the certificate failed: {result.stderr.strip()}")
    os.chmod(key_file, 0o600)


def fingerprint(certificate_file: str) -> str:
    """SHA-256 fingerprint as shown by browsers (AA:BB:...)."""
    with open(certificate_file, encoding="ascii") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def ensure_certificate(
    certificate_file: str, key_file: str, logger: logging.Logger, hostnames: Iterable[str] | None = None
) -> str:
    """Create certificate and key if one of them is missing. Returns the fingerprint of the certificate."""
    if not (os.path.exists(certificate_file) and os.path.exists(key_file)):
        logger.info("creating self signed certificate '%s'.", certificate_file)
        create_self_signed_certificate(certificate_file, key_file, hostnames)
    return fingerprint(certificate_file)


def create_ssl_context(certificate_file: str, key_file: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate_file, key_file)
    return context
