import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import get_job_store
from app.domain import ErrorResponse, JobRecord

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/jobs", response_model=list[JobRecord], responses={503: {"model": ErrorResponse}})
def list_jobs() -> list[JobRecord]:
    try:
        return get_job_store().list()
    except Exception as exc:
        # A store failure (failing open, a locked DB past the busy timeout, or a
        # failed reopen) must surface as a declared 503, not an unmodeled 500.
        logger.exception("Could not list jobs")
        raise HTTPException(status_code=503, detail="Jobblageret er utilgjengelig: kan ikke hente jobbene akkurat nå") from exc


@router.get(
    "/jobs/{job_id}",
    response_model=JobRecord,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def get_job(job_id: str) -> JobRecord:
    try:
        record = get_job_store().get(job_id)
    except Exception as exc:
        logger.exception("Could not fetch job %s", job_id)
        raise HTTPException(status_code=503, detail="Jobblageret er utilgjengelig: kan ikke hente jobben akkurat nå") from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Jobb finnes ikke")
    return record
