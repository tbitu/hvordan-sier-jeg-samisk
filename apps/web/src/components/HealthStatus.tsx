import type { HealthResponse, VariantCapability, TtsVoice } from "../types";
import { describeAudioMode, listMissingRuntimeComponents } from "../utils";

type HealthStatusProps = {
  health: HealthResponse | null;
  isRefreshingRuntime: boolean;
  loadRuntimeData: () => Promise<void>;
  selectedCapability: VariantCapability;
  selectedVoice: TtsVoice | null;
  healthError: string | null;
};

export function HealthStatus({
  health,
  isRefreshingRuntime,
  loadRuntimeData,
  selectedCapability,
  selectedVoice,
  healthError,
}: HealthStatusProps) {
  const preferredRuntime = health?.runtime_profiles?.[0] ?? null;
  const missingRuntimeComponents = listMissingRuntimeComponents(health);

  return (
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
          {health?.runtime_issues?.length ? <p className="hint">Kjente avvik: {health.runtime_issues.join(" | ")}</p> : null}
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
  );
}
