from src.processing.keyword_classifier import KeywordTopicClassifier


def test_keyword_classifier_military():
    classifier = KeywordTopicClassifier(
        topic_keywords={
            "Military": ["جيش", "قصف"],
            "Politics": ["انتخابات"],
        }
    )
    result = classifier.classify("الجيش نفذ قصفا على الهدف")
    assert result.topic == "Military"
    assert result.score >= 1


def test_keyword_classifier_uncategorized():
    classifier = KeywordTopicClassifier(topic_keywords={"Economy": ["اقتصاد"]})
    result = classifier.classify("نص لا يحتوي كلمات مطابقة")
    assert result.topic == "Uncategorized"
