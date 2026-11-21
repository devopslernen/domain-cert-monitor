"""hostnames.normalise: acceptance criterion 5 and the rules in ARCHITECTURE.md §4."""

import pytest

from domain_cert_monitor.hostnames import InvalidHostname, normalise


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # criterion 5
        ("https://Example.com/path", "example.com"),
        ("example.com", "example.com"),
        ("  example.com\t\n", "example.com"),
        ("EXAMPLE.COM", "example.com"),
        ("example.com.", "example.com"),
        ("http://example.com", "example.com"),
        ("https://example.com/", "example.com"),
        ("example.com:443", "example.com"),
        ("https://example.com:8443/a/b?c=d#e", "example.com"),
        ("https://user:secret@example.com/", "example.com"),
        ("example.com/path/only", "example.com"),
        ("example.com?q=1", "example.com"),
        ("www.Example.co.uk", "www.example.co.uk"),
        ("expired.badssl.com", "expired.badssl.com"),
        ("my-host.example.com", "my-host.example.com"),
        ("123.example.com", "123.example.com"),
        # IDN -> punycode (UTS46)
        ("bücher.de", "xn--bcher-kva.de"),
        ("BÜCHER.de", "xn--bcher-kva.de"),
        ("https://www.Bücher.de/shop", "www.xn--bcher-kva.de"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("例え.jp", "xn--r8jz45g.jp"),
    ],
)
def test_normalise_valid(raw, expected):
    assert normalise(raw) == expected


def _name_of_length(n: int) -> str:
    labels = []
    remaining = n
    while remaining > 0:
        size = min(63, remaining)
        labels.append("a" * size)
        remaining -= size + 1  # +1 for the dot
    name = ".".join(labels)
    assert len(name) == n
    return name


def test_name_of_253_characters_is_accepted():
    name = _name_of_length(253)
    assert normalise(name) == name


def test_label_of_63_characters_is_accepted():
    name = "a" * 63 + ".com"
    assert normalise(name) == name


@pytest.mark.parametrize(
    "raw",
    [
        # criterion 5
        "not a domain",
        "",
        "   ",
        "https://",
        # one label only
        "localhost",
        "example",
        "example.",
        # IP literals
        "192.168.0.1",
        "https://192.168.0.1/",
        "8.8.8.8:443",
        "::1",
        "[::1]",
        "https://[2001:db8::1]:443/",
        # all-digit TLD
        "example.123",
        # not LDH
        "exa_mple.com",
        "exa mple.com",
        "example!.com",
        "-example.com",
        "example-.com",
        # empty labels
        "example..com",
        ".example.com",
        # length limits
        "a" * 64 + ".com",
        _name_of_length(254),
    ],
)
def test_normalise_invalid(raw):
    with pytest.raises(InvalidHostname):
        normalise(raw)


def test_invalid_hostname_has_a_message():
    with pytest.raises(InvalidHostname) as exc_info:
        normalise("not a domain")
    assert str(exc_info.value).strip()
