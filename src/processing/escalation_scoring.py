"""Rule-based escalation scoring for geopolitical incident monitoring."""

from __future__ import annotations

from dataclasses import dataclass

HIGH_ESCALATION = {
    "قصف",
    "غارة",
    "اشتباك",
    "هجوم",
    "صاروخ",
    "انفجار",
    "مقتل",
    "اجتياح",
}

MEDIUM_ESCALATION = {
    "توتر",
    "تهديد",
    "حشد",
    "عقوبات",
    "تحذير",
    "تصعيد",
}

LOW_ESCALATION = {
    "اجتماع",
    "محادثات",
    "وساطة",
    "اتفاق",
    "تصريح",
    "زيارة",
}


@dataclass(slots=True)
class EscalationResult:
    label: str
    score: int
    matches: dict[str, list[str]]


def score_escalation(text: str) -> EscalationResult:
    """Estimate geopolitical escalation level from lexical cues."""
    content = text or ""

    high_hits = [kw for kw in HIGH_ESCALATION if kw in content]
    med_hits = [kw for kw in MEDIUM_ESCALATION if kw in content]
    low_hits = [kw for kw in LOW_ESCALATION if kw in content]

    score = (len(high_hits) * 3) + (len(med_hits) * 2) + (len(low_hits) * -1)

    if len(high_hits) >= 2 or score >= 6:
        label = "high"
    elif len(high_hits) >= 1 or score >= 2:
        label = "medium"
    else:
        label = "low"

    return EscalationResult(
        label=label,
        score=score,
        matches={
            "high": sorted(high_hits),
            "medium": sorted(med_hits),
            "low": sorted(low_hits),
        },
    )
