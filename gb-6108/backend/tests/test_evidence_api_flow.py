"""End-to-end HTTP flow for evidence storage migration.

Uses FastAPI's TestClient against SQLite with Redis stubbed, so the whole
operator workflow can be exercised without docker, MySQL or a live worker.
"""
import os
import sys
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/evidence_e2e.sqlite")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "Demo-DbWorkbench-2026")
os.environ.setdefault("SEED_DEMO", "false")

# Stub the redis package before application imports it.
redis_stub = types.ModuleType("redis")


class FakeRedis:
    def __init__(self, *args, **kwargs):
        self.values = {}

    @classmethod
    def from_url(cls, *args, **kwargs):
        return cls()

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value

    def ping(self):
        return True

    def lpush(self, key, value):
        self.values.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, end):
        self.values[key] = self.values.get(key, [])[: end + 1]

    def blpop(self, key, timeout=0):
        return None


redis_stub.Redis = FakeRedis
redis_stub.exceptions = types.ModuleType("redis.exceptions")
redis_stub.exceptions.RedisError = Exception
sys.modules["redis"] = redis_stub
sys.modules["redis.exceptions"] = redis_stub.exceptions

# Fresh database for this run.
db_path = Path("/tmp/evidence_e2e.sqlite")
db_path.unlink(missing_ok=True)

from fastapi.testclient import TestClient

from src.constants.roles import Role
from src.main import app
from src.services.auth import login
from src.utils.passwords import hash_password
from src.models.users import users
from src.config.resources import engine


class EvidenceApiFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        with cls.client:  # triggers lifespan -> schema setup
            pass
        with engine.begin() as db:
            for role in Role:
                db.execute(users.insert().values(
                    username=role.value, role=role.value,
                    password_hash=hash_password("Demo-DbWorkbench-2026"),
                ))
        token = login("operator", "Demo-DbWorkbench-2026")["token"]
        cls.headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "pages": [
                {"page": "N1-021", "generation": 2, "event": "torn",
                 "before": "旧版启闭记录", "after": "新版启闭记录 07:10"},
                {"page": "N1-022", "generation": 3, "event": "complete",
                 "before": "旧版平稳记录", "after": "新版平稳记录 07:12"},
                {"page": "N1-023", "generation": 1, "event": "both",
                 "before": "旧版双损记录", "after": "新版双损记录 07:15"},
            ]
        }
        case = cls.client.post("/cases", json={
            "title": "迁移联调批", "description": "端到端验证", "payload": payload,
        }, headers=cls.headers)
        cls.case_id = case.json()["id"]

    def _enqueue_and_finish(self):
        run = self.client.post(f"/cases/{self.case_id}/runs", json={"parameter": 6}, headers=self.headers)
        self.assertEqual(run.status_code, 202)
        run_id = run.json()["id"]
        # Run the worker pipeline synchronously.
        from src.services import execution
        self.assertTrue(execution.service_one())
        detail = self.client.get(f"/runs/{run_id}", headers=self.headers).json()
        self.assertEqual(detail["status"], "done")
        return run_id, detail

    def test_full_migration_flow_over_http(self):
        run_id, detail = self._enqueue_and_finish()
        # Before migration every page is served from the whole package.
        self.assertEqual(detail["storage_source"], "整包存储")
        self.assertTrue(all(row["存储位置"] == "整包存储" for row in detail["result"]["rows"]))

        # Worker-style sweep discovers the late-finished run.
        sweep = self.client.post("/evidence-migrations/backfill", headers=self.headers)
        self.assertEqual(sweep.status_code, 200)
        self.assertEqual(sweep.json()["backfilled"], [run_id])

        listing = self.client.get("/evidence-migrations", headers=self.headers).json()
        entry = next(item for item in listing if item["run_id"] == run_id)
        self.assertEqual(entry["state"], "backfilled")
        self.assertEqual(entry["written_pages"], 3)

        # Still reading the package until the verified switch.
        detail = self.client.get(f"/runs/{run_id}", headers=self.headers).json()
        self.assertEqual(detail["storage_source"], "整包存储")

        switched = self.client.post(f"/evidence-migrations/runs/{run_id}/switch", headers=self.headers)
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["state"], "switched")

        detail = self.client.get(f"/runs/{run_id}", headers=self.headers).json()
        self.assertEqual(detail["storage_source"], "页存储")
        self.assertTrue(all(row["存储位置"] == "页存储" for row in detail["result"]["rows"]))
        self.assertEqual(detail["result"]["rows"][0]["读取文本"], "新版启闭记录 07:10")

        # Reclaim the redundant package copy; page reads must keep working.
        reclaimed = self.client.post(f"/evidence-migrations/runs/{run_id}/reclaim", headers=self.headers)
        self.assertEqual(reclaimed.json()["state"], "reclaimed")
        detail = self.client.get(f"/runs/{run_id}", headers=self.headers).json()
        self.assertIsNone(detail["digest"])
        self.assertEqual(detail["storage_source"], "页存储")

        # Export still works and carries the migration descriptor.
        export = self.client.get(f"/runs/{run_id}/export", headers=self.headers)
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export.json()["migration"]["state"], "reclaimed")

        # Withdraw restores the original whole-package evidence.
        withdrawn = self.client.post(f"/evidence-migrations/runs/{run_id}/withdraw", headers=self.headers)
        self.assertEqual(withdrawn.json()["state"], "backfilled")
        detail = self.client.get(f"/runs/{run_id}", headers=self.headers).json()
        self.assertEqual(detail["storage_source"], "整包存储")
        self.assertIsNotNone(detail["digest"])
        self.assertTrue(all(row["存储位置"] == "整包存储" for row in detail["result"]["rows"]))

    def test_run_listing_joins_each_case_title(self):
        # Two cases with one run each guards the runs/cases join against
        # degenerating into a cartesian or self-join.
        payload = {"pages": [{"page": "N1-099", "generation": 1, "event": "complete",
                              "before": "旧", "after": "新"}]}
        second_case = self.client.post("/cases", json={
            "title": "第二份资料", "payload": payload,
        }, headers=self.headers).json()["id"]
        first_run, _ = self._enqueue_and_finish()
        run2 = self.client.post(f"/cases/{second_case}/runs", json={"parameter": 6},
                                headers=self.headers).json()["id"]
        from src.services import execution
        execution.service_one()
        listing = {item["id"]: item for item in self.client.get("/runs", headers=self.headers).json()}
        self.assertEqual(listing[first_run]["case_id"], self.case_id)
        self.assertEqual(listing[run2]["case_id"], second_case)
        self.assertNotEqual(listing[first_run]["case_title"], listing[run2]["case_title"])
        self.assertEqual(listing[run2]["case_title"], "第二份资料")

    def test_viewer_cannot_migrate(self):
        run_id, _ = self._enqueue_and_finish()
        viewer_token = login("viewer", "Demo-DbWorkbench-2026")["token"]
        denied = self.client.post(
            f"/evidence-migrations/runs/{run_id}/backfill",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        self.assertEqual(denied.status_code, 403)
        # Viewer can still observe migration status.
        visible = self.client.get("/evidence-migrations",
                                  headers={"Authorization": f"Bearer {viewer_token}"})
        self.assertEqual(visible.status_code, 200)


if __name__ == "__main__":
    unittest.main()
