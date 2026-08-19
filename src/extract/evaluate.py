"""scoring an extractor against hand labelled text.

precision is of everything i found how much was right.
recall is of everything that was there how much did i find.
f1 is the harmonic mean so a model cannot game it by being good at one.

why harmonic and not a plain average. an extractor that tags every word as
a person gets recall 1.0 and precision near zero. plain average gives it
0.5 which looks respectable. harmonic mean gives it near zero which is the
honest answer.

what counts as a hit. i use exact span match. the predicted start and end
and type all have to equal a gold one. partial credit for overlapping spans
exists in the literature but exact match is stricter and easier to defend
and i would rather my numbers be pessimistic than flattering.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.extract.base import ExtractedMention


@dataclass(frozen=True, slots=True)
class Score:
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        found = self.true_positives + self.false_positives
        return self.true_positives / found if found else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _key(mention: ExtractedMention) -> tuple[int, int, str]:
    return (mention.start, mention.end, mention.object_type)


def score_document(
    predicted: list[ExtractedMention], gold: list[ExtractedMention]
) -> Score:
    predicted_keys = {_key(m) for m in predicted}
    gold_keys = {_key(m) for m in gold}
    return Score(
        true_positives=len(predicted_keys & gold_keys),
        false_positives=len(predicted_keys - gold_keys),
        false_negatives=len(gold_keys - predicted_keys),
    )


def score_corpus(
    pairs: list[tuple[list[ExtractedMention], list[ExtractedMention]]]
) -> Score:
    """add up the counts across documents then compute the ratios once.

    this is micro averaging. the alternative is to score each document then
    average the f1 values which is macro averaging and weights a one entity
    document the same as a fifty entity one. micro is the right call when i
    care about total mentions extracted.
    """
    tp = fp = fn = 0
    for predicted, gold in pairs:
        s = score_document(predicted, gold)
        tp += s.true_positives
        fp += s.false_positives
        fn += s.false_negatives
    return Score(tp, fp, fn)


def score_by_type(
    pairs: list[tuple[list[ExtractedMention], list[ExtractedMention]]]
) -> dict[str, Score]:
    """same thing but split per object type.

    an aggregate f1 hides that i might be great at locations and useless at
    people. the brief asks for per type numbers for exactly that reason.
    """
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])

    for predicted, gold in pairs:
        predicted_keys = {_key(m) for m in predicted}
        gold_keys = {_key(m) for m in gold}

        for key in predicted_keys | gold_keys:
            object_type = key[2]
            in_pred = key in predicted_keys
            in_gold = key in gold_keys
            if in_pred and in_gold:
                counts[object_type][0] += 1
            elif in_pred:
                counts[object_type][1] += 1
            else:
                counts[object_type][2] += 1

    return {t: Score(*c) for t, c in counts.items()}


def disagreements(
    predicted: list[ExtractedMention], gold: list[ExtractedMention]
) -> tuple[list[ExtractedMention], list[ExtractedMention]]:
    """what i found that was not there and what i missed.

    this is the CLI output the brief asks for. an f1 number tells me how bad
    things are and this tells me why.
    """
    gold_keys = {_key(m) for m in gold}
    predicted_keys = {_key(m) for m in predicted}
    spurious = [m for m in predicted if _key(m) not in gold_keys]
    missed = [m for m in gold if _key(m) not in predicted_keys]
    return spurious, missed
