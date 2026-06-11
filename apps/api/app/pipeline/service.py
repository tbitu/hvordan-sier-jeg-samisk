from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.domain import JobStatus, PipelineRequest, PipelineResult, PipelineStage
from app.providers.asr.nb_whisper import NbWhisperProvider
from app.providers.speech.common import SpeechProvider
from app.providers.speech.registry import get_variant_capability
from app.providers.translation.tahetorn import TahetornProvider


class PipelineService:
    def __init__(
        self,
        asr: NbWhisperProvider,
        translator: TahetornProvider,
        speech_providers: dict[str, SpeechProvider],
    ) -> None:
        self.asr = asr
        self.translator = translator
        self._speech = speech_providers

    def run(
        self,
        request: PipelineRequest,
        audio_path: Path | None = None,
        on_update: Callable[[PipelineResult], None] | None = None,
    ) -> PipelineResult:
        result = PipelineResult(audio_requested=request.include_audio)

        def push_update() -> None:
            if on_update is not None:
                on_update(result.model_copy(deep=True))

        def block_audio(stage_name: str) -> None:
            if request.include_audio and result.audio_summary is None:
                result.audio_summary = f"Audio ble ikke forsokt fordi {stage_name} feilet tidligere i jobben"

        if audio_path is not None:
            try:
                result.transcript_text = self.asr.transcribe(audio_path)
            except Exception as exc:
                result.stages.append(
                    PipelineStage(name="transcribe", status=JobStatus.failed, summary=f"Transkribering feilet: {exc}")
                )
                block_audio("transkribering")
                push_update()
                return result
            result.stages.append(PipelineStage(name="transcribe", status=JobStatus.completed, summary="Norsk lyd transkribert"))
            push_update()
        elif request.source_text:
            result.transcript_text = request.source_text
            result.stages.append(
                PipelineStage(name="transcribe", status=JobStatus.completed, summary="Transkripsjon hoppet over, kilde tekst brukt direkte")
            )
            push_update()
        else:
            result.stages.append(PipelineStage(name="transcribe", status=JobStatus.failed, summary="Ingen lyd eller tekst tilgjengelig"))
            block_audio("transkribering")
            push_update()
            return result

        try:
            result.translated_text = self.translator.translate(result.transcript_text, request.target_variant)
        except Exception as exc:
            result.stages.append(PipelineStage(name="translate", status=JobStatus.failed, summary=f"Oversetting feilet: {exc}"))
            block_audio("oversetting")
            push_update()
            return result
        result.stages.append(PipelineStage(name="translate", status=JobStatus.completed, summary="Norsk tekst oversatt til samisk malvariant"))
        push_update()

        speech_provider = self._speech[request.target_variant]
        capability = get_variant_capability(request.target_variant)

        if request.include_phonemes:
            try:
                result.phoneme_text = speech_provider.phonemize(result.translated_text)
            except Exception as exc:
                result.stages.append(
                    PipelineStage(name="phonemize", status=JobStatus.failed, summary=f"Uttalerepresentasjon feilet: {exc}")
                )
                block_audio("fonemisering")
                push_update()
                return result
            result.stages.append(PipelineStage(name="phonemize", status=JobStatus.completed, summary="Tekst konvertert til uttalerepresentasjon"))
        else:
            result.stages.append(PipelineStage(name="phonemize", status=JobStatus.completed, summary="Fonemsteg hoppet over for denne jobben"))
        push_update()

        if request.include_audio and capability and capability.capability.value == "audio":
            try:
                result.audio_url = speech_provider.synthesize(result.translated_text, voice=request.target_voice)
            except RuntimeError as exc:
                result.audio_available = False
                result.audio_summary = str(exc)
                result.stages.append(PipelineStage(name="synthesize", status=JobStatus.failed, summary=str(exc)))
                push_update()
                return result
            except Exception as exc:
                result.audio_available = False
                result.audio_summary = f"Audio-generering feilet: {exc}"
                result.stages.append(PipelineStage(name="synthesize", status=JobStatus.failed, summary=f"Audio-generering feilet: {exc}"))
                push_update()
                return result
            else:
                if result.audio_url is None:
                    result.audio_available = False
                    result.audio_summary = "Audio-runtime returnerte ingen artefakt for valgt variant"
                    result.stages.append(
                        PipelineStage(
                            name="synthesize",
                            status=JobStatus.failed,
                            summary="Audio-runtime returnerte ingen artefakt for valgt variant",
                        )
                    )
                    push_update()
                    return result
                else:
                    result.audio_available = True
                    result.audio_summary = "Audio artefakt generert eller reservert"
                    result.stages.append(
                        PipelineStage(name="synthesize", status=JobStatus.completed, summary="Audio artefakt generert eller reservert")
                    )
        elif request.include_audio:
            result.audio_available = False
            result.audio_summary = "Valgt variant mangler verifisert audio-stotte i denne fasen"
            result.stages.append(
                PipelineStage(name="synthesize", status=JobStatus.failed, summary="Valgt variant mangler verifisert audio-stotte i denne fasen")
            )
        else:
            result.audio_available = False
            result.audio_summary = "Audio hoppet over for denne jobben"
            result.stages.append(PipelineStage(name="synthesize", status=JobStatus.completed, summary="Audio hoppet over for denne jobben"))
        push_update()

        return result
