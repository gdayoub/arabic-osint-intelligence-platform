"""Network-free tests for the deployed-route smoke assertions."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import requests

from scripts.smoke_public_urls import (
    CLOUDFLARE_DATA_URL,
    PORTFOLIO_API_URL,
    PORTFOLIO_DASHBOARD_URL,
    PublicSmokeFailure,
    run_smoke,
)

CONTRACT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "dashboard"
    / "1.0.0"
    / "dashboard.fixture.json"
)


def _response(
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    payload=None,  # noqa: ANN001
    text_body: str | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    if payload is not None:
        response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        response.headers.setdefault("Content-Type", "application/json")
    else:
        response._content = (text_body or "").encode("utf-8")
    response.encoding = "utf-8"
    return response


class FakeSession:
    def __init__(self, responses: dict[str, requests.Response]):
        self.responses = responses

    def get(self, url: str, **_kwargs) -> requests.Response:
        return self.responses[url]


def _valid_responses() -> dict[str, requests.Response]:
    snapshot = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    projection = {
        "stats": copy.deepcopy(snapshot["stats"]),
        "recent": copy.deepcopy(snapshot["recent"][:5]),
        "topics": copy.deepcopy(snapshot["topics"]),
    }
    return {
        CLOUDFLARE_DATA_URL: _response(
            headers={"Access-Control-Allow-Origin": "*"}, payload=snapshot
        ),
        PORTFOLIO_DASHBOARD_URL: _response(
            headers={"Content-Type": "text/html; charset=utf-8"},
            text_body=f"<script>fetch('{CLOUDFLARE_DATA_URL}')</script>",
        ),
        PORTFOLIO_API_URL: _response(payload=projection),
    }


def test_public_smoke_accepts_the_contract_and_exact_portfolio_path():
    run_smoke(FakeSession(_valid_responses()), timeout=1)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda responses: responses[CLOUDFLARE_DATA_URL].headers.pop(
                "Access-Control-Allow-Origin"
            ),
            "does not allow",
        ),
        (
            lambda responses: setattr(
                responses[PORTFOLIO_DASHBOARD_URL], "_content", b"<html></html>"
            ),
            "no longer points",
        ),
        (
            lambda responses: setattr(
                responses[PORTFOLIO_API_URL],
                "_content",
                json.dumps({"error": "upstream error"}).encode("utf-8"),
            ),
            "missing stats",
        ),
    ],
)
def test_public_smoke_fails_on_each_consumer_boundary(mutate, message):  # noqa: ANN001
    responses = _valid_responses()
    mutate(responses)

    with pytest.raises(PublicSmokeFailure, match=message):
        run_smoke(FakeSession(responses), timeout=1)
