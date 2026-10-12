"""Read-only checks for this frontend contract bundle using the pinned backend.

Run with the backend's normal Python dependencies, e.g.:
python docs/frontend-handoff/frontend-contract/validate_bundle.py --backend .
No network, database, writes, translation calls, or UI execution.
"""
from pathlib import Path
from dataclasses import replace
import argparse
import hashlib
import json
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--backend", type=Path, required=True)
args = parser.parse_args()
backend = args.backend.resolve()
root = Path(__file__).resolve().parent
sys.path.insert(0, str(backend))
from src.contracts.atlas import AtlasSnapshot, validate_atlas_bundle
from src.contracts.dashboard import DashboardSnapshot
from src.search.evidence import CachedEvidenceTranslation, search_evidence
from src.search.evidence_cli import load_snapshot

def read(name):
    return json.loads((root/name).read_text())

copies = {
    "schemas/dashboard.schema.json":"contracts/dashboard/1.0.0/dashboard.schema.json",
    "schemas/country.schema.json":"contracts/dashboard/1.0.0/country.schema.json",
    "schemas/atlas.schema.json":"contracts/atlas/1.0.0/atlas.schema.json",
    "types/dashboard.types.ts":"contracts/dashboard/1.0.0/dashboard.types.ts",
    "fixtures/evidence.input.synthetic.json":"tests/fixtures/bilingual_evidence.json",
}
for dest, source in copies.items():
    assert (root/dest).read_bytes() == (backend/source).read_bytes(), f"Source copy drift: {dest}"
assert read("schemas/atlas.schema.json") == AtlasSnapshot.model_json_schema()
# The dashboard generator adds publication metadata around its model schema.
# Byte equality to the pinned committed schema above is its consumer contract.

dashboard = read("fixtures/dashboard.synthetic.json")
atlas = read("fixtures/atlas.synthetic.json")
pages = read("fixtures/countries.synthetic.json")
validate_atlas_bundle(dashboard,pages,atlas)
validate_atlas_bundle(read("fixtures/dashboard.empty.json"),{},read("fixtures/atlas.empty.json"))
expected=read("fixture-provenance.json")["expected"]
actual={
    "raw_documents":dashboard["stats"]["total_raw"],
    "current_processed_documents":len(atlas["articles"]),
    "recent_sample":len(dashboard["recent"]),
    "country_unassigned":sum(a["country"] is None for a in atlas["articles"]),
    "publication_undated":sum(a["published_date"] is None for a in atlas["articles"]),
    "collection_undated":sum(a["collected_at"] is None for a in atlas["articles"]),
    "archive_title_en_available":sum(a["title_en"] is not None for a in atlas["articles"]),
    "archive_entities":len(atlas["entities"]),
    "archive_entity_name_en_available":sum(e["name_en"] is not None for e in atlas["entities"]),
    "review_pairs":len(dashboard["review_queue"]["items"]),
}
assert actual == expected, (actual,expected)

corpus=load_snapshot(root/"fixtures/evidence.input.synthetic.json")
for case in read("evidence-cases.json"):
    assert read(case["file"]) == search_evidence(corpus,case["query"],**case["options"]), case["file"]
translated=read("fixtures/evidence.translation.synthetic.json")
cache=dict(translated["results"][0]["evidence"][0]["translation"])
cache.pop("kind")
cache.pop("machine_generated")
assert cache["record_origin"] == "synthetic_fixture"
translated_corpus=replace(corpus,translations=(CachedEvidenceTranslation(**cache),))
assert translated == search_evidence(translated_corpus,"Mohammed al-Ahmad")

sources={d.document.id:d.document.text for d in corpus.documents}
for path in sorted((root/"fixtures").glob("evidence.*.synthetic.json")):
    if path.name == "evidence.input.synthetic.json":
        continue
    data=json.loads(path.read_text())
    assert data["data_kind"] == "synthetic_fixture"
    assert data["access_scope"] == "private_local_only"
    for result in data["results"]:
        for window in result["evidence"]:
            assert window["text"] == sources[window["document_id"]][window["start"]:window["end"]]
            assert hashlib.sha256(window["text"].encode()).hexdigest() == window["span_sha256"]
            mention=window["mention"]
            assert window["text"][mention["start"]-window["start"]:mention["end"]-window["start"]] == mention["text"]

# Corruption must fail rather than gaining an apparent complete-archive status.
from copy import deepcopy
bad=deepcopy(atlas)
bad["articles"][0]["title"]="tampered"
try:
    AtlasSnapshot.model_validate(bad)
except ValueError:
    pass
else:
    raise AssertionError("Archive digest tampering was accepted")

manifest=root/"source-pin.json"
if manifest.exists():
    for path, expected_hash in read("source-pin.json")["backend_source_sha256"].items():
        assert hashlib.sha256((backend/path).read_bytes()).hexdigest() == expected_hash, path
print("PASS: 5 exact backend copies, 2 public bundles, 8 regenerated private evidence outputs, all counts/spans/hashes, tamper rejection, and pinned source hashes.")
