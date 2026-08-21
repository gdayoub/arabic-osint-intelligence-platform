"""M1 checkpoint: insert a document by hand, attach a mention, create an
entity, then let `python main.py provenance show` walk the chain back to the
source text.

Run with DATABASE_URL pointed at a throwaway SQLite file, e.g.:
    DATABASE_URL=sqlite:///data/core_demo.db python scripts/m1_checkpoint_demo.py
"""

from __future__ import annotations

from pathlib import Path

from src.core.ontology import Ontology
from src.store.blob import get_blob_store
from src.store.database import get_core_session, init_core_db
from src.store.provenance import create_document, create_entity, create_mention, register_extractor_version

ARABIC_SENTENCE = "أعلن الرئيس الأمريكي جو بايدن عن سياسة جديدة تجاه الشرق الأوسط."
# "The American President Joe Biden announced a new policy toward the Middle East."
MENTION_TEXT = "جو بايدن"  # "Joe Biden"


def main() -> None:
    init_core_db()
    repo_root = Path(__file__).resolve().parent.parent
    ontology = Ontology.from_yaml(repo_root / "config" / "ontology.yaml")
    blob_store = get_blob_store()

    start = ARABIC_SENTENCE.index(MENTION_TEXT)
    end = start + len(MENTION_TEXT)

    with get_core_session() as session:
        extractor = register_extractor_version(
            session, name="manual_entry", version="0.1.0", description="M1 checkpoint, entered by hand"
        )
        document = create_document(
            session,
            source="manual",
            text=ARABIC_SENTENCE,
            content_hash="m1-checkpoint-demo-doc-1",
            blob_store=blob_store,
        )
        mention = create_mention(
            session,
            document=document,
            text=MENTION_TEXT,
            start=start,
            end=end,
            object_type="person",
            extractor_version=extractor,
            ontology=ontology,
            language="ar",
        )
        entity = create_entity(
            session,
            object_type="person",
            canonical_name="Joe Biden",
            properties={"canonical_name": "Joe Biden", "aliases": ["جو بايدن"]},
            source_mention=mention,
            extractor_version=extractor,
            ontology=ontology,
        )

    print(f"document.id = {document.id}")
    print(f"mention.id  = {mention.id}  (text={mention.text!r}, offsets={mention.start}:{mention.end})")
    print(f"entity.id   = {entity.id}  (canonical_name={entity.canonical_name!r})")
    print()
    print("Now run:")
    print(f"  python main.py provenance show mentions {mention.id}")
    print(f"  python main.py provenance show entities {entity.id}")


if __name__ == "__main__":
    main()
