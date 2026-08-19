"""Tests for src/processing/country_detection.py."""

from __future__ import annotations

from src.processing.country_detection import detect_country


def test_most_mentioned_country_wins_not_first_match():
    """The regression this module exists for.

    A real headline from the live dashboard — the Dabaa nuclear plant, which
    is in Egypt — was labelled Syria by the old first-match-wins logic
    because Syria appeared once in passing and came earlier in the keyword
    dict. Egypt is mentioned more, so Egypt should win.
    """
    text = (
        "الضبعة محطة نووية تحت الإنشاء في مصر تثير الجدل. "
        "وقالت الحكومة المصرية إن المشروع في مصر يسير وفق الجدول الزمني، "
        "على عكس ما جرى في سوريا."
    )
    result = detect_country(text)

    assert result.country == "Egypt"
    assert result.counts["Egypt"] > result.counts["Syria"]


def test_returns_none_when_no_country_mentioned():
    result = detect_country("اجتمع الوزراء لمناقشة الملف الاقتصادي العام.")
    assert result.country is None
    assert result.counts == {}


def test_empty_text_is_safe():
    assert detect_country("").country is None


def test_definite_article_and_attached_prefixes_are_matched():
    """Arabic attaches و/ف/ب/ل/ك and ال directly to the word, so a bare
    substring match would miss most real occurrences."""
    for phrase in ("العراق", "بالعراق", "والعراق", "عراق", "للعراق"):
        assert detect_country(f"تقرير عن {phrase} اليوم").country == "Iraq", phrase


def test_does_not_match_country_name_inside_a_longer_word():
    """مصر (Egypt) is a substring of مصرف (bank). Without boundary handling
    every article about banking would be tagged Egypt."""
    result = detect_country("أعلن المصرف المركزي عن سياسة نقدية جديدة لدعم المصارف.")
    assert "Egypt" not in result.counts


def test_hamza_spelling_variants_are_unified():
    """إسرائيل and اسرائيل differ only by hamza; normalization collapses
    them so both spellings count toward the same country."""
    with_hamza = detect_country("تقرير من إسرائيل")
    without_hamza = detect_country("تقرير من اسرائيل")
    assert with_hamza.country == "Israel"
    assert without_hamza.country == "Israel"


def test_capital_cities_count_toward_their_country():
    assert detect_country("وصل الوفد إلى طهران أمس.").country == "Iran"
    assert detect_country("عقد الاجتماع في بيروت.").country == "Lebanon"


def test_counts_expose_secondary_countries():
    """The full tally is returned, not just the winner — country pages and
    later milestones need to know a document touched more than one place."""
    text = "محادثات بين ايران وامريكا وامريكا تصر على شروطها في طهران"
    result = detect_country(text)

    assert set(result.counts) >= {"Iran", "United States"}
