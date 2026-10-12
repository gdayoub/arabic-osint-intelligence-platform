# Frontend contract fixture bundle

This directory is developer input, not a deployed dataset or UI. Every new article and evidence example is synthetic. See `fixture-provenance.json` and the frozen `../FRONTEND_AGENT_PROMPT.md` before connecting it.

## Files

- `source-pin.json`: exact backend code SHA, PR, frontend baseline, and backend source-file hashes.
- `schemas/*.schema.json`: unchanged committed public dashboard/country/archive schemas.
- `types/dashboard.types.ts`: unchanged generated producer types.
- `types/atlas.types.ts`, `types/evidence.types.ts`: manually mirrored consumer types checked against the pinned producer; these are not runtime validators.
- `types/adapter-contract.types.ts`: frontend-local interfaces to implement, not a backend wire contract.
- `fixtures/dashboard.synthetic.json` and `fixtures/atlas.synthetic.json`: coherent synthetic populated pair; `countries.synthetic.json` is a country-name-to-payload map used only for bundle validation.
- `fixtures/dashboard.empty.json`, `fixtures/atlas.empty.json`: coherent empty pair.
- `fixtures/evidence.input.synthetic.json`: unchanged synthetic private reader input from backend `tests/fixtures/bilingual_evidence.json`.
- `fixtures/evidence.{alias,arabic,romanization,cropped,empty,unbounded,capped,translation}.synthetic.json`: exact regenerated reader outputs. The translation example is explicitly synthetic, not machine-generated.
- `evidence-cases.json`: seven query/option pairs for regeneration. The translation output contains the eighth case's synthetic detached cache record.
- `validate_bundle.py`: read-only authoritative backend validation, regeneration, span/hash checks, and a tampering negative test.
- `verification.json`: what was actually checked, with limits.

## Use

Copy needed types and fixtures into the frontend's test/support directory, outside `public/`. Use the same model/render functions for live and preview modes; inject an explicit synthetic adapter only through a developer-only harness. Never use these files as a network-error fallback.

From a backend checkout with its normal dependencies:

```bash
python docs/frontend-handoff/frontend-contract/validate_bundle.py --backend .
```

For an external copy, supply the actual script path and `--backend /path/to/backend`. Python 3.12 and the repository-pinned Pydantic 2.12.5 were used here. `python -S -m src.search.evidence_cli --snapshot tests/fixtures/bilingual_evidence.json --query 'Mohammed al-Ahmad'` also demonstrates the private reader without third-party dependencies, from the backend root.

Baseline public schemas contain no `data_kind`. Keep the developer mode/provenance wrapper separate; do not add invented fields to the strict public wire payloads. The archive `fx-*` IDs and offline evidence numeric IDs belong to separate corpora. Do not join them or present one as provenance for the other.

The complete archive hash is generated and validated by the actual backend model. A valid synthetic fixture proves contract/rendering behavior only. It does not prove current public endpoint availability, production completeness, model accuracy, or deployment health.
