import { useEffect, useState } from "react";

export type VariantCode = "sme" | "smj" | "sma";
export type CapabilityLevel = "unavailable" | "text" | "phonemes" | "audio";

export type VariantCapability = {
  variant: VariantCode;
  label: string;
  capability: CapabilityLevel;
  notes: string;
};

export type TtsVoice = {
  variant: VariantCode;
  variant_label: string;
  voice: string;
  label: string;
  gender: string;
  is_default: boolean;
};

export type RuntimeProfile = {
  key: string;
  architecture: string;
  accelerator: string;
  container_runtime: string;
  priority: number;
};

export type ModelCacheState = {
  expected_path: string;
  exists: boolean;
  has_snapshots: boolean;
  has_incomplete: boolean;
  looks_usable: boolean;
  summary: string;
};

export type HealthResponse = {
  name: string;
  environment: string;
  stub_mode: boolean;
  provider_runtime: string;
  tts_runtime: string;
  tts_api_base_url: string | null;
  tts_api_configured: boolean;
  tts_api_reachable: boolean;
  tts_command_configured: boolean;
  tts_command_available: boolean;
  tts_variants_ready: Record<string, boolean>;
  tts_variants_local_ready: Record<string, boolean>;
  runtime_components: Record<string, boolean>;
  inference_dependencies_ready: boolean;
  inference_runtime_ready: boolean;
  configured_models: Record<string, string>;
  resolved_paths: Record<string, string>;
  local_model_cache_present: boolean;
  model_cache_state: Record<string, ModelCacheState>;
  runtime_issues: string[];
  runtime_profiles: RuntimeProfile[];
};

export type PipelineStage = {
  name: string;
  status: string;
  summary: string;
};

export type PipelineRequest = {
  target_variant: VariantCode;
  target_voice: string | null;
  source_text: string | null;
  include_phonemes: boolean;
  include_audio: boolean;
};

export type PipelineResult = {
  transcript_text: string | null;
  translated_text: string | null;
  phoneme_text: string | null;
  audio_requested: boolean;
  audio_available: boolean;
  audio_url: string | null;
  audio_summary: string | null;
  stages: PipelineStage[];
};

export type JobRecord = {
  id: string;
  status: string;
  request: PipelineRequest;
  result: PipelineResult | null;
  error: string | null;
};
