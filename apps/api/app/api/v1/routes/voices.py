from fastapi import APIRouter

from app.domain import TtsVoice
from app.providers.speech.registry import get_tts_voices

router = APIRouter()


@router.get("/voices", response_model=list[TtsVoice])
def list_voices() -> list[TtsVoice]:
    return get_tts_voices()