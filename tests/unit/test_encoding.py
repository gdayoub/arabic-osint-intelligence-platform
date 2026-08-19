"""Tests for the response-encoding fix and mojibake detection.

The bug these guard against was real and reached production: CNN Arabic
headlines were stored as Cyrillic mojibake because chardet's guess was
preferred over the server's declared charset.
"""

from __future__ import annotations

import requests

from src.pipeline.retract_mojibake import is_mojibake
from src.scraping.base_scraper import _resolve_encoding

ARABIC = "إيران ترد على العقوبات الأمريكية الجديدة"


class FakeResponse:
    """Minimal stand-in for requests.Response.

    Only the three attributes _resolve_encoding touches. Building a real
    Response would mean constructing urllib3 internals for no added
    confidence.
    """

    def __init__(self, content: bytes, headers: dict[str, str], apparent: str | None = None) -> None:
        self.content = content
        self.headers = requests.structures.CaseInsensitiveDict(headers)
        self.apparent_encoding = apparent


def test_explicit_header_charset_is_authoritative():
    response = FakeResponse(
        ARABIC.encode("windows-1256"),
        {"Content-Type": "text/html; charset=windows-1256"},
        apparent="utf-8",
    )
    assert _resolve_encoding(response) == "windows-1256"


def test_utf8_wins_over_a_bad_chardet_guess():
    """The actual production bug: valid UTF-8 Arabic, chardet says Cyrillic."""
    response = FakeResponse(
        ARABIC.encode("utf-8"),
        {"Content-Type": "text/html"},  # no charset declared
        apparent="windows-1251",
    )
    assert _resolve_encoding(response) == "utf-8"


def test_requests_iso_8859_1_default_is_treated_as_absent():
    """requests substitutes ISO-8859-1 when text/* has no charset, which is
    a default rather than a real declaration and must not be trusted."""
    response = FakeResponse(
        ARABIC.encode("utf-8"),
        {"Content-Type": "text/html; charset=ISO-8859-1"},
        apparent="windows-1251",
    )
    assert _resolve_encoding(response) == "utf-8"


def test_falls_back_to_detection_when_bytes_are_not_utf8():
    response = FakeResponse(
        ARABIC.encode("windows-1256"),
        {"Content-Type": "text/html"},
        apparent="windows-1256",
    )
    assert _resolve_encoding(response) == "windows-1256"


def test_round_trip_produces_readable_arabic_not_cyrillic():
    """End-to-end proof: decode with the resolved encoding and the original
    Arabic comes back, with no Cyrillic anywhere."""
    raw = ARABIC.encode("utf-8")
    response = FakeResponse(raw, {"Content-Type": "text/html"}, apparent="windows-1251")

    decoded = raw.decode(_resolve_encoding(response))

    assert decoded == ARABIC
    assert not is_mojibake(decoded)


def test_mojibake_detection_flags_the_corrupted_form():
    corrupted = ARABIC.encode("utf-8").decode("windows-1251")
    assert is_mojibake(corrupted)


def test_mojibake_detection_ignores_clean_arabic_and_english():
    assert not is_mojibake(ARABIC)
    assert not is_mojibake("Trump postpones tariffs on Canadian goods")
    assert not is_mojibake("")


def test_a_single_cyrillic_mention_is_not_treated_as_corruption():
    """Arabic coverage of Russia may quote a Cyrillic name; that's not the
    bulk corruption this targets."""
    mostly_arabic = ARABIC * 4 + " Спутник"
    assert not is_mojibake(mostly_arabic)
