"""small, explicit helpers for language-owned source-text segmentation.

the helpers never normalize, rebuild, or concatenate document text.  They
return ``TextSpan`` objects whose text was sliced directly from the input, so
the offsets stay usable for P2 provenance checks.

paragraph detection is intentionally conservative: only a blank line is an
explicit paragraph boundary.  Most current scraper output is flattened into
one string, and punctuation or visual line wrapping is not evidence that a
paragraph existed in that source representation.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from src.lang.base import TextSpan

# A blank line is one line ending, optional horizontal whitespace, then at
# least one more line ending.  A single newline stays inside the paragraph:
# it may be a source line wrap rather than semantic structure.
_PARAGRAPH_BREAK = re.compile(r"(?:\r\n|\r|\n)(?:[^\S\r\n]*(?:\r\n|\r|\n))+")

# These characters commonly follow a terminal mark but belong to the same
# visible sentence.  They must be included before the next span starts.
_CLOSING_MARKS = frozenset('"\'”’»)]}')


def segment_explicit_paragraphs(text: str) -> list[TextSpan]:
    """return blocks separated by explicit blank lines in ``text``.

    No punctuation heuristic appears here.  If a scraper supplied flattened
    text with ordinary spaces, the result is exactly one paragraph span.
    Outer whitespace is excluded because it cannot contain a mention, but
    internal whitespace—including a single source newline—is untouched.
    """

    spans: list[TextSpan] = []
    block_start = 0

    for boundary in _PARAGRAPH_BREAK.finditer(text):
        _append_trimmed_span(spans, text, block_start, boundary.start())
        block_start = boundary.end()

    _append_trimmed_span(spans, text, block_start, len(text))
    return spans


def segment_sentences(
    text: str,
    *,
    terminal_marks: frozenset[str],
    is_terminal: Callable[[str, int], bool],
) -> list[TextSpan]:
    """split explicitly bounded paragraphs with one language's rules.

    Paragraph breaks are hard sentence boundaries too.  That does not infer a
    paragraph from layout; it only prevents an unfinished sentence in one
    explicitly supplied paragraph from swallowing the next one.
    """

    spans: list[TextSpan] = []

    for paragraph in segment_explicit_paragraphs(text):
        sentence_start = paragraph.start
        cursor = paragraph.start

        while cursor < paragraph.end:
            if is_terminal(text, cursor):
                sentence_end = _consume_terminal_and_closers(
                    text,
                    cursor,
                    paragraph.end,
                    terminal_marks,
                )
                spans.append(TextSpan.from_source(text, sentence_start, sentence_end))
                sentence_start = _skip_whitespace(text, sentence_end, paragraph.end)
                cursor = sentence_start
                continue
            cursor += 1

        # News copy sometimes has no terminal punctuation.  It is still a
        # usable bounded evidence sentence, ending at the explicit paragraph
        # boundary rather than being discarded or merged with another one.
        _append_trimmed_span(spans, text, sentence_start, paragraph.end)

    return spans


def _append_trimmed_span(
    spans: list[TextSpan], text: str, start: int, end: int
) -> None:
    start = _skip_whitespace(text, start, end)
    end = _trim_trailing_whitespace(text, start, end)
    if start < end:
        spans.append(TextSpan.from_source(text, start, end))


def _skip_whitespace(text: str, start: int, end: int) -> int:
    while start < end and text[start].isspace():
        start += 1
    return start


def _trim_trailing_whitespace(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1].isspace():
        end -= 1
    return end


def _consume_terminal_and_closers(
    text: str,
    start: int,
    limit: int,
    terminal_marks: frozenset[str],
) -> int:
    """include ``?!...`` runs and their closing quote/parenthesis marks."""

    end = start
    while end < limit and text[end] in terminal_marks:
        end += 1
    while end < limit and text[end] in _CLOSING_MARKS:
        end += 1
    return end
