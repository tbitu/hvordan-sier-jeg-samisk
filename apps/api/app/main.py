from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.runtime_readiness import collect_runtime_diagnostics
from app.core.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
artifacts_dir = Path(settings.artifacts_dir)
artifacts_dir.mkdir(parents=True, exist_ok=True)
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/artifacts", StaticFiles(directory=str(artifacts_dir)), name="artifacts")
app.include_router(api_router, prefix=settings.api_prefix)


@app.on_event("startup")
def log_runtime_readiness() -> None:
    diagnostics = collect_runtime_diagnostics(settings)
    if diagnostics.runtime_issues:
        for issue in diagnostics.runtime_issues:
            logger.warning("Runtime readiness: %s", issue)
    else:
        logger.info("Runtime readiness: lokal konfigurasjon ser klar ut")


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "api": settings.api_prefix}
