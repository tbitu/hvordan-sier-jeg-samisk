from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, UploadFile

from app.dependencies import pipeline_service
from app.domain import TranscriptionResponse

router = APIRouter()


@router.post("/transcribe", response_model=TranscriptionResponse)
def transcribe_audio(audio: UploadFile = File(...)) -> TranscriptionResponse:
    suffix = Path(audio.filename or "input.wav").suffix or ".wav"
    with NamedTemporaryFile(delete=True, suffix=suffix) as temp_file:
        temp_file.write(audio.file.read())
        temp_file.flush()
        transcript = pipeline_service.asr.transcribe(Path(temp_file.name))
    return TranscriptionResponse(transcript_text=transcript)
