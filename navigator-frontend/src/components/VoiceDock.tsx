import { Mic, Square, Volume2 } from 'lucide-react';
import type { Language } from '../data/mapData';
import type { VoiceStatus, VoiceTranscript } from '../hooks/useLiveKitVoice';

interface VoiceDockProps {
  language: Language;
  status: VoiceStatus;
  active: boolean;
  message: string;
  transcript: VoiceTranscript | null;
  error: string;
  onVoiceAction: () => void;
}

export function VoiceDock({
  language,
  status,
  active,
  message,
  transcript,
  error,
  onVoiceAction,
}: VoiceDockProps) {
  const stateLabel =
    status === 'connecting'
      ? language === 'ar' ? 'جاري الاتصال بلايف كِت' : 'Connecting through LiveKit'
      : status === 'initializing'
        ? language === 'ar' ? 'كودا تجهّز المحادثة' : 'Coda is joining'
        : status === 'listening'
      ? language === 'ar' ? 'أسمعك الحين' : 'Listening now'
      : status === 'speaking'
        ? language === 'ar' ? 'المرشد يتكلم' : 'Guide is speaking'
        : status === 'thinking'
          ? language === 'ar' ? 'كودا تفكّر' : 'Coda is thinking'
          : status === 'error'
            ? language === 'ar' ? 'تعذّر الاتصال' : 'Connection needs attention'
            : active
              ? language === 'ar' ? 'كودا جاهزة' : 'Coda is ready'
              : language === 'ar' ? 'مرشد كودا المباشر' : 'Coda live guide';

  return (
    <section className={`voice-dock voice-dock--${status}`}>
      <div className="voice-copy">
        <div className="voice-state">
          <span className="voice-state__icon"><Volume2 size={14} /></span>
          {stateLabel}
        </div>
        <p>{message}</p>
        {transcript && (
          <blockquote>
            <strong>
              {transcript.speaker === 'user'
                ? language === 'ar' ? 'أنت' : 'You'
                : 'Coda'}:
            </strong>{' '}
            “{transcript.text}”
          </blockquote>
        )}
        {error && <small role="alert">{error}</small>}
      </div>

      <button
        className={`voice-button ${active ? 'voice-button--active' : ''}`}
        onClick={onVoiceAction}
        aria-pressed={active}
        aria-label={active
          ? language === 'ar' ? 'إنهاء محادثة كودا' : 'End Coda conversation'
          : language === 'ar' ? 'ابدأ محادثة كودا' : 'Start Coda conversation'}
      >
        <span className="voice-button__halo" />
        <span className="voice-button__core">
          {active ? <Square size={20} fill="currentColor" /> : <Mic size={25} />}
        </span>
      </button>

      <div className={`waveform ${active && status !== 'connecting' ? 'waveform--active' : ''}`} aria-hidden="true">
        {Array.from({ length: 16 }, (_, index) => <span key={index} style={{ animationDelay: `${index * -0.07}s` }} />)}
      </div>
    </section>
  );
}
