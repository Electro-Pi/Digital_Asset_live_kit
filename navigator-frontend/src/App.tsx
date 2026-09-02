import { useCallback, useEffect, useState } from 'react';
import {
  ArrowRight,
  ArrowUpDown,
  ChevronDown,
  Languages,
  MapPinned,
  Navigation,
  Route,
  Sparkles,
} from 'lucide-react';
import { MapView } from './components/MapView';
import { RoutePanel } from './components/RoutePanel';
import { VoiceDock } from './components/VoiceDock';
import {
  locationById,
  locationDisplayName,
  locations,
  locationZone,
  nodeById,
  type Language,
  type MapLocation,
} from './data/mapData';
import { useLiveKitVoice, type NavigationRouteAction } from './hooks/useLiveKitVoice';
import {
  buildRouteNarration,
  createRoute,
  type NavigationRouteNarration,
  type RoutePlan,
} from './lib/navigation';

const copy = {
  en: {
    badge: 'VOICE-GUIDED INDOOR NAVIGATION',
    title: 'Find your way,',
    titleAccent: 'without the guesswork.',
    subtitle: 'Tell the guide where you are and where you want to go, then explore the destination on the live 3D venue map.',
    location: 'YOUR LOCATION',
    destination: 'DESTINATION',
    chooseLocation: 'Choose where you are',
    chooseDestination: 'Where do you want to go?',
    start: 'Show full route & start Coda',
    suggested: 'POPULAR DESTINATIONS',
    askStart: 'Hi! Where are you located right now?',
    askDestination: 'Great. Where would you like to go?',
    tapVoice: 'Tap the microphone to talk naturally with the live Coda guide.',
    connecting: 'Connecting you to Coda through LiveKit…',
    connected: 'You are live with Coda. Say where you are and where you want to go.',
    connectionFailed: 'Coda could not connect. Check microphone permission and try again.',
    routeReady: 'Your full route is ready. Coda will read every direction once.',
    narrationFailed: 'Your full route is visible, but Coda could not read it. Reconnect and try again.',
    samePlace: 'Your destination needs to be different from your current location.',
    chooseBoth: 'Choose a current location and a destination to build your route.',
    selectedMapStart: (name: string) => `${name} set as your current location. Now choose a destination.`,
    selectedMapDestination: (name: string) => `${name} set as your destination. Your route is ready to start.`,
    powered: 'LIVE LEAP 2026 3D VENUE MAP',
    estimate: 'Route guidance uses calibrated indoor-plan estimates. The live 3D venue viewer requires network access.',
  },
  ar: {
    badge: 'ملاحة داخلية بالصوت',
    title: 'وصل وجهتك،',
    titleAccent: 'بدون ما تحتار.',
    subtitle: 'قل للمرشد وينك ووين تبي تروح، وبعدها استكشف وجهتك على خريطة الموقع الحية ثلاثية الأبعاد.',
    location: 'موقعك الحالي',
    destination: 'الوجهة',
    chooseLocation: 'اختر موقعك الحالي',
    chooseDestination: 'وين تبي تروح؟',
    start: 'اعرض المسار الكامل وابدأ مع كودا',
    suggested: 'وجهات مشهورة',
    askStart: 'هلا والله! وين موقعك الحين؟',
    askDestination: 'زين، وين تبي تروح؟',
    tapVoice: 'اضغط المايك وتكلم بشكل طبيعي مع مرشدة كودا المباشرة.',
    connecting: 'جاري توصيلك بكودا عن طريق لايف كِت…',
    connected: 'أنت متصل بكودا. قل لها وينك ووين تبي تروح.',
    connectionFailed: 'تعذّر اتصال كودا. تأكد من إذن المايك وجرّب مرة ثانية.',
    routeReady: 'مسارك الكامل جاهز، وكودا بتقرأ لك كل التعليمات مرة وحدة.',
    narrationFailed: 'مسارك الكامل ظاهر، لكن كودا ما قدرت تقرأه. أعد الاتصال وجرّب مرة ثانية.',
    samePlace: 'وجهتك لازم تكون غير موقعك الحالي.',
    chooseBoth: 'اختر موقعك الحالي والوجهة عشان أرسم لك المسار.',
    selectedMapStart: (name: string) => `حددنا ${name} كموقعك الحالي. الحين اختر وجهتك.`,
    selectedMapDestination: (name: string) => `حددنا ${name} كوجهتك. مسارك جاهز للبدء.`,
    powered: 'خريطة ليب ٢٠٢٦ الحية ثلاثية الأبعاد',
    estimate: 'إرشادات المسار تقديرية من المخطط الداخلي. خريطة الموقع الحية تحتاج اتصالاً بالإنترنت.',
  },
};

