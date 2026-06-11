from fastapi import APIRouter, HTTPException

from app.dependencies import speech_providers
from app.domain import ErrorResponse, SynthesisRequest, SynthesisResponse
from app.providers.speech.registry import get_default_tts_voice, get_tts_voice, get_variant_capability

router = APIRouter()


def _variant_value(variant: str) -> str:
    return getattr(variant, "value", variant)


@router.post(
    "/synthesize",
    response_model=SynthesisResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def synthesize_text(request: SynthesisRequest) -> SynthesisResponse:
    capability = get_variant_capability(request.target_variant)
    if capability is None:
        raise HTTPException(status_code=404, detail="Ukjent samisk variant")
    if capability.capability.value != "audio":
        raise HTTPException(
            status_code=409,
            detail=f"Valgt variant ({_variant_value(request.target_variant)}) har ikke verifisert audio-stotte i denne fasen",
        )

    resolved_voice = get_default_tts_voice(request.target_variant)
    if request.target_voice is not None:
        resolved_voice = get_tts_voice(request.target_variant, request.target_voice)
    if request.target_voice is not None and resolved_voice is None:
        raise HTTPException(status_code=404, detail="Ukjent stemme for valgt variant")

    provider = speech_providers[request.target_variant]
    try:
        audio_url = provider.synthesize(request.text, voice=request.target_voice)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if audio_url is None:
        raise HTTPException(status_code=409, detail="Audio-runtime returnerte ingen artefakt for valgt variant")
    return SynthesisResponse(
        audio_url=audio_url,
        voice=resolved_voice.voice if resolved_voice is not None else None,
        capability=capability.capability,
    )
