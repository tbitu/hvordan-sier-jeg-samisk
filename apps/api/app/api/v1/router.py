from fastapi import APIRouter

from app.api.v1.routes import capabilities, health, jobs, pipeline, synthesize, transcribe, translate, variants, voices

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(variants.router, tags=["variants"])
api_router.include_router(voices.router, tags=["voices"])
api_router.include_router(capabilities.router, tags=["capabilities"])
api_router.include_router(transcribe.router, tags=["transcribe"])
api_router.include_router(translate.router, tags=["translate"])
api_router.include_router(synthesize.router, tags=["synthesize"])
api_router.include_router(pipeline.router, tags=["pipeline"])
api_router.include_router(jobs.router, tags=["jobs"])
