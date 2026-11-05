"""Prepare review passages only. No model, installation, or network access."""
import hashlib
import json
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
ROOT = WORKSPACE / 'data' / 'prepared'
ROOT.mkdir(parents=True, exist_ok=True)
SOURCE = WORKSPACE / 'data' / 'articles'
LABELS = WORKSPACE / 'data' / 'labels'
manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
packet = json.loads((LABELS / 'review-40.json').read_text(encoding='utf-8'))
packet_hash = hashlib.sha256((LABELS / 'review-40.json').read_bytes()).hexdigest()
decisions = {}
for line in (LABELS / 'button-review-history.jsonl').read_text(encoding='utf-8').splitlines():
    event = json.loads(line)
    assert event['packet_sha256'] == packet_hash and not event['test']
    decisions[event['question_id']] = event

passages = []
for doc in manifest['documents']:
    raw = (SOURCE / doc['local_text_file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == doc['text_hash']
    text = raw.decode('utf-8')
    assert len(text) == doc['text_length']
    # Fixed, overlapping windows with whitespace boundaries. Never normalize originals.
    start = 0
    spans = []
    while start < len(text):
        end = min(start + 650, len(text))
        if end < len(text):
            boundaries = [m.end() for m in re.finditer(r'\s+', text[start:end])]
            if boundaries and boundaries[-1] > 325:
                end = start + boundaries[-1]
        spans.append((start, end))
        if end == len(text):
            break
        next_start = max(start + 1, end - 100)
        boundary = re.search(r'\s+', text[next_start:end])
        start = next_start + boundary.end() if boundary else next_start
    for start, end in spans:
        span = text[start:end]
        passage_hash = hashlib.sha256(span.encode('utf-8')).hexdigest()
        passages.append({
            'passage_id': f"{doc['id']}:{start}:{end}",
            'document_id': doc['document_id'], 'legacy_document_id': doc['id'],
            'text_hash': doc['text_hash'], 'passage_hash': passage_hash,
            'source': doc['source'], 'source_ref': doc['source_ref'],
            'published_at': doc['published_at'], 'collected_at': doc['collected_at'],
            'data_snapshot_id': manifest['data_snapshot_id'],
            'local_text_file': '../articles/' + doc['local_text_file'],
            'start': start, 'end': end, 'original_span': span,
            'offset_unit': 'unicode_code_point', 'end_exclusive': True,
            'language': 'ar', 'direction': 'rtl',
            'split': 'evaluation_candidate_quarantine',
            'eligible_for_training': False, 'eligible_for_validation_or_test': False,
        })

out = {'configuration': {'max_code_points': 650, 'overlap_target': 100,
    'normalization': 'none', 'boundary': 'whitespace',
    'split_policy': 'All 50 documents stay quarantined together; story and identity grouping pending.'},
    'manifest_sha256': hashlib.sha256((SOURCE / 'manifest.json').read_bytes()).hexdigest(),
    'passages': passages}
(ROOT / 'corpus-passages.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
queries = []
for record in packet['records']:
    d = decisions[record['question_id']]
    queries.append({'question_id': record['question_id'], 'query_en': record['query_en'],
        'previous_record': record, 'previous_decision': d,
        'collection_answerability': 'not_assessed',
        'unjudged_policy': 'Unreviewed passages are unknown, never automatic negatives.'})
(ROOT / 'queries.json').write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'documents': len(manifest['documents']), 'passages': len(passages), 'queries': len(queries),
    'previous_approvals': sum(q['previous_decision']['approved'] for q in queries)}))
