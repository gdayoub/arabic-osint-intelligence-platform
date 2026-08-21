"""Tests for the P1/P2 invariants src/store/provenance.py is supposed to enforce."""

from __future__ import annotations

import pytest

from src.store.provenance import create_document, create_entity, create_mention, get_provenance_chain, link_entities, record_document_fact, register_extractor_version


def test_mention_offsets_must_match_document_text(session, ontology, blob_store):
    document = create_document(session, source="test", text="Joe Biden met the press.", content_hash="h1", blob_store=blob_store)
    extractor = register_extractor_version(session, "gazetteer", "0.1.0")

    with pytest.raises(ValueError, match="Offset mismatch"):
        create_mention(
            session,
            document=document,
            text="Biden",
            start=0,  # wrong: document.text[0:5] is "Joe B", not "Biden"
            end=5,
            object_type="person",
            extractor_version=extractor,
            ontology=ontology,
            language="en",
        )


def test_mention_rejects_unknown_object_type(session, ontology, blob_store):
    document = create_document(session, source="test", text="Joe Biden met the press.", content_hash="h2", blob_store=blob_store)
    extractor = register_extractor_version(session, "gazetteer", "0.1.0")

    with pytest.raises(ValueError, match="not declared in ontology"):
        create_mention(
            session,
            document=document,
            text="Joe Biden",
            start=0,
            end=9,
            object_type="spaceship",  # not in config/ontology.yaml
            extractor_version=extractor,
            ontology=ontology,
            language="en",
        )


def test_mention_and_entity_creation_record_provenance_back_to_document(session, ontology, blob_store):
    document = create_document(session, source="test", text="Joe Biden met the press.", content_hash="h3", blob_store=blob_store)
    extractor = register_extractor_version(session, "gazetteer", "0.1.0")

    mention = create_mention(
        session,
        document=document,
        text="Joe Biden",
        start=0,
        end=9,
        object_type="person",
        extractor_version=extractor,
        ontology=ontology,
        language="en",
    )
    entity = create_entity(
        session,
        object_type="person",
        canonical_name="Joe Biden",
        properties={"canonical_name": "Joe Biden"},
        source_mention=mention,
        extractor_version=extractor,
        ontology=ontology,
    )

    mention_chain = get_provenance_chain(session, "mentions", mention.id, blob_store)
    entity_chain = get_provenance_chain(session, "entities", entity.id, blob_store)

    assert len(mention_chain) == 1
    assert len(entity_chain) == 1
    for entry in (mention_chain[0], entity_chain[0]):
        assert entry.document_id == document.id
        assert entry.mention_text == "Joe Biden"
        assert entry.extractor_name == "gazetteer"
        # P2, re-verified at the provenance layer (text now round-tripped
        # through the blob store), not just at write time:
        assert document.text[entry.mention_start : entry.mention_end] == entry.mention_text
        assert entry.document_text[entry.mention_start : entry.mention_end] == entry.mention_text


def test_link_entities_rejects_type_mismatch(session, ontology, blob_store):
    document = create_document(session, source="test", text="Joe Biden met Jill Biden.", content_hash="h4", blob_store=blob_store)
    extractor = register_extractor_version(session, "gazetteer", "0.1.0")

    m1 = create_mention(session, document, "Joe Biden", 0, 9, "person", extractor, ontology, language="en")
    m2 = create_mention(session, document, "Jill Biden", 14, 24, "person", extractor, ontology, language="en")
    person_a = create_entity(session, "person", "Joe Biden", {}, m1, extractor, ontology)
    person_b = create_entity(session, "person", "Jill Biden", {}, m2, extractor, ontology)

    # member_of is defined in ontology.yaml as person -> organization only.
    with pytest.raises(ValueError, match="not valid between"):
        link_entities(
            session,
            link_type="member_of",
            from_entity=person_a,
            to_entity=person_b,
            document=document,
            extractor_version=extractor,
            ontology=ontology,
        )


def test_link_entities_records_provenance_when_valid(session, ontology, blob_store):
    document = create_document(session, source="test", text="Joe Biden leads the White House.", content_hash="h5", blob_store=blob_store)
    extractor = register_extractor_version(session, "gazetteer", "0.1.0")

    person_mention = create_mention(session, document, "Joe Biden", 0, 9, "person", extractor, ontology, language="en")
    org_mention = create_mention(session, document, "White House", 20, 31, "organization", extractor, ontology, language="en")
    person = create_entity(session, "person", "Joe Biden", {}, person_mention, extractor, ontology)
    org = create_entity(session, "organization", "White House", {}, org_mention, extractor, ontology)

    link = link_entities(
        session,
        link_type="member_of",
        from_entity=person,
        to_entity=org,
        document=document,
        extractor_version=extractor,
        ontology=ontology,
        confidence=0.9,
    )

    chain = get_provenance_chain(session, "links", link.id, blob_store)
    assert len(chain) == 1
    assert chain[0].document_id == document.id


def test_create_document_stores_collected_at_when_given(session, blob_store):
    from datetime import datetime, timezone

    historical = datetime(2020, 1, 1, tzinfo=timezone.utc)
    document = create_document(
        session, source="test", text="old news", content_hash="h6", blob_store=blob_store, collected_at=historical
    )
    assert document.collected_at == historical


def test_create_document_defaults_collected_at_to_now(session, blob_store):
    from datetime import datetime, timezone

    before = datetime.now(timezone.utc)
    document = create_document(session, source="test", text="fresh news", content_hash="h7", blob_store=blob_store)
    after = datetime.now(timezone.utc)

    assert document.collected_at is not None
    assert before <= document.collected_at <= after


def test_record_document_fact_records_provenance(session, ontology, blob_store):
    document = create_document(session, source="test", text="some article", content_hash="h8", blob_store=blob_store)
    extractor = register_extractor_version(session, "scraper", "1.0.0")

    fact = record_document_fact(session, document, "title", "Some Article Title", extractor, ontology)

    assert fact.fact_type == "title"
    assert fact.payload == {"value": "Some Article Title"}
    chain = get_provenance_chain(session, "facts", fact.id, blob_store)
    assert len(chain) == 1
    assert chain[0].document_id == document.id


def test_record_document_fact_rejects_undeclared_fact_type(session, ontology, blob_store):
    document = create_document(session, source="test", text="some article", content_hash="h9", blob_store=blob_store)
    extractor = register_extractor_version(session, "scraper", "1.0.0")

    with pytest.raises(ValueError, match="not declared"):
        record_document_fact(session, document, "not_a_real_attribute", "x", extractor, ontology)


def test_record_document_fact_supersede_links_without_mutating_original(session, ontology, blob_store):
    document = create_document(session, source="test", text="some article", content_hash="h10", blob_store=blob_store)
    extractor = register_extractor_version(session, "scraper", "1.0.0")

    original = record_document_fact(session, document, "title", "Draft Title", extractor, ontology)
    revised = record_document_fact(session, document, "title", "Final Title", extractor, ontology, supersedes=original)

    assert revised.supersedes_id == original.id
    assert original.payload == {"value": "Draft Title"}  # untouched — P5, never mutated in place
