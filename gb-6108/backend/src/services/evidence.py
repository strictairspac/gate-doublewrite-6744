"""Migration of run evidence from whole-package JSON to per-page storage.

Lifecycle:
    backfilling -> backfilled -> switched -> reclaimed
                     ^             |
                     +--- withdraw (restores the whole package) ---+

Reads always go through `load_result`: the package stays authoritative until a
fully verified switch, and every rendered page is tagged with its real source.
"""
from time import time

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update

from src.constants.job_state import JobState
from src.constants.migration_state import MigrationState, SOURCE_PACKAGE, SOURCE_PAGE
from src.domain.evidence_fingerprint import (
    decode_row,
    encode_row,
    page_fingerprint,
    result_fingerprint,
)
from src.models.evidence import evidence_migrations, evidence_pages
from src.models.runs import runs
from . import audit


# How many newly-finished runs a single worker pass picks up.
BATCH_LIMIT = 5


class VerificationError(Exception):
    """Page order, body or fingerprint does not match the source package."""


def _upsert_page(db, run_id, page_no, row):
    values = {
        "page_key": row["页号"],
        "row_json": encode_row(row),
        "fingerprint": page_fingerprint(page_no, row),
    }
    existing_id = db.execute(
        select(evidence_pages.c.id).where(
            (evidence_pages.c.run_id == run_id) & (evidence_pages.c.page_no == page_no)
        )
    ).scalar_one_or_none()
    if existing_id is None:
        db.execute(insert(evidence_pages).values(run_id=run_id, page_no=page_no, **values))
    else:
        # Retrying a resumed backfill overwrites the same page instead of
        # inserting a duplicate, so already-completed pages stay write-once.
        db.execute(update(evidence_pages).where(evidence_pages.c.id == existing_id).values(**values))


def _stored_pages(db, run_id):
    rows = db.execute(
        select(evidence_pages).where(evidence_pages.c.run_id == run_id).order_by(evidence_pages.c.page_no)
    ).mappings().all()
    return [dict(row) for row in rows]


def verify_pages(db, run_id, source_result):
    """Check page order, body and fingerprints against the source package.

    Raises VerificationError describing the first mismatch; returns the rows
    rebuilt from per-page storage when every check passes.
    """
    expected_rows = source_result["rows"]
    stored = _stored_pages(db, run_id)
    if len(stored) != len(expected_rows):
        raise VerificationError(
            f"页数不一致：整包 {len(expected_rows)} 页，页存储 {len(stored)} 页"
        )
    rebuilt_rows = []
    for index, (expected, page) in enumerate(zip(expected_rows, stored), start=1):
        if page["page_no"] != index:
            raise VerificationError(f"第 {index} 页页序断裂，实际为 {page['page_no']}")
        if page["page_key"] != expected["页号"]:
            raise VerificationError(f"第 {index} 页页号不符：{page['page_key']} ≠ {expected['页号']}")
        row = decode_row(page["row_json"])
        if row.get("读取文本") != expected.get("读取文本"):
            raise VerificationError(f"第 {index} 页（{page['page_key']}）正文与整包不一致")
        if page["fingerprint"] != page_fingerprint(index, expected):
            raise VerificationError(f"第 {index} 页（{page['page_key']}）证据指纹不匹配")
        rebuilt_rows.append(row)
    rebuilt = {**source_result, "rows": rebuilt_rows}
    if result_fingerprint(rebuilt) != result_fingerprint(source_result):
        raise VerificationError("页存储重建结果与整包摘要指纹不一致")
    return rebuilt_rows


