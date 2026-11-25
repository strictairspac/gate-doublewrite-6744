"""Lifecycle tests for whole-package -> per-page evidence migration.

Runs against an in-memory SQLite database so the suite needs no MySQL, Redis
or third-party package beyond SQLAlchemy (already a project dependency).

Run:
    cd backend && python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select, update
from sqlalchemy.pool import StaticPool

from src.constants.job_state import JobState
from src.constants.migration_state import MigrationState, SOURCE_PACKAGE, SOURCE_PAGE
from src.domain.evidence_fingerprint import page_fingerprint
from src.models.audit import audit
from src.models.base import metadata
from src.models.cases import cases
from src.models.evidence import evidence_migrations, evidence_pages
from src.models.runs import runs
from src.services import evidence
from src.utils.serial import content_digest


def sample_result():
    return {
        "summary": "核查 3 页，修复 2 页，隔离 1 页。",
        "metrics": {"已修复": 2, "隔离页": 1, "原页通过": 0},
        "rows": [
            {"页号": "N1-021", "结论": "从双写区修复", "读取文本": "闸门 07:10 提升至 1.2 米"},
            {"页号": "N1-022", "结论": "保留数据页", "读取文本": "闸门 07:12 平稳运行"},
            {"页号": "N1-023", "结论": "隔离", "读取文本": None},
        ],
    }


class EvidenceMigrationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as db:
            db.execute(cases.insert().values(
                id=1, code="GT-N1-1124", title="北一闸", description="d", payload={"pages": []},
                created_by="seed",
            ))
            self.run_id = self._insert_run(1, sample_result())

    def _insert_run(self, run_id, result, status=JobState.DONE):
        with self.engine.begin() as db:
            db.execute(runs.insert().values(
                id=run_id, case_id=1, status=status, parameter=6, actor="operator",
                created_at=1000, finished_at=1001, result=result,
                digest=content_digest(result) if result else None,
            ))
        return run_id

    def _migration(self, db, run_id):
        return dict(db.execute(
            select(evidence_migrations).where(evidence_migrations.c.run_id == run_id)
        ).mappings().one())

    # 1. Backfill writes one row per page and is idempotent across retries.
    def test_backfill_is_idempotent_and_resumable(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            # Simulate an interrupted/replayed backfill: running again must not
            # create duplicate page rows.
            evidence.backfill_run(db, self.run_id, "operator")
            migration = self._migration(db, self.run_id)
            page_count = len(db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
            ).all())
        self.assertEqual(migration["state"], MigrationState.BACKFILLED)
        self.assertEqual(migration["page_count"], 3)
        self.assertEqual(page_count, 3)

    # 2. Reads keep coming from the package until a verified switch.
    def test_reads_use_package_before_switch_and_tag_source(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            result, source = evidence.load_result(db, self.run_id)
        self.assertEqual(source, SOURCE_PACKAGE)
        self.assertEqual(result["rows"][0]["读取文本"], "闸门 07:10 提升至 1.2 米")
        tagged = evidence.tag_rows(result, source)
        self.assertTrue(all(row["存储位置"] == SOURCE_PACKAGE for row in tagged["rows"]))

    # 3. Switch requires order/body/fingerprint verification; tampering blocks it.
    def test_switch_blocked_when_fingerprint_tampered(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            stored = db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
                .order_by(evidence_pages.c.page_no)
            ).mappings().all()
            tampered = dict(stored[1])
            tampered["fingerprint"] = "deadbeef"
            db.execute(update(evidence_pages).where(evidence_pages.c.id == stored[1].id).values(
                fingerprint="deadbeef"
            ))
            with self.assertRaises(evidence.VerificationError):
                evidence.switch_run(db, self.run_id, "operator")
            migration = self._migration(db, self.run_id)
        self.assertEqual(migration["state"], MigrationState.BACKFILLED)

    def test_switch_blocked_when_page_order_broken(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            first = db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
                .order_by(evidence_pages.c.page_no)
            ).mappings().first()
            db.execute(update(evidence_pages).where(evidence_pages.c.id == first.id).values(page_no=9))
            with self.assertRaises(evidence.VerificationError):
                evidence.switch_run(db, self.run_id, "operator")

    def test_switch_blocked_when_body_diverges(self):
        from src.domain.evidence_fingerprint import encode_row
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            target = db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
                .order_by(evidence_pages.c.page_no)
            ).mappings().first()
            bad_row = {"页号": "N1-021", "结论": "从双写区修复", "读取文本": "被篡改的正文"}
            db.execute(update(evidence_pages).where(evidence_pages.c.id == target.id).values(
                row_json=encode_row(bad_row),
                fingerprint=page_fingerprint(1, bad_row),
            ))
            with self.assertRaises(evidence.VerificationError):
                evidence.switch_run(db, self.run_id, "operator")

    # 4. Full happy path: switch -> page reads -> reclaim -> withdraw restores.
    def test_switch_reclaim_withdraw_lifecycle(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            entry = evidence.switch_run(db, self.run_id, "operator")
            self.assertEqual(entry["state"], MigrationState.SWITCHED)
            result, source = evidence.load_result(db, self.run_id)
            self.assertEqual(source, SOURCE_PAGE)
            self.assertTrue(all(row["存储位置"] == SOURCE_PAGE
                                for row in evidence.tag_rows(result, source)["rows"]))

            evidence.reclaim_run(db, self.run_id, "operator")
            run = dict(db.execute(select(runs).where(runs.c.id == self.run_id)).mappings().one())
            self.assertIsNone(run["result"])
            migration = self._migration(db, self.run_id)
            self.assertEqual(migration["state"], MigrationState.RECLAIMED)
            # Reads still work after reclaim, from per-page storage.
            result, source = evidence.load_result(db, self.run_id)
            self.assertEqual(source, SOURCE_PAGE)
            self.assertEqual(result["rows"][0]["读取文本"], "闸门 07:10 提升至 1.2 米")

            evidence.withdraw_run(db, self.run_id, "operator")
            run = dict(db.execute(select(runs).where(runs.c.id == self.run_id)).mappings().one())
            self.assertEqual(run["result"], sample_result())
            self.assertEqual(run["digest"], content_digest(sample_result()))
            result, source = evidence.load_result(db, self.run_id)
            self.assertEqual(source, SOURCE_PACKAGE)

    # 5. A run finished late (no migration row) is picked up by the pending scan.
    def test_late_finished_run_is_discovered(self):
        late_id = self._insert_run(2, sample_result())
        with self.engine.begin() as db:
            # First run already migrated; the scan must only discover run 2.
            evidence.backfill_run(db, self.run_id, "operator")
            discovered = evidence.backfill_pending(db)
        self.assertEqual(discovered, [late_id])
        with self.engine.begin() as db:
            self.assertEqual(self._migration(db, late_id)["state"], MigrationState.BACKFILLED)

    # 6. Queued runs are never touched by backfill.
    def test_queued_run_excluded_from_backfill(self):
        self._insert_run(3, None, status=JobState.QUEUED)
        with self.engine.begin() as db:
            discovered = evidence.backfill_pending(db)
        self.assertEqual(discovered, [self.run_id])

    # 7. A corrupted page store after switching fails over to the package.
    def test_post_switch_corruption_falls_back_to_package(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            evidence.switch_run(db, self.run_id, "operator")
            target = db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
                .order_by(evidence_pages.c.page_no)
            ).mappings().first()
            db.execute(update(evidence_pages).where(evidence_pages.c.id == target.id).values(
                fingerprint="0" * 64
            ))
            result, source = evidence.load_result(db, self.run_id)
        self.assertEqual(source, SOURCE_PACKAGE)
        self.assertEqual(result["rows"], sample_result()["rows"])

    # 8. Audit trail covers every migration action.
    def test_audit_events_recorded(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            evidence.switch_run(db, self.run_id, "operator")
            evidence.reclaim_run(db, self.run_id, "operator")
            evidence.withdraw_run(db, self.run_id, "operator")
            actions = db.execute(select(audit.c.action).where(audit.c.entity == "run")
                                 .where(audit.c.entity_id == self.run_id)).scalars().all()
        self.assertEqual(
            actions,
            ["evidence_backfill", "evidence_switch", "evidence_reclaim", "evidence_withdraw"],
        )

    # 9. Migration listing reports written page counts.
    def test_listing_reports_progress(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            entries = evidence.list_entries(db)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["written_pages"], 3)
        self.assertEqual(entries[0]["page_count"], 3)

    # 10. An interrupted backfill resumes at the checkpoint and never rewrites
    # pages already completed before the crash.
    def test_resumed_backfill_skips_completed_pages(self):
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            first_attempt = dict(db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
                .order_by(evidence_pages.c.page_no)
            ).mappings().all()[0])
            # Simulate a crash after page 1: reset state back to backfilling.
            db.execute(update(evidence_migrations)
                       .where(evidence_migrations.c.run_id == self.run_id)
                       .values(state=MigrationState.BACKFILLING, last_page_no=1))
            # Corrupt pages 2-3 rows so a full rewrite would either crash or
            # change fingerprints; resume must skip page 1 and only fix later.
            later = db.execute(
                select(evidence_pages).where(
                    (evidence_pages.c.run_id == self.run_id)
                    & (evidence_pages.c.page_no > 1))
            ).mappings().all()
            for page in later:
                db.execute(update(evidence_pages)
                           .where(evidence_pages.c.id == page.id)
                           .values(row_json="{}", fingerprint="0" * 64))
            evidence.backfill_run(db, self.run_id, "worker")
            resumed = dict(db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
                .order_by(evidence_pages.c.page_no)
            ).mappings().all()[0])
        # Page 1 row identity is untouched: resumed backfill did not rewrite it.
        self.assertEqual(resumed["id"], first_attempt["id"])
        self.assertEqual(resumed["fingerprint"], first_attempt["fingerprint"])

    # 11. Withdrawing a switch is refused when per-page storage no longer
    # verifies, and the package remains readable via the fallback path.
    def test_withdraw_refused_when_page_store_corrupt(self):
        from fastapi import HTTPException
        with self.engine.begin() as db:
            evidence.backfill_run(db, self.run_id, "operator")
            evidence.switch_run(db, self.run_id, "operator")
            target = db.execute(
                select(evidence_pages).where(evidence_pages.c.run_id == self.run_id)
                .order_by(evidence_pages.c.page_no)
            ).mappings().first()
            db.execute(update(evidence_pages).where(evidence_pages.c.id == target.id).values(
                fingerprint="f" * 64
            ))
            with self.assertRaises(HTTPException) as context:
                evidence.withdraw_run(db, self.run_id, "operator")
            self.assertEqual(context.exception.status_code, 409)
            migration = self._migration(db, self.run_id)
            result, source = evidence.load_result(db, self.run_id)
        self.assertEqual(migration["state"], MigrationState.SWITCHED)
        # Reads fail over to the package instead of serving corrupt pages.
        self.assertEqual(source, SOURCE_PACKAGE)
        self.assertEqual(result["rows"], sample_result()["rows"])


if __name__ == "__main__":
    unittest.main()
