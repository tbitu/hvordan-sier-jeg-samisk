import type { VariantCode, VariantCapability, TtsVoice, HealthResponse } from "../types";
import { describeAudioMode, getLatestStage } from "../utils";

type ControlsProps = {
  variant: VariantCode;
  setVariant: (variant: VariantCode) => void;
  voice: string;
  setVoice: (voice: string) => void;
  sourceText: string;
  setSourceText: (text: string) => void;
  selectedCapability: VariantCapability;
  selectedVoices: TtsVoice[];
  selectedVoice: TtsVoice | null;
  shouldRequestAudio: boolean;
  health: HealthResponse | null;
  capabilities: VariantCapability[];
  capabilityError: string | null;
  voiceError: string | null;
  isRecording: boolean;
  startRecording: () => void;
  stopRecording: () => void;
  resetAll: () => void;
  submit: () => void;
  isSubmitting: boolean;
  micError: string | null;
  apiError: string | null;
  audioPreview: string | null;
  audioBlob: Blob | null;
  job: any;
};

export function Controls({
  variant,
  setVariant,
  voice,
  setVoice,
  sourceText,
  setSourceText,
  selectedCapability,
  selectedVoices,
  selectedVoice,
  shouldRequestAudio,
  health,
  capabilityError,
  voiceError,
  isRecording,
  startRecording,
  stopRecording,
  resetAll,
  submit,
  isSubmitting,
  micError,
  apiError,
  audioPreview,
  audioBlob,
  job,
}: ControlsProps) {
  return (
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
          {(() => { const stage = getLatestStage(job); return stage ? <p className="hint">Siste stadium: {stage.name} - {stage.summary}</p> : null; })()}
        </div>
      ) : null}

      {audioPreview ? (
        <div className="preview">
          <p>Siste opptak</p>
          <audio controls src={audioPreview} />
        </div>
      ) : null}
    </section>
  );
}
