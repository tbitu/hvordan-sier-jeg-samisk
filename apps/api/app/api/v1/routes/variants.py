from fastapi import APIRouter

from app.domain import VariantSummary
from app.providers.speech.registry import get_variant_capabilities

router = APIRouter()


@router.get("/variants", response_model=list[VariantSummary])
def list_variants() -> list[VariantSummary]:
    return [VariantSummary(code=item.variant, label=item.label) for item in get_variant_capabilities()]
