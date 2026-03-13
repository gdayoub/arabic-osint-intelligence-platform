from src.processing.normalize_arabic import normalize_arabic_text


def test_normalization_removes_diacritics_and_tatweel():
    text = "اَلْجَيْشُ يَتَحَرَّكُ ـــــ بسرعة"
    cleaned = normalize_arabic_text(text)
    assert "َ" not in cleaned
    assert "ـ" not in cleaned


def test_normalization_alef_variants():
    text = "أخبار وإعلان وآثار"
    cleaned = normalize_arabic_text(text)
    assert "أ" not in cleaned
    assert "إ" not in cleaned
    assert "آ" not in cleaned
    assert "ا" in cleaned
