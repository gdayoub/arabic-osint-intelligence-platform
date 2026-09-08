"""Build a local UI preview from already-public data; does not run ingestion.

python scripts/preview_atlas.py
python -m http.server 8765 --bind 127.0.0.1 --directory preview
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import requests

BASE = "https://arabic-osint-dashboard.georgedayoub500.workers.dev"
ROOT = Path(__file__).resolve().parents[1]


def fetch(path):
    response = requests.get(BASE + "/" + path, timeout=30)
    response.raise_for_status()
    return response.json()


def main():
    out = ROOT / "preview"
    out.mkdir(exist_ok=True)
    source = ROOT / "src/api/static"
    for path in source.glob("*.html"):
        shutil.copy2(path, out / path.name)
    shutil.copy2(source / "dashboard.html", out / "index.html")
    shutil.copytree(source / "assets", out / "assets", dirs_exist_ok=True)
    index = fetch("data.json")
    (out / "data.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    country_dir = out / "countries"
    country_dir.mkdir(exist_ok=True)

    def country(item):
        payload = fetch("countries/" + item["slug"] + ".json")
        if payload["generated_at"] != index["generated_at"]:
            raise RuntimeError("Public snapshot changed during preview download; retry.")
        (country_dir / (item["slug"] + ".json")).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(pool.map(country, index["countries"]))
    try:
        atlas = fetch("countries/_atlas.json")
        if atlas["generated_at"] == index["generated_at"]:
            (country_dir / "_atlas.json").write_text(json.dumps(atlas, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # A legacy deployment has no complete archive. The UI labels its sample.
        (country_dir / "_atlas.json").unlink(missing_ok=True)

    # A bounded, reproducible example of real public headlines for the tour.
    # The explicit incomplete flag is intentional: this is never a full corpus.
    demo_articles = []
    for page in pages:
        rows = sorted(page["articles"], key=lambda a: ({"high": 0, "medium": 1, "low": 2}.get(a["escalation"], 3)))[:2]
        for article in rows:
            demo_articles.append({**article, "id": "example-" + str(len(demo_articles)), "country": page["country"], "collected_at": article.get("processed_at"), "entity_ids": []})
    demo_entities = []
    for object_type, rows in index["entities"]["top"].items():
        for row in rows:
            demo_entities.append({**row, "id": "example-entity-" + str(len(demo_entities)), "object_type": object_type, "name_en": None, "romanizations": []})
    demo = {"generated_at": index["generated_at"], "complete": False, "mode": "sample", "index": index,
            "articles": demo_articles, "entities": demo_entities,
            "classification": {"name": "rule_based_document_classifier", "version": "2.0.1"}}
    demo_path = source / "assets/demo.json"
    if not demo_path.exists():
        demo_path.write_text(json.dumps(demo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        shutil.copy2(demo_path, out / "assets/demo.json")
    print(f"Preview prepared at {out}; {len(pages)} country files. Serve on http://127.0.0.1:8765")


if __name__ == "__main__":
    main()
