"""CLI entrypoint for pipeline orchestration and local operations."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from src.config.logging_config import setup_logging
from src.config.settings import SETTINGS
from src.database.db import init_db
from src.pipeline.ingest_pipeline import run_ingestion
from src.pipeline.process_pipeline import run_processing
from src.pipeline.run_pipeline import run_full_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arabic OSINT Platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create database schema via SQLAlchemy ORM")
    sub.add_parser("ingest", help="Run source scraping and load raw_articles")
    sub.add_parser("process", help="Process raw_articles into processed_articles")
    sub.add_parser("run-pipeline", help="Run ingestion + processing")
    sub.add_parser("dashboard", help="Launch Streamlit dashboard")

    return parser


def main() -> None:
    setup_logging(SETTINGS.log_level)
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
    elif args.command == "ingest":
        print(run_ingestion())
    elif args.command == "process":
        print(run_processing())
    elif args.command == "run-pipeline":
        print(run_full_pipeline())
    elif args.command == "dashboard":
        project_root = Path(__file__).resolve().parent
        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{project_root}{os.pathsep}{current_pythonpath}"
            if current_pythonpath
            else str(project_root)
        )
        subprocess.run(
            [
                "streamlit",
                "run",
                "src/dashboard/app.py",
                "--server.address=0.0.0.0",
                "--server.port=8501",
            ],
            check=True,
            cwd=project_root,
            env=env,
        )


if __name__ == "__main__":
    main()
