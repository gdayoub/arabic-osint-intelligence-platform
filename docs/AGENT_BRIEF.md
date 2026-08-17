# Agent Brief — Entity Resolution & Link Analysis Engine

## 0. How to work with me

Read this section first and follow it for the entire project. It overrides any instinct you have to be efficient.

I am a student building this to learn, not to ship. **If you write code I don't understand, the project has failed even if the code works.** I need to be able to defend every design decision and explain every function in a technical interview.

Therefore:

1. **Explain before you build.** Before writing any module, tell me in plain language: what problem it solves, what approach you're taking, what alternatives you rejected and why. Wait for me to say go.
2. **Stop at every checkpoint.** Each milestone below ends with a checkpoint. Do not proceed past it. Show me what you built, how to run it, and what I should see.
3. **Teach the Python, not just the logic.** I understand algorithms; Python idioms are my weak side. When you use a comprehension, a generator, a decorator, `functools`, `itertools`, a context manager, or anything from the standard library I might not know — explain the syntax itself, not just what it does here.
4. **Prefer boring code.** No clever one-liners. No metaprogramming. No premature abstraction. If there's a straightforward way and an elegant way, write the straightforward one and tell me the elegant one exists.
5. **No dependency without justification.** Before adding a library, tell me what it does, why the standard library isn't enough, and how big it is. I'd rather write 40 lines than add a package I don't understand.
6. **Ask when the spec is ambiguous.** Do not guess and move on. A wrong assumption compounds.
7. **Write an ADR for every real decision.** One file in `docs/adr/`, numbered, with: context, options considered, decision, consequences. Fifteen minutes each. These are my interview notes.

If I say "just build it" for a specific piece, you may — but note in your response what I skipped so I can come back to it.

---

## 1. What we're building

An engine that takes messy text, identifies the real-world things being discussed, merges duplicate references to the same thing, connects them, and makes the result queryable.

The first domain is Arabic open-source intelligence — news and social media. But **the engine must not be Arabic-specific or OSINT-specific.** Language handling and object types are configuration, not core. This is the single most important architectural constraint in this document.

### The core transformation

```
Document  →  Mention  →  Entity  →  Graph
 (text)     (a span     (a real     (things
            of chars)   thing)      connected)
```

- **Document** — something scraped. Has raw text, a source, a timestamp, a URL.
- **Mention** — a span of characters in one document that names something. Has offsets.
- **Entity** — a real-world thing. Has a canonical name, a type, and many mentions as evidence.
- **Link** — a typed, evidenced relationship between two entities.

### Why it matters

Text search finds documents. This finds *things*, which lets you ask questions text search cannot answer: who appears alongside whom, whose mention rate spiked this week, what connects A to B in three hops, what did we believe as of a given date.

---

## 2. Current state

- A working Python scraper producing Arabic documents
- Postgres for storage (schema needs redesign — currently document-centric)
- A `Mention` dataclass with `doc_id`, `text`, `start`, `end`
- Previously hosted on Railway; being migrated (raw text is being moved to compressed object storage, metadata stays in Postgres)

Assume nothing else exists. Read the repo before proposing changes.

---

## 3. Non-negotiable principles

Violating any of these is a bug, even if tests pass.

**P1 — Every derived fact records its origin.** Entity, link, event, score — each must be traceable to the exact document and character span that produced it, plus the extractor name and version. If you can't answer "why does the system believe this," the feature isn't done.

**P2 — Offsets must always be valid.** `document.text[m.start:m.end] == m.text` for every mention, always. Assert it in tests. Character offsets in Python are Unicode code points, not bytes — never mix the two.

**P3 — Nothing is domain-hardcoded.** Object types (`Person`, `Organization`, `Location`, `Event`) live in a config file, not in Python classes or SQL enums. Language-specific logic lives behind a `LanguageAdapter` interface. Adding Farsi or Russian later must not require touching the core.

**P4 — Extractors are versioned.** Every extractor has a name and semantic version. Every output records which version produced it. When a model improves, we must be able to find and reprocess only the affected facts.

**P5 — Facts are append-only.** Never update a fact in place — supersede it with a new row that points at the old one. This is what lets us answer "what did we know on March 4th?"

