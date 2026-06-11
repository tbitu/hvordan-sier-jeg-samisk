import os
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_api_root() -> Path:
    current_file = Path(__file__).resolve()
    for candidate in current_file.parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "app").is_dir():
            return candidate
    return current_file.parents[2]


def _find_repo_root(api_root: Path) -> Path:
    for candidate in api_root.parents:
        if (candidate / "package.json").exists() and (candidate / "apps").is_dir():
            return candidate
    return api_root


API_ROOT = _find_api_root()
REPO_ROOT = _find_repo_root(API_ROOT)


def _resolve_repo_path(path: Path | None) -> Path | None:
    if path is None or path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _default_hf_cache_dir() -> Path:
    explicit_cache = os.getenv("HF_HUB_CACHE") or os.getenv("HUGGINGFACE_HUB_CACHE")
    if explicit_cache:
        return Path(explicit_cache).expanduser().resolve()

    hf_home = os.getenv("HF_HOME")
    if hf_home:
        return (Path(hf_home).expanduser() / "hub").resolve()

    xdg_cache_home = os.getenv("XDG_CACHE_HOME")
    if xdg_cache_home:
        return (Path(xdg_cache_home).expanduser() / "huggingface" / "hub").resolve()

    return (Path.home() / ".cache" / "huggingface" / "hub").resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HSJS_", env_file=".env", extra="ignore")

    app_name: str = "Hvordan sier jeg pa samisk"
    api_prefix: str = "/api/v1"
    environment: str = "development"
    artifacts_dir: Path = Path(".artifacts")
    provider_stub_mode: bool = True
    provider_runtime: str = "transformers"
    host: str = "0.0.0.0"
    port: int = 8000
    rocm_runtime_image: str = "ghcr.io/example/hsjs-inference:rocm"
    cuda_runtime_image: str = "ghcr.io/example/hsjs-inference:cuda"
    cpu_runtime_image: str = "ghcr.io/example/hsjs-inference:cpu"
    tahetorn_model_id: str = "tartuNLP/Tahetorn_9B"
    whisper_model_id: str = "NbAiLab/nb-whisper-large"
    whisper_language: str = "no"
    whisper_chunk_length_s: int = 28
    whisper_batch_size: int = 8
    whisper_num_beams: int = 5
    whisper_return_timestamps: str = "false"
    whisper_attn_implementation: str = "sdpa"
    translation_system_prompt: str = "Du er en oversettingsmotor. Oversett norsk tekst til valgt samisk variant og svar bare med selve oversettelsen."
    translation_max_new_tokens: int = 256
    translation_temperature: float = 0.0
    translation_top_p: float = 1.0
    translation_repetition_penalty: float = 1.05
    translation_attn_implementation: str = "sdpa"
    translation_use_chat_template: bool = True
    hf_device: str = "auto"
    hf_dtype: str = "auto"
    hf_trust_remote_code: bool = False
    default_variant: str = "sme"
    supported_variants: list[str] = Field(default_factory=lambda: ["sme", "smj", "sma"])
    tts_runtime: str = "divvun-api"
    tts_api_base_url: str = "https://api-giellalt.uit.no/tts"
    tts_command: str = ""
    tts_command_cwd: Path | None = None
    tts_sme_voice_model: Path | None = None
    tts_sme_vocoder_model: Path | None = None
    tts_sme_speaker_id: int = 1
    tts_sme_language_id: int = 1
    tts_sme_pace: float = 1.0
    tts_smj_voice_model: Path | None = None
    tts_smj_vocoder_model: Path | None = None
    tts_smj_speaker_id: int = 1
    tts_smj_language_id: int = 1
    tts_smj_pace: float = 1.0
    tts_sma_voice_model: Path | None = None
    tts_sma_vocoder_model: Path | None = None
    tts_sma_speaker_id: int = 1
    tts_sma_language_id: int = 1
    tts_sma_pace: float = 1.0

    def model_post_init(self, __context: object) -> None:
        self.artifacts_dir = _resolve_repo_path(self.artifacts_dir) or self.artifacts_dir
        self.tts_command_cwd = _resolve_repo_path(self.tts_command_cwd)
        self.tts_sme_voice_model = _resolve_repo_path(self.tts_sme_voice_model)
        self.tts_sme_vocoder_model = _resolve_repo_path(self.tts_sme_vocoder_model)
        self.tts_smj_voice_model = _resolve_repo_path(self.tts_smj_voice_model)
        self.tts_smj_vocoder_model = _resolve_repo_path(self.tts_smj_vocoder_model)
        self.tts_sma_voice_model = _resolve_repo_path(self.tts_sma_voice_model)
        self.tts_sma_vocoder_model = _resolve_repo_path(self.tts_sma_vocoder_model)

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def api_root(self) -> Path:
        return API_ROOT

    @property
    def effective_model_cache_dir(self) -> Path:
        return _default_hf_cache_dir()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
