"""Verify the deployed snapshot and the portfolio routes that consume it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping

import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.contracts.dashboard import DashboardSnapshot

CLOUDFLARE_DATA_URL = (
    "https://arabic-osint-dashboard.georgedayoub500.workers.dev/data.json"
)
PORTFOLIO_ORIGIN = "https://george-dayoub-portfolio.vercel.app"
PORTFOLIO_DASHBOARD_URL = f"{PORTFOLIO_ORIGIN}/osint-dashboard.html"
PORTFOLIO_API_URL = f"{PORTFOLIO_ORIGIN}/api/osint"


class PublicSmokeFailure(RuntimeError):
    """Raised when one public compatibility assertion fails."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicSmokeFailure(message)


def _response_json(response: requests.Response, label: str) -> Any:
    try:
        return response.json()
    except requests.JSONDecodeError as exc:
        raise PublicSmokeFailure(f"{label} did not return valid JSON") from exc


def _validate_portfolio_projection(payload: Any) -> None:
    _require(isinstance(payload, Mapping), "portfolio API payload must be an object")
    stats = payload.get("stats")
    recent = payload.get("recent")
    topics = payload.get("topics")
    _require(isinstance(stats, Mapping), "portfolio API is missing stats")
    _require(
        isinstance(stats.get("total_raw"), int)
        and not isinstance(stats.get("total_raw"), bool),
        "portfolio API stats.total_raw must be an integer",
    )
    _require(isinstance(recent, list), "portfolio API recent must be an array")
    _require(isinstance(topics, Mapping), "portfolio API topics must be an object")
    _require(isinstance(topics.get("topics"), list), "portfolio API topics.topics must be an array")


def run_smoke(session: requests.Session, *, timeout: float = 20.0) -> None:
    snapshot_response = session.get(
        CLOUDFLARE_DATA_URL,
        headers={"Origin": PORTFOLIO_ORIGIN},
        timeout=timeout,
    )
    _require(snapshot_response.status_code == 200, "Cloudflare data.json is not HTTP 200")
    allowed_origin = snapshot_response.headers.get("Access-Control-Allow-Origin")
    _require(
        allowed_origin in {"*", PORTFOLIO_ORIGIN},
        "Cloudflare data.json does not allow the portfolio origin",
    )
    DashboardSnapshot.model_validate(
        _response_json(snapshot_response, "Cloudflare data.json")
    )

    dashboard_response = session.get(PORTFOLIO_DASHBOARD_URL, timeout=timeout)
    _require(dashboard_response.status_code == 200, "portfolio dashboard is not HTTP 200")
    content_type = dashboard_response.headers.get("Content-Type", "").lower()
    _require("text/html" in content_type, "portfolio dashboard is not HTML")
    _require(
        CLOUDFLARE_DATA_URL in dashboard_response.text,
        "portfolio dashboard no longer points at the Cloudflare snapshot",
    )

    api_response = session.get(PORTFOLIO_API_URL, timeout=timeout)
    _require(api_response.status_code == 200, "portfolio /api/osint is not HTTP 200")
    _validate_portfolio_projection(_response_json(api_response, "portfolio API"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    try:
        with requests.Session() as session:
            run_smoke(session, timeout=args.timeout)
    except (PublicSmokeFailure, requests.RequestException, ValueError) as exc:
        print(f"public smoke FAILED: {exc}")
        return 1

    print("public smoke passed: Cloudflare snapshot and exact Vercel routes are compatible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
