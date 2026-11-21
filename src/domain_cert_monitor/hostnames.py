"""Normalisation and validation of user-entered hostnames."""

import ipaddress
import re

import idna

MAX_NAME_LENGTH = 253
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class InvalidHostname(ValueError):
    """The input is not a valid hostname. The message is shown to the user."""


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _strip_url_parts(text: str) -> str:
    """Remove scheme, path, query, fragment, userinfo and port."""
    if "://" in text:
        text = text.split("://", 1)[1]
    text = re.split(r"[/?#]", text, maxsplit=1)[0]
    text = text.rpartition("@")[2]
    if text.startswith("[") or text.count(":") > 1:
        raise InvalidHostname("IP addresses are not supported; enter a hostname.")
    host, sep, port = text.partition(":")
    if sep and not port.isdigit():
        raise InvalidHostname(f"Invalid port in {text!r}.")
    return host


def normalise(raw: str) -> str:
    """Return the lower-case ASCII (punycode) hostname for ``raw``.

    Strips scheme, userinfo, path, query, fragment, port and a trailing dot.
    Raises ``InvalidHostname`` on bad input.
    """
    text = _strip_url_parts(raw.strip())
    if text.endswith("."):
        text = text[:-1]
    if not text:
        raise InvalidHostname("Please enter a hostname, e.g. example.com.")
    if _is_ip(text):
        raise InvalidHostname("IP addresses are not supported; enter a hostname.")

    if text.isascii():
        name = text.lower()
    else:
        try:
            name = idna.encode(text, uts46=True).decode("ascii")
        except (idna.IDNAError, UnicodeError) as exc:
            raise InvalidHostname(f"{raw.strip()!r} is not a valid hostname.") from exc

    labels = name.split(".")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidHostname(f"Hostname is longer than {MAX_NAME_LENGTH} characters.")
    if not all(_LABEL_RE.match(label) for label in labels):
        raise InvalidHostname(
            f"{raw.strip()!r} is not a valid hostname. Use letters, digits, hyphens and "
            "dots; each part 1-63 characters, not starting or ending with a hyphen."
        )
    if len(labels) < 2:
        raise InvalidHostname(f"{raw.strip()!r} needs a domain ending, e.g. example.com.")
    if labels[-1].isdigit():
        raise InvalidHostname(f"{raw.strip()!r} is not a valid hostname.")
    return name
