import { useEffect, useRef, useState } from "react";

export function useLiveMic() {
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      mediaRecorderRef.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  async function startRecording() {
    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        setAudioBlob(new Blob(chunksRef.current, { type: "audio/webm" }));
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Kunne ikke starte mikrofonen");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current?.stream.getTracks().forEach((track) => track.stop());
    mediaRecorderRef.current = null;
    setIsRecording(false);
  }

  function reset() {
    setAudioBlob(null);
    setError(null);
    chunksRef.current = [];
  }

  return {
    isRecording,
    audioBlob,
    error,
    startRecording,
    stopRecording,
    reset,
  };
}
