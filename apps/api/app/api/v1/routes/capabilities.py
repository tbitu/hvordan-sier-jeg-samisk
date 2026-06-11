from fastapi import APIRouter

from app.domain import VariantCapability
from app.providers.speech.registry import get_variant_capabilities

router = APIRouter()


@router.get("/capabilities", response_model=list[VariantCapability])
def get_capabilities() -> list[VariantCapability]:
    return get_variant_capabilities()
