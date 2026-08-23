"""Add the private bilingual derived-data foundation.

Revision ID: 0006_bilingual_derived_data
Revises: 0005_stable_entity_generations
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_bilingual_derived_data"
down_revision: str | None = "0005_stable_entity_generations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "translation_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_lang", sa.String(length=16), nullable=False),
        sa.Column("target_lang", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_version", sa.String(length=32), nullable=False),
        sa.Column("settings_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_text_sha256",
            "source_lang",
            "target_lang",
            "provider",
            "provider_version",
            "settings_sha256",
            name="uq_translation_artifacts_content_address",
        ),
    )
    op.create_index(
        "ix_translation_artifacts_source_text_sha256",
        "translation_artifacts",
        ["source_text_sha256"],
    )

    op.create_table(
        "translation_uses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("evidence_identity_id", sa.Integer(), nullable=False),
        sa.Column("source_membership_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["translation_artifacts.id"]),
        sa.ForeignKeyConstraint(
            ["evidence_identity_id"], ["evidence_identities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_membership_id", "evidence_identity_id"],
            [
                "stable_entity_memberships.id",
                "stable_entity_memberships.evidence_identity_id",
            ],
            name="fk_translation_uses_membership_evidence",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id",
            "evidence_identity_id",
            "source_membership_id",
            name="uq_translation_uses_identity",
        ),
    )
    op.create_index(
        "ix_translation_uses_artifact_id", "translation_uses", ["artifact_id"]
    )
    op.create_index(
        "ix_translation_uses_evidence_identity_id",
        "translation_uses",
        ["evidence_identity_id"],
    )

    op.create_table(
        "stable_entity_variants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stable_entity_id", sa.Integer(), nullable=False),
        sa.Column("variant_type", sa.String(length=20), nullable=False),
        sa.Column("surface_text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("source_membership_id", sa.Integer(), nullable=False),
        sa.Column("evidence_identity_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "variant_type IN ('romanization', 'verified_alias')",
            name="ck_stable_entity_variant_type",
        ),
        sa.ForeignKeyConstraint(["stable_entity_id"], ["stable_entities.id"]),
        sa.ForeignKeyConstraint(
            ["evidence_identity_id"], ["evidence_identities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_membership_id", "evidence_identity_id"],
            [
                "stable_entity_memberships.id",
                "stable_entity_memberships.evidence_identity_id",
            ],
            name="fk_stable_entity_variants_membership_evidence",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stable_entity_id",
            "variant_type",
            "surface_text",
            "language",
            name="uq_stable_entity_variants_identity",
        ),
    )
    op.create_index(
        "ix_stable_entity_variants_stable_entity_id",
        "stable_entity_variants",
        ["stable_entity_id"],
    )
    op.create_index(
        "ix_stable_entity_variants_evidence_identity_id",
        "stable_entity_variants",
        ["evidence_identity_id"],
    )

    # The append-only trigger function already exists (created in
    # 0005_stable_entity_generations). It is generic -- it raises using
    # TG_TABLE_NAME rather than a hardcoded table -- so these new immutable
    # tables reuse it instead of defining a near-duplicate function.
    if op.get_bind().dialect.name == "postgresql":
        for table_name in (
            "translation_artifacts",
            "translation_uses",
            "stable_entity_variants",
        ):
            op.execute(
                "CREATE TRIGGER trg_"
                f"{table_name}_append_only "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION "
                "reject_stable_entity_history_mutation()"
            )


def downgrade() -> None:
    raise RuntimeError(
        "The bilingual derived-data foundation is forward-only. Restore a "
        "database backup or apply a forward repair instead of deleting "
        "translation/romanization history."
    )
