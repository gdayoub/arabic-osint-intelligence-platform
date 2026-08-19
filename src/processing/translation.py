"""Arabic → English machine translation with a content-addressed cache (M5).

Two pieces, deliberately separate:

  * `Translator` — a Protocol with one method. `DeepLTranslator` talks to
    the API; `EchoTranslator` is the test double, so the whole test suite
    runs with no API key and no network.
  * `translate_texts()` — the caching layer. Checks the `translations` table
    first, only sends genuine cache misses to the API, and writes results
    back under an extractor version (P4).

The brief's constraint on LLM APIs is about *extraction* — inferring facts
from text before a non-LLM baseline exists. Translation isn't extraction:
it's a rendering of text that already exists, and M5 plans for it
explicitly. It's still recorded as machine-generated, versioned output, not
presented as ground truth.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

import requests

from src.config.settings import SETTINGS, Settings

logger = logging.getLogger("processing.translation")

EXTRACTOR_NAME = "deepl_translator"
EXTRACTOR_VERSION = "1.0.0"

SOURCE_LANG = "AR"
TARGET_LANG = "EN-US"

# DeepL caps a single request; batching well under it keeps requests small
# enough to retry cheaply and keeps one bad string from failing 500 others.
_BATCH_SIZE = 40


def source_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Translator(Protocol):
    def translate(self, texts: list[str]) -> list[str]:
        """Translate a batch, returning one result per input, same order."""
        ...


class EchoTranslator:
    """Test/offline double. Returns the input marked, so a test can assert
    translation *happened* without asserting on a real translation's wording.
    """

    def translate(self, texts: list[str]) -> list[str]:
        return [f"[en] {text}" for text in texts]


class DeepLTranslator:
    """DeepL API client.

    Free-tier keys end in ':fx' and must go to api-free.deepl.com; paid keys
    go to api.deepl.com. Sending a free key to the paid host returns a
    confusing 403 rather than a helpful error, so the host is derived from
    the key instead of being configured separately.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        host = "api-free.deepl.com" if api_key.endswith(":fx") else "api.deepl.com"
        self._url = f"https://{host}/v2/translate"

    def translate(self, texts: list[str]) -> list[str]:
        if not texts:
            return []

        response = requests.post(
            self._url,
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            data={"text": texts, "source_lang": SOURCE_LANG, "target_lang": TARGET_LANG},
            timeout=SETTINGS.request_timeout_seconds,
        )
        response.raise_for_status()
        return [item["text"] for item in response.json()["translations"]]


def get_translator(settings: Settings | None = None) -> Translator:
    settings = settings or SETTINGS
    if not settings.deepl_api_key:
        raise RuntimeError("DEEPL_API_KEY is not set; cannot translate")
    return DeepLTranslator(settings.deepl_api_key)


def batched(items: list[str], size: int = _BATCH_SIZE) -> list[list[str]]:
    """Split a list into fixed-size chunks.

    itertools.batched() would do this in one call, but it's Python 3.12+ and
    this project targets 3.11 — so a plain slice loop, which is also easier
    to read than the islice-based recipe.
    """
    return [items[i : i + size] for i in range(0, len(items), size)]
