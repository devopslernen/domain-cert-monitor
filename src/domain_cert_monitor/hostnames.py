"""Normalisation and validation of user-entered hostnames."""


class InvalidHostname(ValueError):
    """The input is not a valid hostname. The message is shown to the user."""


def normalise(raw: str) -> str:
    """Return the lower-case ASCII (punycode) hostname for ``raw``.

    Strips scheme, userinfo, path, query, fragment, port and a trailing dot.
    Raises ``InvalidHostname`` on bad input.
    """
    raise NotImplementedError
