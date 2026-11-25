from src.config.resources import engine
from src.services import evidence


def backfill_pending(user):
    with engine.begin() as db:
        run_ids = evidence.backfill_pending(db, actor=user["username"])
        return {"backfilled": run_ids}


def backfill_one(run_id, user):
    with engine.begin() as db:
        evidence.backfill_run(db, run_id, user["username"])
        return evidence.describe(db, run_id)


def switch(run_id, user):
    with engine.begin() as db:
        return evidence.switch_run(db, run_id, user["username"])


def reclaim(run_id, user):
    with engine.begin() as db:
        return evidence.reclaim_run(db, run_id, user["username"])


def withdraw(run_id, user):
    with engine.begin() as db:
        return evidence.withdraw_run(db, run_id, user["username"])


def list_migrations():
    with engine.connect() as db:
        return evidence.list_entries(db)