**P6 — Nothing is deleted, only retracted.** A discredited source doesn't erase its facts; it marks them retracted and propagates that downstream.

**P7 — Measure before optimizing.** No performance change lands without a before/after benchmark recorded in `benchmarks/results.md`.

---

## 4. Technology constraints

**Use:**
- Python 3.11+ for all pipeline and backend code
- Postgres 16 with PostGIS and pgvector extensions
- FastAPI + Pydantic v2 for the API
- pytest for tests
- Docker Compose for local services
- Next.js + TypeScript for the eventual frontend

**Do not use, yet:**
- Kafka, Redpanda, Celery, or any message queue — plain functions and a simple job table until we measurably need more
- Kubernetes, Terraform, or any orchestration
- Neo4j or any graph database — recursive CTEs in Postgres until they demonstrably break
- Any LLM API for extraction until we've built and measured a non-LLM baseline

**Rationale for the "not yet" list:** each of these solves a scaling problem we don't have. Adding them now buys complexity and teaches me nothing, because I won't have felt the pain they relieve. When a benchmark shows we need one, we'll add it and I'll understand why.

---

## 5. Milestones

Build strictly in order. Each ends with a checkpoint.

---

### M1 — Data model and provenance foundation

Everything else depends on getting this right, and retrofitting provenance is miserable.

**Build:**
- Core dataclasses: `Document`, `Mention`, `Entity`, `Link`, `Fact`, `Provenance`
- Postgres schema with these tables, plus `extractor_versions` and a `provenance` lineage table
- A `record_fact()` path that makes it *impossible* to write a fact without provenance — no bypass
- A CLI: `provenance show <fact_id>` printing the full chain back to the source sentence
- Object type definitions loaded from `config/ontology.yaml`, not hardcoded

**Ontology config shape (adjust as needed, but keep it declarative):**

```yaml
object_types:
  person:
    display_name: {en: Person, ar: شخص}
    properties:
      - {name: canonical_name, type: text, required: true}
      - {name: aliases, type: text[], required: false}
      - {name: date_of_birth, type: date, required: false}
  organization:
    properties:
      - {name: canonical_name, type: text, required: true}
      - {name: org_type, type: enum, values: [government, military, ngo, media, commercial]}
  location:
    properties:
      - {name: canonical_name, type: text, required: true}
      - {name: geom, type: point, required: false}
      - {name: geonames_id, type: integer, required: false}

link_types:
  - {name: mentioned_with, from: "*", to: "*", symmetric: true}
  - {name: member_of, from: person, to: organization, symmetric: false}
  - {name: located_in, from: "*", to: location, symmetric: false}
```

**Checkpoint:** insert a document by hand, attach a mention, create an entity, run `provenance show` on it, and see the chain terminate at the source text.

---

### M2 — Language adapters and Arabic normalization

**Build:**
- A `LanguageAdapter` protocol with methods: `detect()`, `normalize()`, `tokenize()`, `blocking_keys()`, `romanize()`
- `ArabicAdapter` implementing it, with normalization covering: NFKC, diacritic stripping, tatweel removal, alef unification (أإآٱ→ا), taa marbuta (ة→ه), alef maksura (ى→ي), hamza carrier collapse, definite article handling
- `EnglishAdapter` as a trivial second implementation — this exists purely to prove the abstraction holds
- A property-based test suite (`hypothesis`) asserting normalization is idempotent: `normalize(normalize(x)) == normalize(x)`
- A golden-file test with 100 real Arabic name pairs, hand-labeled same/different

**Explain to me:** why normalization must never be applied to stored text, only to comparison keys. What breaks if we normalize in place.

**Checkpoint:** show me a table of 20 raw Arabic names and their normalized forms. I want to eyeball it.

---

### M3 — Mention extraction

**Build:**
- A `MentionExtractor` protocol, versioned per P4
- `GazetteerExtractor` — dictionary match against a known-names list, using Aho-Corasick. **Build this first.** It's the baseline everything else is measured against.
- `ModelExtractor` — wraps a HuggingFace token-classification model (start with `CAMeL-Lab/bert-base-arabic-camelbert-mix` fine-tuned for NER)
- An evaluation harness: precision, recall, F1 against a hand-labeled set, per entity type
- A CLI to dump extractor disagreements for manual review

