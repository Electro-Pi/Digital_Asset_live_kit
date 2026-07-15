import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  BarVisualizer,
  VoiceAssistantControlBar,
  useVoiceAssistant,
  useConnectionState,
  useLocalParticipant,
  useTranscriptions,
} from "@livekit/components-react";
import { ConnectionState } from "livekit-client";

const MICROPHONE_OPTIONS = {
  autoGainControl: false,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  voiceIsolation: true,
};

export default function App() {
  const [conn, setConn] = useState(null); // { token, serverUrl, room }
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState(null);

  const startCall = useCallback(async () => {
    setError(null);
    setConnecting(true);
    try {
      const res = await fetch("/api/token");
      if (!res.ok) throw new Error(`token request failed (${res.status})`);
      const data = await res.json();
      if (!data.token || !data.serverUrl) throw new Error("invalid token response");
      setConn(data);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setConnecting(false);
    }
  }, []);

  const endCall = useCallback(() => setConn(null), []);

  return (
    <div className="page">
      <header className="hero">
        <h1>Digital Asset</h1>
        <p className="subtitle">Arabic Voice Agent — live demo</p>
      </header>

      {!conn ? (
        <div className="card landing">
          <p className="lede">
            Talk to the agent naturally in Arabic.
          </p>
          <button className="primary" onClick={startCall} disabled={connecting}>
            {connecting ? "Connecting…" : "Start talking"}
          </button>
          {error && <p className="error">⚠ {error}</p>}
          <p className="hint">
            Best in Chrome. Use headphones to improve echo cancellation.
          </p>
        </div>
      ) : (
        <LiveKitRoom
          className="card room"
          token={conn.token}
          serverUrl={conn.serverUrl}
          connect={true}
          audio={MICROPHONE_OPTIONS}
          video={false}
          options={{ audioCaptureDefaults: MICROPHONE_OPTIONS }}
          onDisconnected={endCall}
          onError={(e) => setError(e.message)}
        >
          <VoiceSession onEnd={endCall} />
          <RoomAudioRenderer />
        </LiveKitRoom>
      )}

      <footer className="foot">room: {conn?.room ?? "—"}</footer>
    </div>
  );
}

const STATE_LABELS = {
  connecting: "Connecting…",
  initializing: "Waking the agent…",
  listening: "Listening — go ahead",
  thinking: "Thinking…",
  speaking: "Speaking",
};

function VoiceSession({ onEnd }) {
  const voiceAssistant = useVoiceAssistant();
  const { state, audioTrack } = voiceAssistant;
  const roomState = useConnectionState();

  const label = useMemo(() => {
    if (roomState !== ConnectionState.Connected) return "Connecting…";
    if (!state) return "Waiting for agent to join…";
    return STATE_LABELS[state] ?? state;
  }, [roomState, state]);

  return (
    <div className="session">
      <div className={`status status-${state ?? "connecting"}`}>{label}</div>

      <div className="viz">
        <BarVisualizer
          state={state}
          trackRef={audioTrack}
          barCount={7}
          options={{ minHeight: 8 }}
        />
      </div>

      <TranscriptPanel voiceAssistant={voiceAssistant} />

      <div className="controls">
        <VoiceAssistantControlBar controls={{ leave: false }} />
        <button className="danger" onClick={onEnd}>
          End call
        </button>
      </div>
    </div>
  );
}

const FINAL_ATTR = "lk.transcription_final";
const SEGMENT_ATTR = "lk.segment_id";

function TranscriptPanel({ voiceAssistant }) {
  const allTranscriptions = useTranscriptions();
  const { localParticipant } = useLocalParticipant();
  const listRef = useRef(null);

  const agentIdentities = useMemo(
    () =>
      new Set(
        [
          voiceAssistant.agent?.identity,
          voiceAssistant.audioTrack?.participant?.identity,
        ].filter(Boolean)
      ),
    [voiceAssistant.agent?.identity, voiceAssistant.audioTrack?.participant?.identity]
  );

  const transcriptItems = useMemo(() => {
    const entries = new Map();
    let hasLlmTextStream = false;

    allTranscriptions.forEach((stream) => {
      const text = stream.text?.trim();
      if (!text) return;

      const identity = stream.participantInfo.identity;
      const role =
        identity === localParticipant.identity
          ? "stt"
          : agentIdentities.has(identity)
          ? "llm"
          : "other";
      if (role === "llm") hasLlmTextStream = true;
      const segmentId = stream.streamInfo.attributes?.[SEGMENT_ATTR];
      const finalAttr = stream.streamInfo.attributes?.[FINAL_ATTR];

      entries.set(segmentId ?? stream.streamInfo.id, {
        id: segmentId ?? stream.streamInfo.id,
        role,
        text,
        final: finalAttr === "true" || finalAttr === true,
        timestamp: stream.streamInfo.timestamp ?? Date.now(),
      });
    });

    if (!hasLlmTextStream) {
      voiceAssistant.agentTranscriptions.forEach((segment) => {
        const text = segment.text?.trim();
        if (!text) return;
        const id = `agent-track-${segment.id}`;
        if (entries.has(segment.id) || entries.has(id)) return;

        entries.set(id, {
          id,
          role: "llm",
          text,
          final: segment.final,
          timestamp: segment.firstReceivedTime ?? segment.receivedAt ?? Date.now(),
        });
      });
    }

    return Array.from(entries.values()).sort(
      (a, b) => a.timestamp - b.timestamp
    );
  }, [
    allTranscriptions,
    agentIdentities,
    localParticipant.identity,
    voiceAssistant.agentTranscriptions,
  ]);

  useEffect(() => {
    listRef.current?.scrollTo({
      top: listRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [transcriptItems]);

  return (
    <section className="transcripts" aria-label="Live transcripts">
      <div className="transcripts-head">
        <h2>Live transcript</h2>
        <span className="live-dot">Live</span>
      </div>

      <div className="transcript-list" ref={listRef}>
        {transcriptItems.length === 0 ? (
          <p className="transcript-empty">Waiting for speech…</p>
        ) : (
          transcriptItems.map((item) => (
            <article
              className={`transcript-line transcript-${item.role}`}
              key={item.id}
            >
              <div className="transcript-meta">
                <span>{item.role === "stt" ? "STT" : "LLM"}</span>
                {!item.final && <span className="interim">typing</span>}
              </div>
              <p dir="auto">{item.text}</p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
