import type { HealthResponse, PipelineStage, VariantCapability } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

export async function pollJob(jobId: string, onUpdate: (job: any) => void): Promise<any> {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const response = await fetch(`${API_BASE}/jobs/${jobId}`);
    if (!response.ok) {
      throw new Error("Kunne ikke hente jobbstatus fra API-et");
    }
    const payload = (await response.json()) as any;
    onUpdate(payload);
    if (payload.status === "completed" || payload.status === "failed") {
      return payload;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error("Jobben brukte for lang tid. Sjekk API-loggene og prov igjen.");
}

export async function fetchCapabilities(): Promise<VariantCapability[]> {
  const response = await fetch(`${API_BASE}/capabilities`);
  if (!response.ok) {
    throw new Error("Kunne ikke hente capability-matrise fra API-et");
  }
  return (await response.json()) as VariantCapability[];
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new Error("Kunne ikke hente runtime-status fra API-et");
  }
  return (await response.json()) as HealthResponse;
}

export async function fetchVoices(): Promise<any[]> {
  const response = await fetch(`${API_BASE}/voices`);
  if (!response.ok) {
    throw new Error("Kunne ikke hente stemmer fra API-et");
  }
  return (await response.json()) as any[];
}

export function resolveArtifactUrl(audioUrl: string): string {
  try {
    return new URL(audioUrl, new URL(API_BASE).origin).toString();
  } catch {
    return audioUrl;
  }
}

export function getLatestStage(job: any): PipelineStage | null {
  if (!job?.result?.stages.length) {
    return null;
  }
  return job.result.stages[job.result.stages.length - 1] ?? null;
}

export function countCompletedStages(job: any): number {
  return job?.result?.stages.filter((stage: PipelineStage) => stage.status === "completed").length ?? 0;
}

export function listMissingRuntimeComponents(health: HealthResponse | null): string[] {
  if (health === null) {
    return [];
  }
  return Object.entries(health.runtime_components)
    .filter(([, isReady]) => !isReady)
    .map(([name]) => name)
    .sort();
}

export function resolveCapability(capabilities: VariantCapability[], variant: string): VariantCapability {
  return (
    capabilities.find((item) => item.variant === variant) ??
    FALLBACK_CAPABILITIES.find((item) => item.variant === variant) ??
    FALLBACK_CAPABILITIES[0]
  );
}

export function describeAudioMode(health: HealthResponse | null, capability: VariantCapability): string {
  if (capability.capability !== "audio") {
    return "Denne varianten kjorer tekst og fonemer, men ikke verifisert audio i denne fasen.";
  }
  if (health === null) {
    return "Runtime-status er ikke hentet ennå. Frontend antar at audio kan proves nar API-et svarer.";
  }
  if (health.stub_mode) {
    return "API-et kjorer i stub-modus. Audio blir en lokal demo-WAV som beviser hele jobb-lopet uten ekte TTS.";
  }
  if (health.tts_variants_ready[capability.variant]) {
    return "Divvun TTS-API er klart for denne varianten. Frontend ber om ekte audio i pipeline-kallet.";
  }
  return "TTS er ikke klar for denne varianten. Frontend ber bare om tekst og fonemer til API-et rapporterer at audio er klart.";
}

export function isVariantAudioReady(health: HealthResponse | null, capability: VariantCapability): boolean {
  if (capability.capability !== "audio") {
    return false;
  }
  if (health === null) {
    return true;
  }
  return health.stub_mode || Boolean(health.tts_variants_ready[capability.variant]);
}

export const FALLBACK_CAPABILITIES: VariantCapability[] = [
  {
    variant: "sme",
    label: "Nordsamisk",
    capability: "audio",
    notes: "Har verifisert audio-lop i denne fasen nar lokal TTS-runtime er konfigurert.",
  },
  {
    variant: "smj",
    label: "Lulesamisk",
    capability: "audio",
    notes: "Kan bruke audio-lop nar lokal TTS-runtime er konfigurert.",
  },
  {
    variant: "sma",
    label: "Sorsamisk",
    capability: "audio",
    notes: "Bruker offentlig Divvun TTS-API med Aanna som tilgjengelig stemme.",
  },
];

export const FALLBACK_VOICES: any[] = [
  { variant: "sme", variant_label: "Nordsamisk", voice: "biret", label: "Biret", gender: "female", is_default: true },
  { variant: "sme", variant_label: "Nordsamisk", voice: "mahtte", label: "Mahtte", gender: "male", is_default: false },
  { variant: "sme", variant_label: "Nordsamisk", voice: "sunna", label: "Sunna", gender: "female", is_default: false },
  { variant: "smj", variant_label: "Lulesamisk", voice: "abmut", label: "Abmut", gender: "male", is_default: true },
  { variant: "smj", variant_label: "Lulesamisk", voice: "nihkol", label: "Nihkol", gender: "male", is_default: false },
  { variant: "smj", variant_label: "Lulesamisk", voice: "sigga", label: "Sigga", gender: "female", is_default: false },
  { variant: "sma", variant_label: "Sorsamisk", voice: "aanna", label: "Aanna", gender: "female", is_default: true },
];

export function resolveVoices(voices: any[], variant: string): any[] {
  const variantVoices = voices.filter((item) => item.variant === variant);
  if (variantVoices.length > 0) {
    return variantVoices;
  }
  return FALLBACK_VOICES.filter((item) => item.variant === variant);
}

export function resolveDefaultVoice(voices: any[], variant: string): any | null {
  const variantVoices = resolveVoices(voices, variant);
  return variantVoices.find((item) => item.is_default) ?? variantVoices[0] ?? null;
}
