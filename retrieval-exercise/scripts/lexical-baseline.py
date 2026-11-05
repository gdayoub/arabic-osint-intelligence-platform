"""Exact CPU BM25 baseline: raw English query versus original Arabic passages.

Cross-language word mismatch is intentional and must be reported. This baseline
does not translate queries or use labels. All zero-score ties use passage ID.
"""
import collections
import hashlib
import json
import math
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
ROOT = WORKSPACE / 'data' / 'prepared'
RESULTS = WORKSPACE / 'results'


def terms(text):
    return re.findall(r'\w+', text.casefold(), flags=re.UNICODE)


def run():
    corpus_bytes = (ROOT / 'corpus-passages.json').read_bytes()
    corpus = json.loads(corpus_bytes)['passages']
    queries = json.loads((ROOT / 'queries.json').read_text(encoding='utf-8'))
    counts = [collections.Counter(terms(p['original_span'])) for p in corpus]
    lengths = [sum(c.values()) for c in counts]
    average_length = sum(lengths) / len(lengths)
    df = collections.Counter(t for c in counts for t in c)
    results = []
    for q in queries:
        scores = []
        for p, freq, length in zip(corpus, counts, lengths):
            score = 0.0
            for term in set(terms(q['query_en'])):
                if term not in freq:
                    continue
                idf = math.log(1 + (len(corpus) - df[term] + .5) / (df[term] + .5))
                tf = freq[term]
                score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / average_length))
            scores.append({'passage_id': p['passage_id'], 'score': score})
        scores.sort(key=lambda p: (-p['score'], p['passage_id']))
        results.append({'question_id': q['question_id'], 'ranking': scores})
    output = {'method': 'raw_english_arabic_BM25_v1', 'model_version': None,
        'corpus_sha256': hashlib.sha256(corpus_bytes).hexdigest(),
        'configuration': {'k1': 1.2, 'b': .75, 'query_term_frequency': 'binary',
            'tokenization': 'Unicode word regex with casefold', 'translation': None,
            'stemming': None, 'tie_break': 'passage_id ascending'},
        'limitations': 'Literal English-Arabic matching is weak. Zero scores are no lexical match, not evidence of irrelevance or unanswerability.',
        'abstention': None, 'metrics': None, 'results': results}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / 'lexical-rankings.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'queries': len(results), 'passages': len(corpus),
        'queries_with_nonzero_match': sum(x['ranking'][0]['score'] > 0 for x in results)}))


if __name__ == '__main__':
    run()
