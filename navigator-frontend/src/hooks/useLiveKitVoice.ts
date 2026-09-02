import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ConnectionState,
  Room,
  RoomEvent,
  Track,
  type Participant,
  type RemoteTrack,
} from 'livekit-client';
import type { Language } from '../data/mapData';
import { publishNavigationRouteNarration } from '../lib/livekitNavigation';
import type { NavigationRouteNarration } from '../lib/navigation';

const AGENT_STATE_ATTRIBUTE = 'lk.agent.state';
const TRANSCRIPTION_TOPIC = 'lk.transcription';
const TRANSCRIBED_TRACK_ATTRIBUTE = 'lk.transcribed_track_id';
const NAVIGATION_ROUTE_TOPIC = 'navigator.route';

export type VoiceStatus =
  | 'idle'
  | 'connecting'
  | 'initializing'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'error';

export interface NavigationRouteAction {
  version: 1;
  type: 'set_navigation_route';
  startId: string;
  destinationId: string;
  language?: Language;
}

export interface VoiceTranscript {
  speaker: 'user' | 'agent';
  text: string;
}

interface ConnectionDetails {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
}

interface VoiceSessionContext {
  language: Language;
  startId?: string;
  destinationId?: string;
  voice?: 'female' | 'male';
}

interface UseLiveKitVoiceOptions {
  onRouteAction: (action: NavigationRouteAction) => void;
  onTranscript?: (transcript: VoiceTranscript) => void;
}

function isConnectionDetails(value: unknown): value is ConnectionDetails {
  if (!value || typeof value !== 'object') return false;
  const details = value as Partial<ConnectionDetails>;
  return Boolean(
    details.serverUrl
      && details.roomName
      && details.participantName
      && details.participantToken,
  );
}

function isNavigationRouteAction(value: unknown): value is NavigationRouteAction {
  if (!value || typeof value !== 'object') return false;
  const action = value as Partial<NavigationRouteAction>;
  return action.version === 1
    && action.type === 'set_navigation_route'
    && typeof action.startId === 'string'
    && typeof action.destinationId === 'string';
}

function isAgent(participant: Participant) {
  return participant.isAgent || Boolean(participant.attributes[AGENT_STATE_ATTRIBUTE]);
}

function statusFromParticipant(participant: Participant): VoiceStatus {
  const state = participant.attributes[AGENT_STATE_ATTRIBUTE];
  if (state === 'listening' || state === 'thinking' || state === 'speaking') return state;
  if (state === 'initializing' || state === 'idle') return 'initializing';
  return 'initializing';
}

