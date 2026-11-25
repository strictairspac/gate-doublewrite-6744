from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from .base import metadata


# One row per run: tracks the lifecycle of moving its evidence out of the
# whole-package JSON into per-page storage.
evidence_migrations = Table(
    "evidence_migrations", metadata,
    Column("run_id", ForeignKey("runs.id"), primary_key=True),
    Column("state", String(16), nullable=False, index=True),
    Column("page_count", Integer, nullable=False, default=0),
    # SHA-256 of the canonical whole-package result, captured at backfill time.
    Column("source_digest", String(64), nullable=False),
    # Immutable whole-package snapshot, kept so a switch can be withdrawn even
    # after the redundant copy on the run row has been reclaimed.
    Column("source_snapshot", JSON(none_as_null=True), nullable=False),
    Column("last_page_no", Integer, nullable=False, default=0),
    Column("updated_at", Integer, nullable=False),
)

# Per-page evidence. Rows are keyed by (run_id, page_no) so a retried or
# resumed backfill can upsert without ever double-writing a page.
evidence_pages = Table(
    "evidence_pages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", ForeignKey("runs.id"), nullable=False, index=True),
    Column("page_no", Integer, nullable=False),
    Column("page_key", String(32), nullable=False),
    # Canonical JSON of the original result row, including the page body.
    Column("row_json", Text, nullable=False),
    # Fingerprint of the page: page order, logical page key, body and full row.
    Column("fingerprint", String(64), nullable=False),
    UniqueConstraint("run_id", "page_no", name="uq_evidence_page_order"),
)