**Explain to me:** how Aho-Corasick works and why it beats running N separate string searches. How subword tokenization makes character offsets nontrivial to recover from a transformer, and how you're handling it.

**Checkpoint:** F1 numbers for both extractors on the same eval set, in a table. If the model doesn't beat the gazetteer, say so plainly.

---

### M4 — Entity resolution

The heart of the project. Take the most care here.

**Build:**

*Blocking:*
- Multi-key blocking (last token, first+last-initial, sorted token set, character trigrams)
- Block size capping — drop any block above a configurable threshold and log it
- MinHash LSH as a second implementation, behind the same interface
- Report the reduction ratio: candidate pairs generated vs. the full N²/2

*Scoring:*
- A `PairScorer` combining: string similarity, co-occurring entity overlap (Jaccard), contextual embedding cosine, temporal proximity, source agreement
- Weights learned by logistic regression on a hand-labeled pair set — **not hand-tuned**
- Report AUC and a precision-recall curve

*Clustering:*
- Union-Find with path compression and union-by-rank
- A guard against giant components — if a cluster exceeds a threshold, re-cluster it with hierarchical agglomerative clustering
- Output the cluster size distribution as a histogram

*Human-in-the-loop:*
- A review queue for pairs scoring near the threshold
- CLI to accept/reject, with decisions fed back into the training set
- Manual merge and split operations that are themselves recorded with provenance

**Explain to me:** why blocking is necessary with the actual arithmetic for my corpus size. Why transitive closure through Union-Find is dangerous and how the guard works. Why the weights must be learned rather than chosen.

**Checkpoint:** precision, recall, F1 on entity resolution. Cluster size histogram. Five examples of correct merges and five of errors, with the scores that produced them.

---

### M5 — Bilingual layer

This is what makes the system usable by people who don't read Arabic — which is most of the people who will evaluate it.

**Build:**
- Translation of entity names and evidence sentences, cached in Postgres keyed by content hash
- A romanization module for Arabic names, generating *multiple* candidate spellings (Mohammed / Muhammad / Mohamed / Mohammad)
- Bidirectional search: an English query for "Mohammed al-Ahmad" must find the Arabic entity, and vice versa
- Every UI surface shows original and translation side by side, never translation alone
- Translations are marked as machine-generated in provenance — a translation is a derived fact like any other

**Explain to me:** why we cache on content hash rather than entity ID. What happens to cached translations when an entity is merged.

**Checkpoint:** search in English, get the right Arabic entity, see both spellings and a translated evidence sentence with the Arabic beside it.

---

### M6 — Links and the graph

**Build:**
- Co-occurrence links with weights (same document < same paragraph < same sentence)
- Relation extraction for typed links (`member_of`, `located_in`) with confidence scores
- Recursive CTE traversal with configurable depth and minimum edge weight
- Graph metrics: degree, betweenness centrality, Louvain community detection
- A shortest-path query between two entities

**Checkpoint:** given an entity ID, return its 2-hop neighborhood as JSON, with edge weights and the evidence backing each edge.

---

### M7 — Events and geography

**Build:**
- `Event` as an ontology type: what, where, when, who, reported-by
- Event extraction from text, with date resolution (including relative dates — "yesterday," "last week" — resolved against publication date)
- GeoNames integration: load the dump, index Arabic alternate names, resolve place mentions to coordinates
- Toponym disambiguation using document context — resolve unambiguous places first, then use their centroid to pull ambiguous ones toward the right region
- PostGIS spatial queries: radius search, bounding box, nearest-neighbor

**Explain to me:** the toponym disambiguation algorithm, with a worked example on a place name that's ambiguous between two countries.

**Checkpoint:** a query returning all events within 50km of a point in the last 30 days, each with coordinates and source.

---

### M8 — Deduplication and source reliability

**Build:**
- SimHash near-duplicate detection to collapse syndicated wire copy into story clusters
- Independent-source counting that operates on story clusters, not raw documents
- Admiralty-code scoring: source reliability (A–F) and information credibility (1–6)
- Credibility computed from independent corroboration count, not raw mention count
- Source reliability tracked over time based on historical corroboration rate

**Explain to me:** why deduplication must happen before corroboration counting, with a concrete example of the failure mode if it doesn't.

