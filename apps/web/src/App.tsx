import { useEffect, useState } from "react";
import { useLiveMic } from "./features/live-mic/useLiveMic";

type VariantCode = "sme" | "smj" | "sma";
type CapabilityLevel = "unavailable" | "text" | "phonemes" | "audio";

type VariantCapability = {
  variant: VariantCode;
  label: string;
  capability: CapabilityLevel;
  notes: string;
};

type TtsVoice = {
  variant: VariantCode;
  variant_label: string;
  voice: string;
  label: string;
  gender: string;
  is_default: boolean;
};

type RuntimeProfile = {
  key: string;
  architecture: string;
  accelerator: string;
  container_runtime: string;
  priority: number;
};

type ModelCacheState = {
  expected_path: string;
  exists: boolean;
  has_snapshots: boolean;
  has_incomplete: boolean;
  looks_usable: boolean;
  summary: string;
};

type HealthResponse = {
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

type PipelineStage = {
  name: string;
  status: string;
  summary: string;
};

type PipelineRequest = {
  target_variant: VariantCode;
  target_voice: string | null;
  source_text: string | null;
  include_phonemes: boolean;
  include_audio: boolean;
};

type PipelineResult = {
  transcript_text: string | null;
  translated_text: string | null;
  phoneme_text: string | null;
  audio_requested: boolean;
  audio_available: boolean;
  audio_url: string | null;
  audio_summary: string | null;
  stages: PipelineStage[];
};

type JobRecord = {
  id: string;
  status: string;
  request: PipelineRequest;
  result: PipelineResult | null;
  error: string | null;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
const FALLBACK_CAPABILITIES: VariantCapability[] = [
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

const FALLBACK_VOICES: TtsVoice[] = [
  { variant: "sme", variant_label: "Nordsamisk", voice: "biret", label: "Biret", gender: "female", is_default: true },
  { variant: "sme", variant_label: "Nordsamisk", voice: "mahtte", label: "Mahtte", gender: "male", is_default: false },
  { variant: "sme", variant_label: "Nordsamisk", voice: "sunna", label: "Sunna", gender: "female", is_default: false },
  { variant: "smj", variant_label: "Lulesamisk", voice: "abmut", label: "Abmut", gender: "male", is_default: true },
  { variant: "smj", variant_label: "Lulesamisk", voice: "nihkol", label: "Nihkol", gender: "male", is_default: false },
  { variant: "smj", variant_label: "Lulesamisk", voice: "sigga", label: "Sigga", gender: "female", is_default: false },
  { variant: "sma", variant_label: "Sorsamisk", voice: "aanna", label: "Aanna", gender: "female", is_default: true },
];

function resolveArtifactUrl(audioUrl: string): string {
  try {
    return new URL(audioUrl, new URL(API_BASE).origin).toString();
  } catch {
    return audioUrl;
  }
}

async function pollJob(jobId: string, onUpdate: (job: JobRecord) => void): Promise<JobRecord> {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const response = await fetch(`${API_BASE}/jobs/${jobId}`);
    if (!response.ok) {
      throw new Error("Kunne ikke hente jobbstatus fra API-et");
    }
    const payload = (await response.json()) as JobRecord;
    onUpdate(payload);
    if (payload.status === "completed" || payload.status === "failed") {
      return payload;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error("Jobben brukte for lang tid. Sjekk API-loggene og prov igjen.");
}

async function fetchCapabilities(): Promise<VariantCapability[]> {
  const response = await fetch(`${API_BASE}/capabilities`);
  if (!response.ok) {
    throw new Error("Kunne ikke hente capability-matrise fra API-et");
  }
  return (await response.json()) as VariantCapability[];
}

async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new Error("Kunne ikke hente runtime-status fra API-et");
  }
  return (await response.json()) as HealthResponse;
}

async function fetchVoices(): Promise<TtsVoice[]> {
  const response = await fetch(`${API_BASE}/voices`);
  if (!response.ok) {
    throw new Error("Kunne ikke hente stemmer fra API-et");
  }
  return (await response.json()) as TtsVoice[];
}

function resolveCapability(capabilities: VariantCapability[], variant: VariantCode): VariantCapability {
  return (
    capabilities.find((item) => item.variant === variant) ??
    FALLBACK_CAPABILITIES.find((item) => item.variant === variant) ??
    FALLBACK_CAPABILITIES[0]
  );
}

function resolveVoices(voices: TtsVoice[], variant: VariantCode): TtsVoice[] {
  const variantVoices = voices.filter((item) => item.variant === variant);
  if (variantVoices.length > 0) {
    return variantVoices;
  }
  return FALLBACK_VOICES.filter((item) => item.variant === variant);
}

function resolveDefaultVoice(voices: TtsVoice[], variant: VariantCode): TtsVoice | null {
  const variantVoices = resolveVoices(voices, variant);
  return variantVoices.find((item) => item.is_default) ?? variantVoices[0] ?? null;
}

function isVariantAudioReady(health: HealthResponse | null, capability: VariantCapability): boolean {
  if (capability.capability !== "audio") {
    return false;
  }
  if (health === null) {
    return true;
  }
  return health.stub_mode || Boolean(health.tts_variants_ready[capability.variant]);
}

function describeAudioMode(health: HealthResponse | null, capability: VariantCapability): string {
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

function getLatestStage(job: JobRecord | null): PipelineStage | null {
  if (!job?.result?.stages.length) {
    return null;
  }
  return job.result.stages[job.result.stages.length - 1] ?? null;
}

function countCompletedStages(job: JobRecord | null): number {
  return job?.result?.stages.filter((stage) => stage.status === "completed").length ?? 0;
}

function listMissingRuntimeComponents(health: HealthResponse | null): string[] {
  if (health === null) {
    return [];
  }
  return Object.entries(health.runtime_components)
    .filter(([, isReady]) => !isReady)
    .map(([name]) => name)
    .sort();
}

export default function App() {
  const { isRecording, audioBlob, error: micError, startRecording, stopRecording, reset } = useLiveMic();
  const [variant, setVariant] = useState<VariantCode>("sme");
  const [capabilities, setCapabilities] = useState<VariantCapability[]>(FALLBACK_CAPABILITIES);
  const [voices, setVoices] = useState<TtsVoice[]>(FALLBACK_VOICES);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [voice, setVoice] = useState<string>(resolveDefaultVoice(FALLBACK_VOICES, "sme")?.voice ?? "");
  const [job, setJob] = useState<JobRecord | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [audioPreview, setAudioPreview] = useState<string | null>(null);
  const [isRefreshingRuntime, setIsRefreshingRuntime] = useState(false);

  useEffect(() => {
    const variantVoices = resolveVoices(voices, variant);
    if (variantVoices.length === 0) {
      if (voice !== "") {
        setVoice("");
      }
      return;
    }

    if (variantVoices.some((item) => item.voice === voice)) {
      return;
    }

    setVoice(resolveDefaultVoice(voices, variant)?.voice ?? "");
  }, [variant, voice, voices]);

  useEffect(() => {
    if (!audioBlob) {
      setAudioPreview(null);
      return;
    }

    const objectUrl = URL.createObjectURL(audioBlob);
    setAudioPreview(objectUrl);
    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [audioBlob]);

  async function loadRuntimeData() {
    setIsRefreshingRuntime(true);
    try {
      const [capabilityResult, healthResult, voiceResult] = await Promise.allSettled([
        fetchCapabilities(),
        fetchHealth(),
        fetchVoices(),
      ]);

      if (capabilityResult.status === "fulfilled") {
        setCapabilities(capabilityResult.value);
        setCapabilityError(null);
      } else {
        setCapabilityError(
          capabilityResult.reason instanceof Error
            ? capabilityResult.reason.message
            : "Kunne ikke hente capability-matrise"
        );
      }

      if (healthResult.status === "fulfilled") {
        setHealth(healthResult.value);
        setHealthError(null);
      } else {
        setHealthError(
          healthResult.reason instanceof Error ? healthResult.reason.message : "Kunne ikke hente runtime-status"
        );
      }

      if (voiceResult.status === "fulfilled") {
        setVoices(voiceResult.value);
        setVoiceError(null);
      } else {
        setVoiceError(voiceResult.reason instanceof Error ? voiceResult.reason.message : "Kunne ikke hente stemmer");
      }
    } catch {
      setCapabilityError("Kunne ikke hente capability-matrise");
      setHealthError("Kunne ikke hente runtime-status");
      setVoiceError("Kunne ikke hente stemmer");
    } finally {
      setIsRefreshingRuntime(false);
    }
  }

  useEffect(() => {
    let isMounted = true;
    void loadRuntimeData().catch(() => {
      if (!isMounted) {
        return;
      }
    });
    return () => {
      isMounted = false;
    };
  }, []);

  const selectedCapability = resolveCapability(capabilities, variant);
  const selectedVoices = resolveVoices(voices, variant);
  const selectedVoice = selectedVoices.find((item) => item.voice === voice) ?? resolveDefaultVoice(voices, variant);
  const shouldRequestAudio = isVariantAudioReady(health, selectedCapability);
  const resultCapability = job ? resolveCapability(capabilities, job.request.target_variant) : selectedCapability;
  const currentStage = getLatestStage(job);
  const completedStages = countCompletedStages(job);
  const totalStages = job?.result?.stages.length ?? 0;
  const preferredRuntime = health?.runtime_profiles[0] ?? null;
  const missingRuntimeComponents = listMissingRuntimeComponents(health);

  function resetAll() {
    reset();
    setSourceText("");
    setApiError(null);
    setJob(null);
  }

  async function submit() {
    if (!audioBlob && !sourceText.trim()) {
      setApiError("Ta opp lyd eller skriv inn norsk tekst for a starte pipeline.");
      return;
    }

    setApiError(null);
    setIsSubmitting(true);
    setJob(null);

    const formData = new FormData();
    formData.set("target_variant", variant);
    if (voice) {
      formData.set("target_voice", voice);
    }
    formData.set("include_phonemes", "true");
    formData.set("include_audio", shouldRequestAudio ? "true" : "false");
    if (sourceText.trim()) {
      formData.set("source_text", sourceText.trim());
    }
    if (audioBlob) {
      formData.set("audio", audioBlob, "mic-input.webm");
    }

    try {
      const createResponse = await fetch(`${API_BASE}/pipeline`, {
        method: "POST",
        body: formData,
      });
      if (!createResponse.ok) {
        const payload = (await createResponse.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(payload?.detail ?? "Kunne ikke starte pipeline-jobben");
      }
      const queuedJob = (await createResponse.json()) as JobRecord;
      setJob(queuedJob);
      const resolvedJob = await pollJob(queuedJob.id, setJob);
      setJob(resolvedJob);
    } catch (caughtError) {
      setApiError(caughtError instanceof Error ? caughtError.message : "Ukjent API-feil");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Hvordan sier jeg pa samisk</p>
        <h1>Norsk tale inn. Samisk tekst og uttale ut.</h1>
        <p className="lead">
          Enkel lokal testflate for norsk tale til samisk tekst, fonemer og audio via FastAPI-jobber pa din egen maskin.
        </p>
      </section>

      <section className="panel runtime-panel">
        <div className="section-head">
          <div>
            <h2>Runtime-status</h2>
            <p className="hint">Frontend leser health-endepunktet for a vise hva som faktisk kan testes akkurat na.</p>
          </div>
          <div className="runtime-actions">
            <button type="button" className="secondary" onClick={() => void loadRuntimeData()} disabled={isRefreshingRuntime}>
              {isRefreshingRuntime ? "Oppdaterer..." : "Oppdater status"}
            </button>
            {health ? (
              <p className={`job-status job-status--${health.stub_mode ? "queued" : "completed"}`}>
                {health.stub_mode ? "Stub-modus" : health.tts_runtime === "divvun-api" ? "Divvun API" : "Lokal runtime"}
              </p>
            ) : null}
          </div>
        </div>

        <div className="runtime-grid">
          <article>
            <h3>API og inferens</h3>
            <p>{health ? `${health.name} / ${health.environment}` : "Venter pa API-status..."}</p>
            <p className="hint">
              {health
                ? `Provider-runtime: ${health.provider_runtime}. Inference-avhengigheter ${health.inference_dependencies_ready ? "er" : "er ikke"} klare. Lokal inferens ${health.inference_runtime_ready ? "kan" : "kan ikke"} kjores direkte na.`
                : "Kunne ikke lese inferensstatus ennå."}
            </p>
            {missingRuntimeComponents.length > 0 ? (
              <p className="hint">Mangler akkurat na: {missingRuntimeComponents.join(", ")}</p>
            ) : null}
            {health?.runtime_issues.length ? <p className="hint">Kjente avvik: {health.runtime_issues.join(" | ")}</p> : null}
          </article>
          <article>
            <h3>Valgt variant</h3>
            <p>{selectedCapability.label}</p>
            <p className="hint">{describeAudioMode(health, selectedCapability)}</p>
            {selectedVoice ? <p className="hint">Stemme: {selectedVoice.label}</p> : null}
          </article>
          <article>
            <h3>Foretrukket runtime-profil</h3>
            <p>{preferredRuntime ? preferredRuntime.key : "Ikke oppgitt av API-et"}</p>
            <p className="hint">
              {preferredRuntime
                ? `${preferredRuntime.architecture} / ${preferredRuntime.accelerator} / ${preferredRuntime.container_runtime}`
                : "Health-endepunktet svarte ikke med runtime-profiler."}
            </p>
          </article>
          <article>
            <h3>Modeller</h3>
            <p>{health?.configured_models.asr ?? "ASR-modell ukjent"}</p>
            <p className="hint">{health?.configured_models.translation ?? "Oversettingsmodell ukjent"}</p>
            {health?.model_cache_state.nb_whisper ? <p className="hint">nb-whisper: {health.model_cache_state.nb_whisper.summary}</p> : null}
            {health?.model_cache_state.tahetorn ? <p className="hint">Tahetorn: {health.model_cache_state.tahetorn.summary}</p> : null}
          </article>
          <article>
            <h3>Stier</h3>
            <p>{health?.resolved_paths.model_dir ?? "Modelldir ukjent"}</p>
            <p className="hint">{health?.resolved_paths.artifacts_dir ?? "Artefaktdir ukjent"}</p>
            <p className="hint">
              {health
                ? `Lokal modellcache ${health.local_model_cache_present ? "finnes" : "finnes ikke"} pa denne stien.`
                : "Ingen runtime-status for lokale stier ennå."}
            </p>
          </article>
        </div>

        {healthError ? <p className="error">{healthError}</p> : null}
      </section>

      <section className="panel controls">
        <label>
          Samisk variant
          <select value={variant} onChange={(event) => setVariant(event.target.value as VariantCode)}>
            <option value="sme">Nordsamisk</option>
            <option value="smj">Lulesamisk</option>
            <option value="sma">Sorsamisk</option>
          </select>
        </label>

        <label>
          Stemme
          <select value={voice} onChange={(event) => setVoice(event.target.value)} disabled={selectedVoices.length === 0}>
            {selectedVoices.length === 0 ? <option value="">Ingen stemmer tilgjengelig</option> : null}
            {selectedVoices.map((item) => (
              <option key={item.voice} value={item.voice}>
                {item.label} ({item.gender})
              </option>
            ))}
          </select>
        </label>

        <div className="capability-card">
          <p className="capability-title">Valgt lop: {selectedCapability.capability}</p>
          <p>{selectedCapability.notes}</p>
          <p className="hint">{describeAudioMode(health, selectedCapability)}</p>
          {selectedVoice ? <p className="hint">Valgt stemme er {selectedVoice.label}.</p> : null}
          {!shouldRequestAudio ? <p className="hint">Denne jobben sendes uten include_audio for a unnga et garantert audio-avvik.</p> : null}
          {capabilityError ? <p className="hint">{capabilityError} Bruker innebygd fallback i mellomtiden.</p> : null}
          {voiceError ? <p className="hint">{voiceError} Bruker innebygd fallback i mellomtiden.</p> : null}
        </div>

        <label>
          Norsk tekst som alternativ til lyd
          <textarea
            rows={5}
            placeholder="Skriv norsk tekst her hvis du vil teste pipeline uten mikrofon"
            value={sourceText}
            onChange={(event) => setSourceText(event.target.value)}
          />
        </label>

        <div className="button-row">
          <button type="button" onClick={isRecording ? stopRecording : startRecording}>
            {isRecording ? "Stopp opptak" : "Start mikrofon"}
          </button>
          <button type="button" className="secondary" onClick={resetAll} disabled={isSubmitting || (!audioBlob && !sourceText && !job)}>
            Nullstill
          </button>
          <button type="button" className="primary" onClick={submit} disabled={isSubmitting}>
            {isSubmitting ? "Kjorer pipeline..." : "Send til API"}
          </button>
        </div>

        {micError ? <p className="error">{micError}</p> : null}
        {apiError ? <p className="error">{apiError}</p> : null}

        {job ? (
          <div className="job-meta">
            <p className={`job-status job-status--${job.status}`}>Jobbstatus: {job.status}</p>
            <p className="hint">Jobb-ID: {job.id}</p>
            {job.request.target_voice ? <p className="hint">Stemme: {job.request.target_voice}</p> : null}
            {currentStage ? <p className="hint">Siste stadium: {currentStage.name} - {currentStage.summary}</p> : null}
          </div>
        ) : null}

        {audioPreview ? (
          <div className="preview">
            <p>Siste opptak</p>
            <audio controls src={audioPreview} />
          </div>
        ) : null}
      </section>

      <section className="panel results">
        <div className="section-head">
          <div>
            <h2>Pipeline-resultat</h2>
            {job ? <p className="hint">Ferdige steg: {completedStages} av {totalStages || 4}</p> : null}
          </div>
          {job ? <p className={`job-status job-status--${job.status}`}>{job.status}</p> : null}
        </div>
        {!job ? <p>Ingen jobb kjort ennå.</p> : null}
        {job?.status === "running" && !job.result ? <p className="hint">Jobben er startet og venter pa forste statusoppdatering fra pipeline-tjenesten.</p> : null}
        {job?.error ? <p className="error">{job.error}</p> : null}
        {job?.result ? (
          <>
            <div className="result-grid">
              <article>
                <h3>Transkripsjon</h3>
                <p>{job.result.transcript_text ?? "-"}</p>
              </article>
              <article>
                <h3>Oversetting</h3>
                <p>{job.result.translated_text ?? "-"}</p>
              </article>
              <article>
                <h3>Uttale / fonemer</h3>
                <p>{job.result.phoneme_text ?? "-"}</p>
              </article>
              <article>
                <h3>Audio</h3>
                {job.result.audio_url ? <audio controls src={resolveArtifactUrl(job.result.audio_url)} /> : null}
                <p>{job.result.audio_summary ?? (job.result.audio_url ? job.result.audio_url : "Ingen audio-status rapportert")}</p>
                {job.request.target_voice ? <p className="hint">Audio ble bedt om med stemme {job.request.target_voice}.</p> : null}
              </article>
            </div>
            <div>
              <h3>Stadier</h3>
              <ul className="stage-list">
                {job.result.stages.map((stage) => (
                  <li key={stage.name} className={`stage stage--${stage.status}`}>
                    <strong>{stage.name}</strong>
                    <span>{stage.status}</span>
                    <p>{stage.summary}</p>
                  </li>
                ))}
              </ul>
            </div>
          </>
        ) : null}
      </section>
    </main>
  );
}
