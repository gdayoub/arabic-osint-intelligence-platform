"""Claude API-powered intelligence summary generator.

Only runs when ANTHROPIC_API_KEY is set in the environment.
Only processes articles with high or medium escalation to limit API usage.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("processing.ai_summarizer")

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            import anthropic
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed; AI summaries disabled")
            return None
    return _client


def generate_summary(title: str, cleaned_text: str, escalation: str) -> Optional[str]:
    """Generate a 2-3 sentence English intelligence summary for high/medium escalation articles.

    Returns None silently if:
    - ANTHROPIC_API_KEY is not set
    - escalation level is low or unknown
    - any API error occurs
    """
    if escalation not in ("high", "medium"):
        return None

    client = _get_client()
    if client is None:
        return None

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are an Arabic geopolitical intelligence analyst. "
                        "Write a concise 2-3 sentence intelligence summary in English "
                        "of the following Arabic news article. Focus on who, what, where, "
                        "and the strategic significance. Be direct and factual.\n\n"
                        f"Title: {title}\n\n"
                        f"Content: {cleaned_text[:1500]}"
                    ),
                }
            ],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.warning("AI summarization failed: %s", exc)
        return None
