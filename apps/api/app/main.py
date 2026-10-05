from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.runtime_readiness import collect_runtime_diagnostics
from app.core.settings import get_settings
from app.dependencies import close_job_store, get_job_store, recover_interrupted_jobs

logger = logging.getLogger(__name__)
settings = get_settings()
artifacts_dir = Path(settings.artifacts_dir)
artifacts_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    diagnostics = collect_runtime_diagnostics(settings)
    if diagnostics.runtime_issues:
        for issue in diagnostics.runtime_issues:
            logger.warning("Runtime readiness: %s", issue)
    else:
        logger.info("Runtime readiness: lokal konfigurasjon ser klar ut")
    # Open the store eagerly so a corrupt or locked DB fails at startup instead of
    # surfacing as an unmodeled 500 on the first job request while health stays green.
    store = get_job_store()
    try:
        recovered = recover_interrupted_jobs(store)
        if recovered:
            logger.warning("Recovered %d interrupted job(s) after unclean restart", recovered)
    except BaseException:
        # A startup failure after the store opened must not leak it into the
        # global: the next in-process startup would otherwise reuse the leaked
        # instance (with the failed startup's DB path) instead of rebuilding
        # from current settings.
        close_job_store()
        raise
    try:
        yield
    finally:
        # Teardown must be exception-safe, symmetric with the guarded startup:
        # an error delivered at the yield point (or raised by close itself) must
        # run the close and then propagate the ORIGINAL failure, not mask it.
        try:
            close_job_store()
        except Exception:
            logger.exception("Could not close the job store during shutdown")


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/artifacts", StaticFiles(directory=str(artifacts_dir)), name="artifacts")
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "api": settings.api_prefix}