def backfill_run(db, run_id, actor="worker"):
    """Idempotently backfill one run. Safe to retry after an interruption.

    On resume, pages at or below the persisted `last_page_no` are trusted as
    already written and skipped; only the remaining pages are upserted, so a
    retry never rewrites a page that was backfilled before the interruption.
    """
    run = db.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    if run["status"] != JobState.DONE or not run["result"]:
        raise HTTPException(409, "仅成功完成的批次可回填页存储")
    result = run["result"]
    source_digest = result_fingerprint(result)
    existing = db.execute(
        select(evidence_migrations).where(evidence_migrations.c.run_id == run_id)
    ).mappings().first()
    if existing is None:
        db.execute(insert(evidence_migrations).values(
            run_id=run_id, state=MigrationState.BACKFILLING, page_count=len(result["rows"]),
            source_digest=source_digest, source_snapshot=result, last_page_no=0, updated_at=int(time()),
        ))
        resume_from = 1
        already_complete = False
    elif existing["state"] in (MigrationState.SWITCHED, MigrationState.RECLAIMED):
        return existing["state"]
    else:
        # backfilling (resumed after a crash) or backfilled (explicit retry).
        resume_from = (existing["last_page_no"] or 0) + 1
        already_complete = existing["state"] == MigrationState.BACKFILLED
    for page_no, row in enumerate(result["rows"], start=1):
        if page_no < resume_from:
            continue
        _upsert_page(db, run_id, page_no, row)
        db.execute(update(evidence_migrations).where(evidence_migrations.c.run_id == run_id).values(
            last_page_no=page_no, updated_at=int(time()),
        ))
    # Full verification before promotion: page order, body and fingerprints
    # must all match the source package, otherwise the package stays active.
    verify_pages(db, run_id, result)
    db.execute(update(evidence_migrations).where(evidence_migrations.c.run_id == run_id).values(
        state=MigrationState.BACKFILLED, page_count=len(result["rows"]),
        last_page_no=len(result["rows"]), updated_at=int(time()),
    ))
    if not already_complete:
        audit.record(db, "evidence_backfill", actor, "run", run_id)
    return MigrationState.BACKFILLED


def backfill_pending(db, limit=BATCH_LIMIT, actor="worker"):
    """Pick up finished runs that have no migration row yet.

    A run written late by an old process is caught on the next scan because the
    queue derives from run state rather than any fixed run-id list.
    """
    pending = db.execute(
        select(runs.c.id)
        .select_from(runs.outerjoin(evidence_migrations, evidence_migrations.c.run_id == runs.c.id))
        .where((runs.c.status == JobState.DONE)
               & runs.c.result.isnot(None)
               & evidence_migrations.c.run_id.is_(None))
        .order_by(runs.c.id)
        .limit(limit)
    ).scalars().all()
    processed = []
    for run_id in pending:
        backfill_run(db, run_id, actor)
        processed.append(run_id)
    return processed


def switch_run(db, run_id, actor):
    migration = _require_migration(db, run_id)
    if migration["state"] != MigrationState.BACKFILLED:
        raise HTTPException(409, "仅已回填且核对通过的批次可以切换读取来源")
    # Re-verify against the immutable snapshot immediately before switching.
    verify_pages(db, run_id, migration["source_snapshot"])
    db.execute(update(evidence_migrations).where(evidence_migrations.c.run_id == run_id).values(
        state=MigrationState.SWITCHED, updated_at=int(time()),
    ))
    audit.record(db, "evidence_switch", actor, "run", run_id)
    return describe(db, run_id)


def reclaim_run(db, run_id, actor):
    """Drop the redundant package copy on the run row.

    The snapshot on the migration row is intentionally retained, so a withdraw
    can still restore the original evidence.
    """
    migration = _require_migration(db, run_id)
    if migration["state"] != MigrationState.SWITCHED:
        raise HTTPException(409, "仅切换到页存储的批次可以回收整包副本")
    db.execute(update(runs).where(runs.c.id == run_id).values(result=None, digest=None))
    db.execute(update(evidence_migrations).where(evidence_migrations.c.run_id == run_id).values(
        state=MigrationState.RECLAIMED, updated_at=int(time()),
    ))
    audit.record(db, "evidence_reclaim", actor, "run", run_id)
    return describe(db, run_id)