export default function App() {
  const [language, setLanguage] = useState<Language>('en');
  const [startId, setStartId] = useState('');
  const [destinationId, setDestinationId] = useState('');
  const [picking, setPicking] = useState<'start' | 'destination'>('start');
  const [route, setRoute] = useState<RoutePlan | null>(null);
  const [pendingNarration, setPendingNarration] = useState<NavigationRouteNarration | null>(null);
  const [voiceMessage, setVoiceMessage] = useState(copy.en.tapVoice);

  const t = copy[language];
  const start = startId ? locationById.get(startId) : undefined;
  const destination = destinationId ? locationById.get(destinationId) : undefined;
  const currentPoint = start ? nodeById.get(start.nodeId) : undefined;

  const handleAgentRoute = useCallback((action: NavigationRouteAction) => {
    const nextStart = locationById.get(action.startId);
    const nextDestination = locationById.get(action.destinationId);
    if (!nextStart || !nextDestination || nextStart.id === nextDestination.id) return;
    const plan = createRoute(nextStart.id, nextDestination.id, language);
    if (!plan) return;
    setStartId(nextStart.id);
    setDestinationId(nextDestination.id);
    setPicking('destination');
    setRoute(plan);
    setPendingNarration(buildRouteNarration(plan, language));
    setVoiceMessage(copy[language].routeReady);
  }, [language]);

  const voice = useLiveKitVoice({ onRouteAction: handleAgentRoute });

  useEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
  }, [language]);

  useEffect(() => {
    if (!voice.isConnected) return;
    void voice.updateContext({ language, startId, destinationId }).catch(() => undefined);
  }, [destinationId, language, startId, voice.isConnected, voice.updateContext]);

  useEffect(() => {
    // LiveKit data packets are not buffered for participants that have not
    // joined yet. Waiting for Coda's listening state guarantees that the agent
    // session and its narration handler are ready before this one-shot packet.
    if (!pendingNarration || !voice.isConnected || voice.status !== 'listening') return;
    const narration = pendingNarration;
    setPendingNarration(null);
    void voice.publishRouteNarration(narration)
      .then((published) => {
        if (!published) setVoiceMessage(copy[language].narrationFailed);
      })
      .catch(() => setVoiceMessage(copy[language].narrationFailed));
  }, [language, pendingNarration, voice.isConnected, voice.publishRouteNarration, voice.status]);

  async function activateRoute(fromId: string, toId: string) {
    if (!fromId || !toId) {
      setVoiceMessage(t.chooseBoth);
      return;
    }
    if (fromId === toId) {
      setVoiceMessage(t.samePlace);
      return;
    }
    const plan = createRoute(fromId, toId, language);
    if (!plan) return;
    setRoute(plan);
    setPendingNarration(buildRouteNarration(plan, language));
    setVoiceMessage(t.routeReady);

    if (voice.isConnected) {
      return;
    }

    try {
      await voice.connect({ language, startId: fromId, destinationId: toId });
    } catch {
      setVoiceMessage(t.connectionFailed);
    }
  }

  async function onVoiceAction() {
    if (voice.isActive || voice.isConnected) {
      setPendingNarration(null);
      await voice.disconnect();
      setVoiceMessage(t.tapVoice);
      return;
    }
    setVoiceMessage(t.connecting);
    if (startId && destinationId && startId !== destinationId) {
      const plan = createRoute(startId, destinationId, language);
      if (plan) {
        setRoute(plan);
        setPendingNarration(buildRouteNarration(plan, language));
      }
    }
    try {
      await voice.connect({ language, startId, destinationId });
      setVoiceMessage(t.connected);
    } catch {
      setVoiceMessage(t.connectionFailed);
    }
  }

  function onMapSelect(location: MapLocation) {
    if (picking === 'start') {
      setStartId(location.id);
      setRoute(null);
      setPendingNarration(null);
      setPicking('destination');
      setVoiceMessage(t.selectedMapStart(locationDisplayName(location, language)));
      return;
    }
    if (location.id === startId) {
      setVoiceMessage(t.samePlace);
      return;
    }
    setDestinationId(location.id);
    setRoute(null);
    setPendingNarration(null);
    setVoiceMessage(t.selectedMapDestination(locationDisplayName(location, language)));
  }

  function onStartChange(value: string) {
    setStartId(value);
    setRoute(null);
    setPendingNarration(null);
    setPicking('destination');
    setVoiceMessage(value ? t.askDestination : t.askStart);
  }

  function onDestinationChange(value: string) {
    setDestinationId(value);
    setRoute(null);
    setPendingNarration(null);
    setVoiceMessage(value ? t.tapVoice : t.askDestination);
  }

  function swapLocations() {
    if (!startId && !destinationId) return;
    setStartId(destinationId);
    setDestinationId(startId);
    setPicking(destinationId ? 'destination' : 'start');
    setPendingNarration(null);
    if (route && destinationId && startId) {
      const reversed = createRoute(destinationId, startId, language);
      setRoute(reversed);
    } else {
      setRoute(null);
    }
  }

  function changeLanguage(nextLanguage: Language) {
    if (voice.isActive || voice.isConnected) void voice.disconnect();
    setPendingNarration(null);
    setLanguage(nextLanguage);
    const nextCopy = copy[nextLanguage];
    if (route) {
      setRoute(createRoute(route.startId, route.destinationId, nextLanguage));
      setVoiceMessage(nextCopy.routeReady);
    } else if (!startId) {
      setVoiceMessage(nextCopy.askStart);
    } else if (!destinationId) {
      setVoiceMessage(nextCopy.askDestination);
    } else {
      setVoiceMessage(nextCopy.tapVoice);
    }
  }

  const popularDestinationIds = ['leader-pass-investors', 'ai-deep-tech-a', 'info-hub', 'prayer-room-east'];

  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="#top" aria-label="LEAP Wayfinder home">
          <span className="brand-mark"><Navigation size={20} fill="currentColor" /></span>
          <span><strong>LEAP</strong><em>WAYFINDER</em></span>
        </a>

        <div className="header-center">
          <span className="system-status"><span /> {voice.isConnected
            ? language === 'ar' ? 'كودا متصلة' : 'CODA LIVE'
            : language === 'ar' ? 'الخريطة جاهزة' : 'MAP READY'}</span>
          <span className="header-divider" />
          <span>{t.powered}</span>
        </div>

        <div className="language-switch" aria-label="Language">
          <Languages size={16} />
          <button className={language === 'en' ? 'active' : ''} onClick={() => changeLanguage('en')}>EN</button>
          <button className={language === 'ar' ? 'active' : ''} onClick={() => changeLanguage('ar')}>ع</button>
        </div>
      </header>

      <main className="workspace" id="top">
        <aside className="navigation-sidebar">
          <div className="intro-block">
            <div className="eyebrow"><Sparkles size={14} /> {t.badge}</div>
            <h1>{t.title}<br /><span>{t.titleAccent}</span></h1>
            <p>{t.subtitle}</p>
          </div>

          <VoiceDock
            language={language}
            status={voice.status}
            active={voice.isActive || voice.isConnected}
            message={voiceMessage}
            transcript={voice.lastTranscript}
            error={voice.error}
            onVoiceAction={() => void onVoiceAction()}
          />

          <section className="planner" aria-label="Route planner">
            <div className="location-fields">
              <label className={`location-field ${picking === 'start' ? 'location-field--active' : ''}`}>
                <span className="field-marker field-marker--start"><span /></span>
                <span className="field-copy">
                  <small>{t.location}</small>
                  <span className="select-wrap">
                    <select
                      value={startId}
                      onChange={(event) => onStartChange(event.target.value)}
                      onFocus={() => setPicking('start')}
                    >
                      <option value="">{t.chooseLocation}</option>
                      {locations.map((location) => (
                        <option key={location.id} value={location.id}>
                          {locationDisplayName(location, language)} · {locationZone(location, language)}
                        </option>
                      ))}
                    </select>
                    <ChevronDown size={17} />
                  </span>
                </span>
              </label>

              <button className="swap-button" onClick={swapLocations} aria-label="Swap locations">
                <ArrowUpDown size={17} />
              </button>

              <label className={`location-field ${picking === 'destination' ? 'location-field--active' : ''}`}>
                <span className="field-marker field-marker--destination"><MapPinned size={17} /></span>
                <span className="field-copy">
                  <small>{t.destination}</small>
                  <span className="select-wrap">
                    <select
                      value={destinationId}
                      onChange={(event) => onDestinationChange(event.target.value)}
                      onFocus={() => setPicking('destination')}
                    >
                      <option value="">{t.chooseDestination}</option>
                      {locations.map((location) => (
                        <option key={location.id} value={location.id} disabled={location.id === startId}>
                          {locationDisplayName(location, language)} · {locationZone(location, language)}
                        </option>
                      ))}
                    </select>
                    <ChevronDown size={17} />
                  </span>
                </span>
              </label>
            </div>

            <div className="suggestions">
              <span>{t.suggested}</span>
              <div>
                {popularDestinationIds.map((id) => {
                  const location = locationById.get(id)!;
                  return (
                    <button
                      key={id}
                      className={destinationId === id ? 'active' : ''}
                      onClick={() => onDestinationChange(id)}
                    >
                      {language === 'ar' ? location.shortNameAr : location.shortName}
                    </button>
                  );
                })}
              </div>
            </div>

            <button className="start-button" onClick={() => void activateRoute(startId, destinationId)}>
              <Route size={19} />
              {t.start}
              <ArrowRight size={19} className="start-button__arrow" />
            </button>
          </section>

          {route && (
            <RoutePanel
              language={language}
              route={route}
            />
          )}

          <p className="map-disclaimer">{t.estimate}</p>
        </aside>

        <MapView
          language={language}
          route={route}
          currentPoint={currentPoint}
          start={start}
          destination={destination}
          picking={picking}
          onSelectLocation={onMapSelect}
        />
      </main>
    </div>
  );
}
