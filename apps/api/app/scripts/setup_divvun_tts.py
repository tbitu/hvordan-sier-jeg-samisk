from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys


SUPPORTED_VARIANTS = ("sme", "smj", "sma")
ENV_PREFIX = "HSJS_TTS_"


@dataclass(frozen=True)
class ModelPair:
    voice_model: Path
    vocoder_model: Path

    @property
    def parent(self) -> Path:
        return self.voice_model.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finn Divvun voice/vocoder-filer og lag en .env-snutt for lokal TTS"
    )
    parser.add_argument("--variant", default="sme", choices=SUPPORTED_VARIANTS, help="Samisk variant")
    parser.add_argument(
        "--search-root",
        action="append",
        default=[],
        help="Mappe a soke i. Kan brukes flere ganger. Default er dagens mappe og ~/Downloads hvis de finnes.",
    )
    parser.add_argument(
        "--tts-command",
        help="Path til ferdig synthesize-runner eller installert Divvun-kommando som skal settes i HSJS_TTS_COMMAND",
    )
    parser.add_argument(
        "--command-cwd",
        help="Optional arbeidsmappe for HSJS_TTS_COMMAND_CWD, for eksempel et divvun-speech-rs checkout",
    )
    parser.add_argument(
        "--write-env",
        help="Skriv eller oppdater verdiene i en lokal .env-fil i stedet for bare a skrive til stdout",
    )
    parser.add_argument(
        "--select-parent",
        help="Tving valg av en bestemt modellmappe hvis flere kandidater blir funnet",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    search_roots = _resolve_search_roots(args.search_root)
    if not search_roots:
        raise SystemExit("Fant ingen sokemapper. Oppgi minst én --search-root.")

    pair = _select_pair(search_roots, forced_parent=args.select_parent)
    command_path = _resolve_command(args.tts_command)
    command_cwd = _resolve_optional_path(args.command_cwd)

    env_lines = _build_env_lines(
        variant=args.variant,
        pair=pair,
        tts_command=command_path,
        command_cwd=command_cwd,
    )

    print(f"Valgt modellmappe: {pair.parent}")
    print(f"Voice-modell: {pair.voice_model}")
    print(f"Vocoder-modell: {pair.vocoder_model}")
    if command_path is not None:
        print(f"TTS-kommando: {command_path}")
    elif args.tts_command:
        print(f"TTS-kommando beholdes som oppgitt streng: {args.tts_command}")

    print("\nAnbefalt .env-snutt:\n")
    print("\n".join(env_lines))

    if args.write_env:
        env_path = Path(args.write_env).expanduser().resolve()
        _write_env_file(env_path, env_lines)
        print(f"\nOppdaterte {env_path}")


def _resolve_search_roots(raw_roots: list[str]) -> list[Path]:
    roots: list[Path] = []
    for raw_root in raw_roots:
        path = Path(raw_root).expanduser().resolve()
        if path.exists() and path.is_dir() and path not in roots:
            roots.append(path)

    for fallback in (Path.cwd(), Path.home() / "Downloads"):
        resolved = fallback.expanduser().resolve()
        if resolved.exists() and resolved.is_dir() and resolved not in roots:
            roots.append(resolved)

    return roots


def _select_pair(search_roots: list[Path], forced_parent: str | None) -> ModelPair:
    pairs = _discover_model_pairs(search_roots)
    if not pairs:
        searched = "\n".join(f"- {root}" for root in search_roots)
        raise SystemExit(
            "Fant ingen modellpar med voice/vocoder under disse sokemappene:\n"
            f"{searched}\n"
            "Pakk ut Borealium/Divvun-stemmepakken et sted lokalt og prov igjen."
        )

    if forced_parent is not None:
        selected_parent = Path(forced_parent).expanduser().resolve()
        for pair in pairs:
            if pair.parent == selected_parent:
                return pair
        raise SystemExit(f"Fant ikke valgt modellmappe: {selected_parent}")

    if len(pairs) == 1:
        return pairs[0]

    print("Fant flere modellpar. Bruker den forste kandidaten. Velg eksplisitt med --select-parent hvis du vil ha en annen.\n", file=sys.stderr)
    for pair in pairs:
        print(f"- {pair.parent}", file=sys.stderr)
    return pairs[0]


def _discover_model_pairs(search_roots: list[Path]) -> list[ModelPair]:
    pairs: list[ModelPair] = []
    seen_parents: set[Path] = set()

    for root in search_roots:
        grouped: dict[Path, dict[str, list[Path]]] = {}
        for path in root.rglob("*.pte"):
            if not path.is_file():
                continue
            kind = _classify_model(path)
            if kind is None:
                continue
            parent_entry = grouped.setdefault(path.parent, {"voice": [], "vocoder": []})
            parent_entry[kind].append(path)

        for parent, kinds in sorted(grouped.items()):
            voices = sorted(kinds["voice"])
            vocoders = sorted(kinds["vocoder"])
            if not voices or not vocoders or parent in seen_parents:
                continue
            pairs.append(ModelPair(voice_model=voices[0], vocoder_model=vocoders[0]))
            seen_parents.add(parent)

    pairs.sort(key=lambda pair: (len(str(pair.parent)), str(pair.parent)))
    return pairs


def _classify_model(path: Path) -> str | None:
    name = path.name.lower()
    if name == "voice.pte" or "voice" in name:
        return "voice"
    if name == "vocoder.pte" or "vocoder" in name:
        return "vocoder"
    return None


def _resolve_command(raw_command: str | None) -> Path | None:
    if raw_command is None or not raw_command.strip():
        return None

    candidate = Path(raw_command).expanduser()
    if candidate.exists():
        return candidate.resolve()

    resolved = shutil.which(raw_command)
    if resolved is not None:
        return Path(resolved).resolve()

    return None


def _resolve_optional_path(raw_path: str | None) -> Path | None:
    if raw_path is None or not raw_path.strip():
        return None
    return Path(raw_path).expanduser().resolve()


def _build_env_lines(
    *,
    variant: str,
    pair: ModelPair,
    tts_command: Path | None,
    command_cwd: Path | None,
) -> list[str]:
    variant_prefix = f"{ENV_PREFIX}{variant.upper()}"
    env_lines = [
        "HSJS_PROVIDER_STUB_MODE=false",
        "HSJS_PROVIDER_RUNTIME=transformers",
        "HSJS_TTS_RUNTIME=divvun-command",
    ]
    if tts_command is not None:
        env_lines.append(f"HSJS_TTS_COMMAND={tts_command}")
    if command_cwd is not None:
        env_lines.append(f"HSJS_TTS_COMMAND_CWD={command_cwd}")
    env_lines.extend(
        [
            f"{variant_prefix}_VOICE_MODEL={pair.voice_model}",
            f"{variant_prefix}_VOCODER_MODEL={pair.vocoder_model}",
            f"{variant_prefix}_SPEAKER_ID=1",
            f"{variant_prefix}_LANGUAGE_ID=1",
            f"{variant_prefix}_PACE=1.0",
        ]
    )
    return env_lines


def _write_env_file(env_path: Path, env_lines: list[str]) -> None:
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated_lines = existing_lines[:]

    for new_line in env_lines:
        key = new_line.split("=", 1)[0]
        replacement_index = next(
            (index for index, line in enumerate(updated_lines) if line.startswith(f"{key}=")),
            None,
        )
        if replacement_index is None:
            updated_lines.append(new_line)
        else:
            updated_lines[replacement_index] = new_line

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()