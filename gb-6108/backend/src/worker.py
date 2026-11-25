import logging
import time

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.config.resources import cache, engine
from src.services import evidence
from src.services.execution import JobFailure, fail, service_one

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")


def check():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    heartbeat = cache.get("workbench:heartbeat")
    return heartbeat is not None and time.time() - float(heartbeat) < 90


def backfill_evidence():
    # Migration runs alongside normal inspection. Every scan re-derives the
    # pending set from run state, so a batch finished late by an old process
    # is picked up without any manual registration. Upserts make retries safe.
    try:
        with engine.begin() as db:
            run_ids = evidence.backfill_pending(db, actor="worker")
        if run_ids:
            logger.info("Evidence backfilled for runs %s", run_ids)
    except Exception:
        # A single bad batch must not take down the inspection loop; the next
        # scan resumes from the persisted checkpoint.
        logger.exception("Evidence backfill pass failed")


def work():
    while True:
        try:
            cache.set("workbench:heartbeat", time.time(), ex=90)
            try:
                if service_one():
                    continue
            except JobFailure as exc:
                logger.exception("Execution %s failed", exc.run_id)
                fail(exc)
                continue
            backfill_evidence()
            cache.blpop("workbench:wake", timeout=1)
        except (RedisError, SQLAlchemyError):
            logger.exception("Worker connection unavailable")
            time.sleep(2)


if __name__ == "__main__":
    work()
