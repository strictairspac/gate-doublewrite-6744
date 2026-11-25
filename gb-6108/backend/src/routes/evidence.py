from fastapi import APIRouter, Depends

from src.controllers.access import identity, operator
from src.controllers import evidence as evidence_controller
from src.schemas.runs import MigrationEntry

router = APIRouter(prefix="/evidence-migrations", tags=["Evidence Storage"])


@router.get("", response_model=list[MigrationEntry], dependencies=[Depends(identity)])
def list_migrations():
    return evidence_controller.list_migrations()


@router.post("/backfill", dependencies=[Depends(operator)])
def backfill_pending(user=Depends(operator)):
    return evidence_controller.backfill_pending(user)


@router.post("/runs/{run_id}/backfill", response_model=MigrationEntry, dependencies=[Depends(operator)])
def backfill_one(run_id: int, user=Depends(operator)):
    return evidence_controller.backfill_one(run_id, user)


@router.post("/runs/{run_id}/switch", response_model=MigrationEntry, dependencies=[Depends(operator)])
def switch(run_id: int, user=Depends(operator)):
    return evidence_controller.switch(run_id, user)


@router.post("/runs/{run_id}/reclaim", response_model=MigrationEntry, dependencies=[Depends(operator)])
def reclaim(run_id: int, user=Depends(operator)):
    return evidence_controller.reclaim(run_id, user)


@router.post("/runs/{run_id}/withdraw", response_model=MigrationEntry, dependencies=[Depends(operator)])
def withdraw(run_id: int, user=Depends(operator)):
    return evidence_controller.withdraw(run_id, user)
