from __future__ import annotations

from pathlib import Path

from app.providers.speech.common import SpeechSynthesisConfig, synthesize_with_config

class SouthSamiSpeechProvider:
    def __init__(
        self,
        artifacts_dir: Path,
        *,
        stub_mode: bool = True,
        synth_config: SpeechSynthesisConfig | None = None,
    ) -> None:
        self.artifacts_dir = artifacts_dir
        self.stub_mode = stub_mode
        self.synth_config = synth_config or SpeechSynthesisConfig()

    def phonemize(self, text: str) -> str:
        return f"/sma/ {text}"

    def synthesize(self, text: str, voice: str | None = None) -> str | None:
        return synthesize_with_config(
            variant="sma",
            text=text,
            artifacts_dir=self.artifacts_dir,
            stub_mode=self.stub_mode,
            config=self.synth_config,
            voice=voice,
        )