def withdraw_run(db, run_id, actor):
    """Withdraw the switch and restore the original whole-package evidence.

    The page store must still verify against the retained snapshot; otherwise
    the withdrawal is refused so no unchecked evidence path is left behind.
    """
    migration = _require_migration(db, run_id)
    if migration["state"] not in (MigrationState.SWITCHED, MigrationState.RECLAIMED):
        raise HTTPException(409, "仅已切换的批次可以撤回")
    snapshot = migration["source_snapshot"]
    try:
        verify_pages(db, run_id, snapshot)
    except VerificationError as exc:
        raise HTTPException(409, f"页存储核对未通过，无法安全撤回：{exc}") from exc
    db.execute(update(runs).where(runs.c.id == run_id).values(
        result=snapshot, digest=result_fingerprint(snapshot),
    ))
    db.execute(update(evidence_migrations).where(evidence_migrations.c.run_id == run_id).values(
        state=MigrationState.BACKFILLED, updated_at=int(time()),
    ))
    audit.record(db, "evidence_withdraw", actor, "run", run_id)
    return describe(db, run_id)


def _require_migration(db, run_id):
    migration = db.execute(
        select(evidence_migrations).where(evidence_migrations.c.run_id == run_id)
    ).mappings().first()
    if migration is None:
        raise HTTPException(404, "该批次尚未回填页存储")
    return dict(migration)


def load_result(db, run_id):
    """Return (result, source) using the storage currently authoritative.

    After a switch every page is read from per-page storage; on any
    verification failure the reader falls back to the retained package
    snapshot instead of serving inconsistent evidence.
    """
    run = db.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    migration = db.execute(
        select(evidence_migrations).where(evidence_migrations.c.run_id == run_id)
    ).mappings().first()
    if migration is None or migration["state"] == MigrationState.BACKFILLING:
        return run["result"], SOURCE_PACKAGE
    if migration["state"] == MigrationState.BACKFILLED:
        return run["result"] or migration["source_snapshot"], SOURCE_PACKAGE
    # switched / reclaimed: per-page storage is authoritative.
    snapshot = migration["source_snapshot"]
    try:
        rebuilt_rows = verify_pages(db, run_id, snapshot)
    except VerificationError:
        # Switch effectively failed at read time: keep serving the original.
        return snapshot, SOURCE_PACKAGE
    return {**snapshot, "rows": rebuilt_rows}, SOURCE_PAGE


def tag_rows(result, source):
    tagged = dict(result)
    tagged["rows"] = [{**row, "存储位置": source} for row in result["rows"]]
    return tagged


def describe(db, run_id):
    migration = db.execute(
        select(evidence_migrations).where(evidence_migrations.c.run_id == run_id)
    ).mappings().first()
    if migration is None:
        return None
    written = db.execute(
        select(func.count()).select_from(evidence_pages).where(evidence_pages.c.run_id == run_id)
    ).scalar_one()
    return {
        "run_id": run_id,
        "state": migration["state"],
        "page_count": migration["page_count"],
        "written_pages": written,
        "source_digest": migration["source_digest"],
        "updated_at": migration["updated_at"],
    }


def list_entries(db):
    migrations = db.execute(
        select(evidence_migrations, runs.c.case_id)
        .join(runs, runs.c.id == evidence_migrations.c.run_id)
        .order_by(evidence_migrations.c.run_id.desc())
    ).mappings().all()
    entries = []
    for item in migrations:
        written = db.execute(
            select(func.count()).select_from(evidence_pages)
            .where(evidence_pages.c.run_id == item["run_id"])
        ).scalar_one()
        entries.append({
            "run_id": item["run_id"],
            "case_id": item["case_id"],
            "state": item["state"],
            "page_count": item["page_count"],
            "written_pages": written,
            "source_digest": item["source_digest"],
            "updated_at": item["updated_at"],
        })
    return entries