**Checkpoint:** a claim displayed with its Admiralty rating and the list of independent sources supporting it.

---

### M9 — Temporal analysis and alerting

**Build:**
- Daily mention-count time series per entity
- Rolling z-score burst detection with configurable window and threshold
- Kleinberg two-state burst detection as a second implementation, compared against the z-score baseline
- Watchlists: saved queries over entities, geofences, and keywords
- Alert evaluation on ingest, using an inverted index so we don't run every watchlist against every document
- An alert feed with deduplication (don't fire twice for the same underlying event)

**Explain to me:** the inverted-index trick for efficient watchlist evaluation. Why naive evaluation is O(watchlists × documents) and how this changes it.

**Checkpoint:** create a watchlist, ingest a document that matches, see the alert fire with its evidence.

---

### M10 — API and frontend

**Build:**
- FastAPI with strict Pydantic models and cursor-based pagination (not offset — offset breaks on live data)
- OpenAPI spec generated, TypeScript client generated from it
- Next.js frontend:
  - Entity profile page: canonical name (both scripts), aliases, timeline, map, connected entities, evidence list
  - Graph canvas using WebGL (Sigma.js v3 or Cosmograph) — must stay smooth at 10k nodes, with layout in a Web Worker
  - Map using deck.gl over MapLibre
  - Brushable timeline that cross-filters the graph and map
  - Virtualized tables (TanStack Table) handling 100k rows
  - Provenance drawer: click any claim, see the chain, land on the highlighted source sentence with translation
- Live alert feed over SSE

**Explain to me:** why DOM-based graph rendering fails at scale and what WebGL does differently. Why the layout algorithm needs its own thread.

**Checkpoint:** load a 10k-node graph, brush the timeline, watch the graph filter in real time without dropping frames.

---

### M11 — Access control

Only after everything above works.

**Build:**
- Classification markings on objects and facts: level, compartments, releasability
- User clearances and compartment memberships
- Access decisions enforced in the SQL `WHERE` clause via row-level security — **never** by filtering results in Python
- A redaction policy for graph edges to invisible nodes, documented with its rationale
- A leakage test suite: for each of N test users, assert that no query path returns data above their clearance — including counts, aggregates, and edge existence

**Explain to me:** the difference between filtering in the query and filtering in the response, and exactly what leaks in the latter case. What "aggregation leakage" means and why it's hard.

**Checkpoint:** the leakage test suite passing, with at least one test that would have caught a real mistake.

---

## 6. Repository layout

```
.
├── config/
│   ├── ontology.yaml
│   ├── sources.yaml
│   └── extractors.yaml
├── src/
│   ├── core/           # dataclasses, provenance, no domain logic
│   ├── lang/           # LanguageAdapter implementations
│   ├── extract/        # mention and relation extractors
│   ├── resolve/        # blocking, scoring, clustering
│   ├── geo/            # toponym resolution, PostGIS helpers
│   ├── analyze/        # burst detection, graph metrics, dedup
│   ├── store/          # repositories, migrations
│   └── api/            # FastAPI app
├── web/                # Next.js
├── tests/
│   ├── unit/
│   ├── integration/
│   └── golden/         # hand-labeled eval sets
├── benchmarks/
│   └── results.md
├── docs/
│   ├── adr/
│   └── DECISIONS_I_GOT_WRONG.md
└── docker-compose.yml
```

---

## 7. Definition of done

A milestone is not complete until all of these hold:

- [ ] Tests pass, including at least one test that would catch a realistic regression
- [ ] Every new fact type records provenance (P1)
- [ ] Nothing domain-specific leaked into `src/core/` (P3)
- [ ] An ADR exists for every non-obvious decision
- [ ] Benchmark numbers recorded if performance-relevant (P7)
- [ ] `docker compose up` still works from a clean clone
- [ ] I have explained the milestone's core idea back to you in my own words, and you agree I've got it

That last one is the real gate. If I can't explain it, we're not done — go back and teach it differently.

---

## 8. First action

Do not write code yet.

Read the existing repository. Then report back with:

1. What currently exists and what state it's in
2. Anything in this brief that conflicts with what you found
3. Your proposed schema for M1, in SQL, with the reasoning for each design choice
4. The three decisions in M1 you think are most likely to be wrong, and what would change your mind

Then stop and wait.
