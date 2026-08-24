import { Mic, Square, Volume2 } from 'lucide-react';
import type { Language } from '../data/mapData';

export type VoiceStatus = 'idle' | 'listening' | 'speaking' | 'error';

interface VoiceDockProps {
  language: Language;
  status: VoiceStatus;
  message: string;
  transcript: string;
  recognitionSupported: boolean;
  onVoiceAction: () => void;
}

export function VoiceDock({
  language,
  status,
  message,
  transcript,
  recognitionSupported,
  onVoiceAction,
}: VoiceDockProps) {
  const active = status === 'listening' || status === 'speaking';
  const stateLabel =
    status === 'listening'
      ? language === 'ar' ? 'أسمعك الحين' : 'Listening now'
      : status === 'speaking'
        ? language === 'ar' ? 'المرشد يتكلم' : 'Guide is speaking'
        : language === 'ar' ? 'المرشد الصوتي جاهز' : 'Voice guide ready';

  return (
    <section className={`voice-dock voice-dock--${status}`}>
      <div className="voice-copy">
        <div className="voice-state">
          <span className="voice-state__icon"><Volume2 size={14} /></span>
          {stateLabel}
        </div>
        <p>{message}</p>
        {transcript && <blockquote>“{transcript}”</blockquote>}
        {!recognitionSupported && (
          <small>{language === 'ar' ? 'الإدخال الصوتي يحتاج Chrome أو Edge. تقدر تستخدم القوائم.' : 'Voice input needs Chrome or Edge. The selectors still work.'}</small>
        )}
      </div>

      <button
        className={`voice-button ${active ? 'voice-button--active' : ''}`}
        onClick={onVoiceAction}
        aria-label={active ? 'Stop voice' : 'Start voice guide'}
      >
        <span className="voice-button__halo" />
        <span className="voice-button__core">
          {active ? <Square size={20} fill="currentColor" /> : <Mic size={25} />}
        </span>
      </button>

      <div className={`waveform ${active ? 'waveform--active' : ''}`} aria-hidden="true">
        {Array.from({ length: 16 }, (_, index) => <span key={index} style={{ animationDelay: `${index * -0.07}s` }} />)}
      </div>
    </section>
  );
}
