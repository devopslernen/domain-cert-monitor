"""TLS certificate check: connect to ``hostname:443`` and read the leaf certificate's expiry."""

import asyncio
import socket
import ssl
import time

from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from . import CheckResult

PORT = 443
TIMEOUT = 10.0

# OpenSSL X509_V_ERR_* codes with friendlier messages.
_VERIFY_MESSAGES = {
    10: "certificate expired",
    9: "certificate not yet valid",
    18: "self-signed certificate",
    19: "self-signed certificate in chain",
    20: "untrusted certificate chain (unable to get local issuer certificate)",
    21: "untrusted certificate chain (unable to verify the first certificate)",
    62: "hostname mismatch",
}


class _CheckError(Exception):
    """No certificate date could be obtained; the message becomes ``CheckResult.error``."""


def _connect(hostname: str, port: int, deadline: float) -> tuple[socket.socket, str]:
    """TCP connection to the first address the resolver returns."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _CheckError(f"DNS lookup failed: {exc.strerror or exc}") from exc
    family, type_, proto, _, addr = infos[0]
    sock = socket.socket(family, type_, proto)
    try:
        sock.settimeout(max(0.001, deadline - time.monotonic()))
        sock.connect(addr)
    except BaseException:
        sock.close()
        raise
    return sock, addr[0]


def _handshake(
    hostname: str, port: int, context: ssl.SSLContext, deadline: float
) -> tuple[bytes, str]:
    sock, ip = _connect(hostname, port, deadline)
    try:
        sock.settimeout(max(0.001, deadline - time.monotonic()))
        with context.wrap_socket(sock, server_hostname=hostname) as tls:
            der = tls.getpeercert(binary_form=True)
    finally:
        sock.close()
    if not der:
        raise _CheckError("server sent no certificate")
    return der, ip


def _names(name: x509.Name) -> str:
    return name.rfc4514_string()


def _check_sync(
    hostname: str, port: int, timeout: float, verify_context: ssl.SSLContext
) -> CheckResult:
    deadline = time.monotonic() + timeout
    details: dict = {"verified": None, "verify_error": None, "verify_code": None}
    try:
        try:
            der, ip = _handshake(hostname, port, verify_context, deadline)
            details["verified"] = True
        except ssl.SSLCertVerificationError as exc:
            details["verified"] = False
            details["verify_code"] = exc.verify_code
            details["verify_error"] = _VERIFY_MESSAGES.get(
                exc.verify_code, exc.verify_message or str(exc)
            )
            # Read the leaf certificate anyway, without verification.
            unverified = ssl.create_default_context()
            unverified.check_hostname = False
            unverified.verify_mode = ssl.CERT_NONE
            der, ip = _handshake(hostname, port, unverified, deadline)
    except _CheckError as exc:
        return CheckResult(kind="cert", error=str(exc), details=details)
    except ConnectionRefusedError:
        return CheckResult(kind="cert", error=f"connection refused on port {port}", details=details)
    except TimeoutError:
        return CheckResult(kind="cert", error=f"timed out after {timeout:g} s", details=details)
    except ssl.SSLError as exc:
        reason = exc.reason or exc.strerror or str(exc)
        return CheckResult(kind="cert", error=f"TLS handshake failed: {reason}", details=details)
    except OSError as exc:
        return CheckResult(
            kind="cert", error=f"connection failed: {exc.strerror or exc}", details=details
        )

    cert = x509.load_der_x509_certificate(der)
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        sans = san.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    details.update(
        subject=_names(cert.subject),
        issuer=_names(cert.issuer),
        sans=sans,
        ip=ip,
    )
    return CheckResult(kind="cert", expires_at=cert.not_valid_after_utc, details=details)


async def check_cert(
    hostname: str,
    *,
    port: int = PORT,
    timeout: float = TIMEOUT,
    verify_context: ssl.SSLContext | None = None,
) -> CheckResult:
    """Check the certificate of ``hostname:port``.

    ``expires_at`` is the leaf certificate's ``notAfter`` even if verification fails;
    ``details["verified"]`` and ``details["verify_error"]`` record the verification
    outcome. ``error`` is set only when no certificate could be read at all (DNS,
    refused, timeout, handshake failure). Connect and handshake share ``timeout``.
    ``verify_context`` replaces the default verifying context (e.g. a test CA).
    ``checked_at`` is left for the caller to set from its clock.
    """
    context = verify_context or ssl.create_default_context()
    return await asyncio.to_thread(_check_sync, hostname, port, timeout, context)