export function useLiveKitVoice({ onRouteAction, onTranscript }: UseLiveKitVoiceOptions) {
  const roomRef = useRef<Room | null>(null);
  const fetchAbortRef = useRef<AbortController | null>(null);
  const disconnectingRef = useRef(false);
  const callbacksRef = useRef({ onRouteAction, onTranscript });
  const audioElementsRef = useRef(new Set<HTMLMediaElement>());
  const [status, setStatus] = useState<VoiceStatus>('idle');
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState('');
  const [lastTranscript, setLastTranscript] = useState<VoiceTranscript | null>(null);

  useEffect(() => {
    callbacksRef.current = { onRouteAction, onTranscript };
  }, [onRouteAction, onTranscript]);

  const removeAudioElements = useCallback(() => {
    audioElementsRef.current.forEach((element) => {
      element.pause();
      element.remove();
    });
    audioElementsRef.current.clear();
  }, []);

  const disconnect = useCallback(async () => {
    disconnectingRef.current = true;
    fetchAbortRef.current?.abort();
    fetchAbortRef.current = null;
    const room = roomRef.current;
    roomRef.current = null;
    if (room) await room.disconnect();
    removeAudioElements();
    setIsConnected(false);
    setStatus('idle');
    setError('');
    disconnectingRef.current = false;
  }, [removeAudioElements]);

  useEffect(() => () => {
    disconnectingRef.current = true;
    fetchAbortRef.current?.abort();
    const room = roomRef.current;
    roomRef.current = null;
    void room?.disconnect();
    removeAudioElements();
  }, [removeAudioElements]);

  const connect = useCallback(async (context: VoiceSessionContext) => {
    if (roomRef.current || fetchAbortRef.current) return;

    setStatus('connecting');
    setError('');
    setLastTranscript(null);
    disconnectingRef.current = false;
    const abortController = new AbortController();
    fetchAbortRef.current = abortController;

    try {
      const response = await fetch('/api/connection-details', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store',
        signal: abortController.signal,
        body: JSON.stringify({
          language: context.language,
          voice: context.voice ?? 'female',
          startId: context.startId || undefined,
          destinationId: context.destinationId || undefined,
        }),
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok || !isConnectionDetails(payload)) {
        const message = payload && typeof payload === 'object' && 'message' in payload
          ? String(payload.message)
          : `Could not start the live voice session (${response.status}).`;
        throw new Error(message);
      }
      if (abortController.signal.aborted) return;

      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
        disconnectOnPageLeave: true,
      });
      roomRef.current = room;

      room.registerTextStreamHandler(TRANSCRIPTION_TOPIC, async (reader, participantInfo) => {
        const text = (await reader.readAll()).trim();
        if (!text || roomRef.current !== room) return;
        const transcribedTrackId = reader.info.attributes?.[TRANSCRIBED_TRACK_ATTRIBUTE];
        const localMicrophoneId = room.localParticipant
          .getTrackPublication(Track.Source.Microphone)?.trackSid;
        const transcript: VoiceTranscript = {
          speaker: participantInfo.identity === room.localParticipant.identity
            || Boolean(transcribedTrackId && transcribedTrackId === localMicrophoneId)
            ? 'user'
            : 'agent',
          text,
        };
        setLastTranscript(transcript);
        callbacksRef.current.onTranscript?.(transcript);
      });

      const attachAudio = (track: RemoteTrack, participant: Participant) => {
        if (track.kind !== Track.Kind.Audio || !isAgent(participant)) return;
        const element = track.attach();
        element.autoplay = true;
        element.className = 'livekit-agent-audio';
        element.setAttribute('aria-hidden', 'true');
        document.body.appendChild(element);
        audioElementsRef.current.add(element);
        void room.startAudio().catch(() => undefined);
      };

      room
        .on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
          attachAudio(track, participant);
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          track.detach().forEach((element) => {
            audioElementsRef.current.delete(element);
            element.remove();
          });
        })
        .on(RoomEvent.ParticipantConnected, (participant) => {
          if (isAgent(participant)) setStatus(statusFromParticipant(participant));
        })
        .on(RoomEvent.ParticipantAttributesChanged, (_attributes, participant) => {
          if (isAgent(participant)) setStatus(statusFromParticipant(participant));
        })
        .on(RoomEvent.ParticipantDisconnected, (participant) => {
          if (isAgent(participant) && !disconnectingRef.current) {
            setStatus('error');
            setError('The Coda guide left the voice session. Tap the microphone to reconnect.');
          }
        })
        .on(RoomEvent.Reconnecting, () => setStatus('connecting'))
        .on(RoomEvent.Reconnected, () => {
          let synchronizedStatus: VoiceStatus = 'initializing';
          room.remoteParticipants.forEach((participant) => {
            if (isAgent(participant)) synchronizedStatus = statusFromParticipant(participant);
          });
          setStatus(synchronizedStatus);
        })
        .on(RoomEvent.MediaDevicesError, (mediaError) => {
          setStatus('error');
          setError(mediaError.message || 'Microphone access failed.');
        })
        .on(RoomEvent.DataReceived, (data, participant, _kind, topic) => {
          if (topic !== NAVIGATION_ROUTE_TOPIC || !participant || !isAgent(participant)) return;
          try {
            const action: unknown = JSON.parse(new TextDecoder().decode(data));
            if (isNavigationRouteAction(action)) callbacksRef.current.onRouteAction(action);
          } catch {
            // Ignore unrelated or malformed room data. Only validated route actions
            // are allowed to influence the deterministic frontend route graph.
          }
        })
        .on(RoomEvent.Disconnected, () => {
          roomRef.current = null;
          removeAudioElements();
          setIsConnected(false);
          if (disconnectingRef.current) {
            setStatus('idle');
          } else {
            setStatus('error');
            setError('The live voice session ended. Tap the microphone to reconnect.');
          }
        });

      await room.connect(payload.serverUrl, payload.participantToken);
      if (abortController.signal.aborted || roomRef.current !== room) {
        await room.disconnect();
        return;
      }
      setIsConnected(true);
      // Participant/attribute events can mark Coda as listening while
      // room.connect() is still resolving. Preserve that newer state instead
      // of racing it back to "initializing", which would prevent the frontend
      // from publishing its one-shot route narration packet.
      setStatus((currentStatus) => (
        currentStatus === 'connecting' ? 'initializing' : currentStatus
      ));

      await room.startAudio().catch(() => undefined);
      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });

      room.remoteParticipants.forEach((participant) => {
        if (isAgent(participant)) setStatus(statusFromParticipant(participant));
      });
    } catch (connectionError) {
      if (connectionError instanceof DOMException && connectionError.name === 'AbortError') return;
      const message = connectionError instanceof Error
        ? connectionError.message
        : 'Could not connect to the live Coda guide.';
      const room = roomRef.current;
      roomRef.current = null;
      if (room) await room.disconnect();
      removeAudioElements();
      setIsConnected(false);
      setStatus('error');
      setError(message);
      throw connectionError;
    } finally {
      if (fetchAbortRef.current === abortController) fetchAbortRef.current = null;
    }
  }, [removeAudioElements]);

  const publishRouteNarration = useCallback(async (narration: NavigationRouteNarration) => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) return false;
    await publishNavigationRouteNarration(
      (payload, options) => room.localParticipant.publishData(Uint8Array.from(payload), options),
      narration,
    );
    return true;
  }, []);

  const updateContext = useCallback(async (context: VoiceSessionContext) => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) return;
    await room.localParticipant.setAttributes({
      'navigator.language': context.language,
      'navigator.start_id': context.startId ?? '',
      'navigator.destination_id': context.destinationId ?? '',
    });
  }, []);

  return {
    status,
    isConnected,
    isActive: isConnected || status === 'connecting' || status === 'initializing',
    error,
    lastTranscript,
    connect,
    disconnect,
    publishRouteNarration,
    updateContext,
  };
}
