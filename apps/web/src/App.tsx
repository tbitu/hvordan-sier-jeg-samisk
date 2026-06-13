import { useEffect, useRef, useState } from "react";
import { useLiveMic } from "./features/live-mic/useLiveMic";
import type { VariantCode, VariantCapability, TtsVoice, HealthResponse, JobRecord } from "./types";
import {
  FALLBACK_CAPABILITIES,
  FALLBACK_VOICES,
  resolveCapability,
  resolveVoices,
  resolveDefaultVoice,
  isVariantAudioReady,
  fetchCapabilities,
  fetchHealth,
  fetchVoices,
  pollJob,
} from "./utils";
import { HealthStatus } from "./components/HealthStatus";
import { Controls } from "./components/Controls";
import { ResultsPanel } from "./components/ResultsPanel";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

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

  // AbortController for runtime data fetches - prevents stale data overwrites
  const runtimeAbortRef = useRef<AbortController | null>(null);
  // AbortController for active job polling - allows cancellation on new submit/unmount
  const pollAbortRef = useRef<AbortController | null>(null);

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
    // Cancel any in-flight runtime fetch
    runtimeAbortRef.current?.abort();
    const controller = new AbortController();
    runtimeAbortRef.current = controller;

    setIsRefreshingRuntime(true);
    try {
      const [capabilityResult, healthResult, voiceResult] = await Promise.allSettled([
        fetchCapabilities(controller.signal),
        fetchHealth(controller.signal),
        fetchVoices(controller.signal),
      ]);

      if (controller.signal.aborted) return;

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
    } finally {
      setIsRefreshingRuntime(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void loadRuntimeData().catch(() => {
      // Silently ignore - errors or abortions are handled inside loadRuntimeData
    });
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedCapability = resolveCapability(capabilities, variant);
  const selectedVoices = resolveVoices(voices, variant);
  const selectedVoice = selectedVoices.find((item) => item.voice === voice) ?? resolveDefaultVoice(voices, variant);
  const shouldRequestAudio = isVariantAudioReady(health, selectedCapability);

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

      // Cancel any in-flight poll before starting a new one
      pollAbortRef.current?.abort();
      const pollController = new AbortController();
      pollAbortRef.current = pollController;
      const resolvedJob = await pollJob(queuedJob.id, setJob, pollController.signal);
      setJob(resolvedJob);
    } catch (caughtError) {
      if (caughtError instanceof Error && caughtError.message === "Avbrutt") {
        // Poll was cancelled by a new submit or unmount - silently ignore
        return;
      }
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

      <HealthStatus
        health={health}
        isRefreshingRuntime={isRefreshingRuntime}
        loadRuntimeData={loadRuntimeData}
        selectedCapability={selectedCapability}
        selectedVoice={selectedVoice}
        healthError={healthError}
      />

      <Controls
        variant={variant}
        setVariant={setVariant}
        voice={voice}
        setVoice={setVoice}
        sourceText={sourceText}
        setSourceText={setSourceText}
        selectedCapability={selectedCapability}
        selectedVoices={selectedVoices}
        selectedVoice={selectedVoice}
        shouldRequestAudio={shouldRequestAudio}
        health={health}
        capabilities={capabilities}
        capabilityError={capabilityError}
        voiceError={voiceError}
        isRecording={isRecording}
        startRecording={startRecording}
        stopRecording={stopRecording}
        resetAll={resetAll}
        submit={submit}
        isSubmitting={isSubmitting}
        micError={micError}
        apiError={apiError}
        audioPreview={audioPreview}
        audioBlob={audioBlob}
        job={job}
      />

      <ResultsPanel job={job} />
    </main>
  );
}
