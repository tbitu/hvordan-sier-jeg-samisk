from fastapi import APIRouter

from app.dependencies import pipeline_service
from app.domain import TranslationRequest, TranslationResponse

router = APIRouter()


@router.post("/translate", response_model=TranslationResponse)
def translate_text(request: TranslationRequest) -> TranslationResponse:
    translated_text = pipeline_service.translator.translate(request.text, request.target_variant)
    return TranslationResponse(translated_text=translated_text)
