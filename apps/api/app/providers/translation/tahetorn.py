from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class TahetornProvider:
    VARIANT_LABELS = {
        "sme": "nordsamisk",
        "smj": "lulesamisk",
        "sma": "sorsamisk",
    }

    def __init__(
        self,
        model_id: str,
        stub_mode: bool = True,
        *,
        runtime: str = "transformers",
        device: str = "auto",
        dtype: str = "auto",
        trust_remote_code: bool = False,
        model_dir: Path | None = None,
        system_prompt: str = "",
        max_new_tokens: int = 256,
        temperature: float = 0.0,
        top_p: float = 1.0,
        repetition_penalty: float = 1.05,
        attn_implementation: str = "sdpa",
        use_chat_template: bool = True,
    ) -> None:
        self.model_id = model_id
        self.stub_mode = stub_mode
        self.runtime = runtime
        self.device = device
        self.dtype = dtype
        self.trust_remote_code = trust_remote_code
        self.model_dir = model_dir
        self.system_prompt = system_prompt
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.repetition_penalty = repetition_penalty
        self.attn_implementation = attn_implementation
        self.use_chat_template = use_chat_template
        self._translator: Callable[[str, str], str] | None = None

    @staticmethod
    def _normalize_variant(target_variant: str) -> str:
        return getattr(target_variant, "value", target_variant)

    def translate(self, text: str, target_variant: str) -> str:
        normalized_variant = self._normalize_variant(target_variant)
        if self.stub_mode:
            return f"[{normalized_variant}] {text.strip()}"
        if self.runtime != "transformers":
            raise RuntimeError(f"Ukjent oversettingsruntime: {self.runtime}")
        if self._translator is None:
            self._translator = self._build_transformers_runtime()
        return self._translator(text, normalized_variant)

    def _build_transformers_runtime(self) -> Callable[[str, str], str]:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Transformers-runtime for Tahetorn_9B krever `transformers`, `tokenizers`, `safetensors` og en fungerende torch-installering. "
                "Installer apps/api med inference-ekstraene eller bruk inferenscontaineren."
            ) from exc

        pretrained_kwargs: dict[str, Any] = {"trust_remote_code": self.trust_remote_code}
        if self.model_dir is not None:
            pretrained_kwargs["cache_dir"] = str(self.model_dir)

        tokenizer = AutoTokenizer.from_pretrained(self.model_id, **pretrained_kwargs)
        model_kwargs: dict[str, Any] = {"trust_remote_code": self.trust_remote_code}
        if self.model_dir is not None:
            model_kwargs["cache_dir"] = str(self.model_dir)
        torch_dtype = self._resolve_torch_dtype(torch)
        if torch_dtype is not None:
            model_kwargs["dtype"] = torch_dtype
        if self.attn_implementation:
            model_kwargs["attn_implementation"] = self.attn_implementation

        if self.device == "auto":
            model = AutoModelForCausalLM.from_pretrained(
                self.model_id,
                device_map="auto",
                **model_kwargs,
            )
        else:
            model = AutoModelForCausalLM.from_pretrained(
                self.model_id,
                **model_kwargs,
            )
            model.to(self.device)
        model.eval()
        if getattr(model.generation_config, "cache_implementation", None) is not None:
            model.generation_config.cache_implementation = None

        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        pad_token_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id

        def run(text: str, target_variant: str) -> str:
            inputs = self._prepare_inputs(tokenizer, text, target_variant)
            model_device = next(model.parameters()).device
            inputs = {key: value.to(model_device) for key, value in inputs.items()}
            do_sample = self.temperature > 0
            generation_kwargs: dict[str, Any] = {
                **inputs,
                "max_new_tokens": self.max_new_tokens,
                "do_sample": do_sample,
                "repetition_penalty": self.repetition_penalty,
                "pad_token_id": pad_token_id,
                "eos_token_id": tokenizer.eos_token_id,
            }
            if do_sample:
                generation_kwargs["temperature"] = max(self.temperature, 1e-5)
                generation_kwargs["top_p"] = self.top_p

            generated = model.generate(**generation_kwargs)
            prompt_length = inputs["input_ids"].shape[-1]
            completion_ids = generated[0][prompt_length:]
            completion = tokenizer.decode(completion_ids, skip_special_tokens=True).strip()
            translated_text = self._clean_completion(completion)
            if not translated_text:
                raise RuntimeError("Tahetorn_9B returnerte tom oversetting")
            return translated_text

        return run

    def _prepare_inputs(self, tokenizer: Any, text: str, target_variant: str) -> dict[str, Any]:
        prompt = self._build_prompt(text, target_variant)
        if self.use_chat_template and getattr(tokenizer, "chat_template", None):
            messages = [
                {"role": "system", "content": self.system_prompt.strip()},
                {"role": "user", "content": prompt},
            ]
            return tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
                return_dict=True,
            )
        return tokenizer(prompt, return_tensors="pt")

    def _build_prompt(self, text: str, target_variant: str) -> str:
        variant_label = self.VARIANT_LABELS.get(target_variant, target_variant)
        return (
            f"Malvariant: {variant_label}\n"
            "Kildesprak: norsk\n"
            "Oppgave: Oversett teksten til valgt samisk variant og svar bare med oversettelsen.\n"
            f"Tekst: {text.strip()}\n"
            "Oversettelse:"
        )

    def _clean_completion(self, completion: str) -> str:
        if not completion:
            return ""
        lines = [line.strip() for line in completion.splitlines() if line.strip()]
        if not lines:
            return ""
        first_line = lines[0]
        if ":" in first_line and first_line.lower().startswith("oversettelse"):
            _, _, remainder = first_line.partition(":")
            first_line = remainder.strip()
        return first_line

    def _resolve_torch_dtype(self, torch: Any) -> Any | None:
        dtype_name = self.dtype.lower()
        if dtype_name == "auto":
            return None
        return getattr(torch, dtype_name, None)
