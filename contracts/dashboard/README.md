# Dashboard public contract

This directory is the producer-side source of truth for JSON consumed by the
Cloudflare dashboard and the separate portfolio. The existing portfolio route
remains:

`https://george-dayoub-portfolio.vercel.app/osint-dashboard.html`

`contract.lock.json` selects the current immutable semantic-version directory.
Each version contains JSON Schemas, canonical fixtures, generated TypeScript
declarations, and a hash manifest. Generate or verify them with:

```bash
python scripts/generate_dashboard_contract.py
python scripts/generate_dashboard_contract.py --check
```

Generation requires the exact `pydantic==2.12.5` pin. Runtime payload version
`schema_version` and contract bundle version are related but separate:

- A patch contract release may clarify documentation or fixtures without
  changing the accepted JSON shape.
- A minor contract release may add optional fields. Existing fields keep their
  names, meanings, and types, and consumers must ignore fields they do not use.
- Removing or renaming a field, making an optional field required, narrowing an
  accepted value, or changing meaning/type is breaking. It requires a major
  contract release, a documented `schema_version` migration, and both public
  consumers pinned to the expanded shape before the old field is contracted.

Committed version directories are immutable after a consumer pins them. Start
a new semantic-version directory for an intentional change; do not rewrite an
old bundle and leave its version unchanged.

The producer preserves external text exactly, including HTML-sensitive
characters. Browser consumers must render those strings as text, never as
trusted markup. Source URLs are the exception: the producer rejects anything
except safe absolute HTTP(S) links and also rejects raw attribute delimiters.
