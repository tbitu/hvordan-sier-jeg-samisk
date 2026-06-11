from fastapi import APIRouter, HTTPException

from app.dependencies import job_store
from app.domain import JobRecord

router = APIRouter()


@router.get("/jobs", response_model=list[JobRecord])
def list_jobs() -> list[JobRecord]:
    return job_store.list()


@router.get("/jobs/{job_id}", response_model=JobRecord)
def get_job(job_id: str) -> JobRecord:
    record = job_store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Jobb finnes ikke")
    return record
