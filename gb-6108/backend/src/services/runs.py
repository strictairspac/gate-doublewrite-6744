from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select

from src.constants.job_state import JobState
from src.domain.scenario import CONFIG
from src.models.cases import cases
from src.models.runs import runs
from . import audit, cases as case_service, evidence


def query():
    return select(runs, cases.c.title.label("case_title")).join(cases, cases.c.id == runs.c.case_id)


def get(db, run_id):
    run = db.execute(query().where(runs.c.id == run_id)).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    run = dict(run)
    # Serve evidence through the storage that is currently authoritative and
    # tag each page with the source it was actually read from.
    if run["status"] == JobState.DONE:
        result, source = evidence.load_result(db, run_id)
        run["result"] = evidence.tag_rows(result, source)
        run["storage_source"] = source
    else:
        run["storage_source"] = None
    return run


def list_entries(db):
    entries = [dict(row) for row in db.execute(query().order_by(runs.c.id.desc()).limit(100)).mappings()]
    for entry in entries:
        if entry["status"] == JobState.DONE:
            result, source = evidence.load_result(db, entry["id"])
            entry["result"] = evidence.tag_rows(result, source)
            entry["storage_source"] = source
        else:
            entry["storage_source"] = None
    return entries


def enqueue(db, case_id, parameter, actor):
    if parameter > CONFIG["max_parameter"]:
        raise HTTPException(422, f"参数不能超过 {CONFIG['max_parameter']}")
    case_service.get(db, case_id)
    change = db.execute(insert(runs).values(
        case_id=case_id, status=JobState.QUEUED, parameter=parameter, actor=actor, created_at=int(time())
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "run", run_id)
    return get(db, run_id)
