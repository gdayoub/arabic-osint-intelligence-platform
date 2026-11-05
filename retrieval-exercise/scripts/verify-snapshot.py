"""Read-only, standard-library integrity checks for the portable exercise snapshot."""
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8-sig"))


def main():
    manifest = read("data/articles/manifest.json")
    documents = {}
    for doc in manifest["documents"]:
        path = ROOT / "data/articles" / doc["local_text_file"]
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        check(digest(raw) == doc["text_hash"], f"Source hash: {path.name}")
        check(len(text) == doc["text_length"], f"Source length: {path.name}")
        check(gzip.decompress(Path(str(path) + ".gz").read_bytes()) == raw,
              f"Compressed original: {path.name}")
        check(doc["document_id"] not in documents, "Duplicate document ID")
        documents[doc["document_id"]] = (doc, text)

    packet_raw = (ROOT / "data/labels/review-40.json").read_bytes()
    packet = json.loads(packet_raw)
    records = {record["question_id"]: record for record in packet["records"]}
    check(len(records) == len(packet["records"]), "Duplicate question ID")
    for record in records.values():
        doc, text = documents[record["document_id"]]
        span = record["passage"]
        check(text[span["start"]:span["end"]] == span["original_span"], "Review span")
        check(doc["text_hash"] == record["text_hash"], "Review source hash")

    decisions = {}
    journal = ROOT / "data/labels/button-review-history.jsonl"
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        check(event["packet_sha256"] == digest(packet_raw), "Journal packet mismatch")
        check(not event["test"], "Synthetic event in production journal")
        record = records[event["question_id"]]
        check(event["document_id"] == record["document_id"], "Journal document mismatch")
        check(event["text_hash"] == record["text_hash"], "Journal text mismatch")
        decisions[event["question_id"]] = event

    corpus_raw = (ROOT / "data/prepared/corpus-passages.json").read_bytes()
    corpus = json.loads(corpus_raw)
    check(corpus["manifest_sha256"] == digest((ROOT / "data/articles/manifest.json").read_bytes()),
          "Corpus manifest mismatch")
    passage_ids = set()
    for passage in corpus["passages"]:
        doc, text = documents[passage["document_id"]]
        span = passage["original_span"]
        check(text[passage["start"]:passage["end"]] == span, "Prepared span")
        check(digest(span.encode("utf-8")) == passage["passage_hash"], "Passage hash")
        check(doc["text_hash"] == passage["text_hash"], "Passage source hash")
        path = ROOT / "data/prepared" / passage["local_text_file"]
        check(digest(path.read_bytes()) == doc["text_hash"], "Prepared source path")
        check(passage["passage_id"] not in passage_ids, "Duplicate passage ID")
        passage_ids.add(passage["passage_id"])

    queries = read("data/prepared/queries.json")
    check({q["question_id"] for q in queries} == set(records), "Prepared queries")
    rankings = read("results/lexical-rankings.json")
    check(rankings["corpus_sha256"] == digest(corpus_raw), "Rankings corpus mismatch")
    check({r["question_id"] for r in rankings["results"]} == set(records), "Ranked queries")
    for result in rankings["results"]:
        ids = [item["passage_id"] for item in result["ranking"]]
        check(len(ids) == len(passage_ids) and set(ids) == passage_ids, "Ranking coverage")
    print(json.dumps({"documents": len(documents), "questions": len(records),
                      "passages": len(passage_ids), "latest_decisions": len(decisions),
                      "approved": sum(d["approved"] for d in decisions.values()),
                      "labels": dict(Counter(d["label"] for d in decisions.values())),
                      "arabic_verified": sum(d["arabic_verified"] is True for d in decisions.values()),
                      "integrity": "passed", "retrieval_quality": "not evaluated"}))


if __name__ == "__main__":
    main()
