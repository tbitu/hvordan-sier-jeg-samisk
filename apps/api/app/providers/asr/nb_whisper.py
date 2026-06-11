from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
from shutil import which
from typing import Any
import wave


class NbWhisperProvider:
    def __init__(
        self,
        model_id: str,
        stub_mode: bool = True,
        *,
        runtime: str = "transformers",
        language: str = "no",
        chunk_length_s: int = 28,
        batch_size: int = 8,
        num_beams: int = 5,
        return_timestamps: str = "false",
        device: str = "auto",
        dtype: str = "auto",
        trust_remote_code: bool = False,
        model_dir: Path | None = None,
        attn_implementation: str = "sdpa",
    ) -> None:
        self.model_id = model_id
        self.stub_mode = stub_mode
        self.runtime = runtime
        self.language = language
        self.chunk_length_s = chunk_length_s
        self.batch_size = batch_size
        self.num_beams = num_beams
        self.return_timestamps = return_timestamps
        self.device = device
        self.dtype = dtype
        self.trust_remote_code = trust_remote_code
        self.model_dir = model_dir
        self.attn_implementation = attn_implementation
        self._transcriber: Callable[[Path], str] | None = None

    def transcribe(self, audio_path: Path) -> str:
        if self.stub_mode:
            return f"Stub transkripsjon for {audio_path.name} via {self.model_id}"
        if self.runtime != "transformers":
            raise RuntimeError(f"Ukjent ASR-runtime: {self.runtime}")
        if self._transcriber is None:
            self._transcriber = self._build_transformers_runtime()
        return self._transcriber(audio_path)

    def _build_transformers_runtime(self) -> Callable[[Path], str]:
        try:
            import torch
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError(
                "Transformers-runtime for nb-whisper-large krever `transformers`, `tokenizers`, `safetensors` og en fungerende torch-installering. "
                "Installer apps/api med inference-ekstraene eller bruk inferenscontaineren."
            ) from exc

        model_kwargs: dict[str, Any] = {"trust_remote_code": self.trust_remote_code}
        if self.model_dir is not None:
            model_kwargs["cache_dir"] = str(self.model_dir)
        trust_remote_code = model_kwargs.pop("trust_remote_code")
        torch_dtype = self._resolve_torch_dtype(torch)
        if torch_dtype is not None:
            model_kwargs["dtype"] = torch_dtype
        if self.attn_implementation:
            model_kwargs["attn_implementation"] = self.attn_implementation

        pipeline_kwargs: dict[str, Any] = {
            "task": "automatic-speech-recognition",
            "model": self.model_id,
            "tokenizer": self.model_id,
            "feature_extractor": self.model_id,
            "model_kwargs": model_kwargs,
            "trust_remote_code": trust_remote_code,
        }
        device = self._resolve_runtime_device(torch)
        if device is not None:
            pipeline_kwargs["device"] = device

        asr = pipeline(**pipeline_kwargs)

        def run(audio_path: Path) -> str:
            audio_input = self._prepare_audio_input(audio_path)
            generate_kwargs = {"task": "transcribe", "language": self.language, "num_beams": self.num_beams}
            result = asr(
                audio_input,
                chunk_length_s=self.chunk_length_s,
                batch_size=self.batch_size,
                return_timestamps=self._resolve_return_timestamps(),
                generate_kwargs=generate_kwargs,
            )
            text = result.get("text") if isinstance(result, dict) else None
            if not text:
                raise RuntimeError("nb-whisper-large returnerte ikke transkribert tekst")
            return text.strip()

        return run
    def _resolve_runtime_device(self, torch: Any) -> str | int | None:
        if self.device in {"auto", ""}:
            if torch.cuda.is_available():
                return 0
            return -1
        if self.device == "cpu":
            return -1
        if self.device.isdigit():
            return int(self.device)
        return self.device

    def _resolve_return_timestamps(self) -> bool | str:
        value = self.return_timestamps.lower()
        if value == "false":
            return False
        if value == "true":
            return True
        return value

    def _resolve_torch_dtype(self, torch: Any) -> Any | None:
        dtype_name = self.dtype.lower()
        if dtype_name == "auto":
            return None
        return getattr(torch, dtype_name, None)

    def _prepare_audio_input(self, audio_path: Path) -> str | dict[str, Any]:
        torchaudio_audio = self._load_with_torchaudio(audio_path)
        if torchaudio_audio is not None:
            return torchaudio_audio
        if audio_path.suffix.lower() == ".wav":
            return self._load_wav(audio_path)
        ffmpeg_path = self._find_ffmpeg_executable()
        if ffmpeg_path is None:
            raise RuntimeError(
                "Lokal nb-whisper-runtime uten torchaudio-backend eller ffmpeg stotter forelopig bare WAV-filer. "
                "Bruk WAV som input eller installer imageio-ffmpeg i valgt runtime-miljo."
            )
        return self._decode_with_ffmpeg(audio_path, ffmpeg_path)

    def _load_with_torchaudio(self, audio_path: Path) -> dict[str, Any] | None:
        try:
            import torch
            import torchaudio
        except ImportError:
            return None

        try:
            waveform, sample_rate = torchaudio.load(str(audio_path))
        except (ImportError, RuntimeError, OSError):
            return None
        waveform = waveform.to(torch.float32)
        if waveform.ndim > 1 and waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        waveform = waveform.squeeze(0).cpu().numpy()
        return {"array": waveform, "sampling_rate": sample_rate}

    def _load_wav(self, audio_path: Path) -> dict[str, Any]:
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("WAV-stotte for nb-whisper-large krever numpy i inference-miljoet") from exc

        with wave.open(str(audio_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            raw_audio = wav_file.readframes(frame_count)

        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(sample_width)
        if dtype is None:
            raise RuntimeError(f"Ustottet WAV sample width: {sample_width}")

        waveform = np.frombuffer(raw_audio, dtype=dtype).astype(np.float32)
        if channels > 1:
            waveform = waveform.reshape(-1, channels).mean(axis=1)
        scale = float(np.iinfo(dtype).max)
        waveform = waveform / max(scale, 1.0)
        return {"array": waveform, "sampling_rate": frame_rate}

    def _find_ffmpeg_executable(self) -> str | None:
        system_ffmpeg = which("ffmpeg")
        if system_ffmpeg is not None:
            return system_ffmpeg
        try:
            import imageio_ffmpeg
        except ImportError:
            return None
        return imageio_ffmpeg.get_ffmpeg_exe()

    def _decode_with_ffmpeg(self, audio_path: Path, ffmpeg_path: str) -> dict[str, Any]:
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("ffmpeg-dekoding for nb-whisper-large krever numpy i inference-miljoet") from exc

        command = [
            ffmpeg_path,
            "-i",
            str(audio_path),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-hide_banner",
            "-loglevel",
            "error",
            "pipe:1",
        ]
        process = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        waveform = np.frombuffer(process.stdout, dtype=np.float32)
        if waveform.size == 0:
            raise RuntimeError("ffmpeg-dekoding returnerte tomt lydsignal")
        return {"array": waveform, "sampling_rate": 16000}
