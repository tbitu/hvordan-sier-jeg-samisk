import type { JobRecord, PipelineStage } from "../types";
import { resolveArtifactUrl, getLatestStage, countCompletedStages } from "../utils";

type ResultsPanelProps = {
  job: JobRecord | null;
};

export function ResultsPanel({ job }: ResultsPanelProps) {
  const currentStage = getLatestStage(job);
  const completedStages = countCompletedStages(job);
  const totalStages = job?.result?.stages.length ?? 0;

  return (
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
              {job.result.stages.map((stage: PipelineStage) => (
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
  );
}
