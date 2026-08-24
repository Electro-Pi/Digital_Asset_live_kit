import { useEffect, useRef, useState } from 'react';
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
import { VoiceDock, type VoiceStatus } from './components/VoiceDock';
import {
  locationById,
  locationName,
  locations,
  locationZone,
  nodeById,
  type Language,
  type MapLocation,
} from './data/mapData';
import { createRoute, findLocationFromSpeech, routeSpeech, type RoutePlan } from './lib/navigation';

const copy = {
  en: {
    badge: 'VOICE-GUIDED INDOOR NAVIGATION',
    title: 'Find your way,',
    titleAccent: 'without the guesswork.',
    subtitle: 'Tell the guide where you are and where you want to go. Your route appears directly on the original event plan.',
    location: 'YOUR LOCATION',
    destination: 'DESTINATION',
    chooseLocation: 'Choose where you are',
    chooseDestination: 'Where do you want to go?',
    start: 'Start voice navigation',
    suggested: 'POPULAR DESTINATIONS',
    askStart: 'Hi! Where are you located right now?',
    askDestination: 'Great. Where would you like to go?',
    tapVoice: 'Tap the microphone and tell me where you are.',
    routeReady: 'Your route is ready. Tap any direction to hear it again.',
    noMatch: 'I could not match that place. Try saying a name shown on the map.',
    samePlace: 'Your destination needs to be different from your current location.',
    chooseBoth: 'Choose a current location and a destination to build your route.',
    heardAt: (name: string) => `Got it, you are at ${name}. Where would you like to go?`,
    selectedMapStart: (name: string) => `${name} set as your current location. Now choose a destination.`,
    selectedMapDestination: (name: string) => `${name} set as your destination. Your route is ready to start.`,
    powered: 'Calibrated to the uploaded floor plan',
    estimate: 'Distances are indoor-plan estimates. Advance steps manually; live positioning needs venue beacons.',
  },
  ar: {
    badge: 'ملاحة داخلية بالصوت',
    title: 'وصل وجهتك،',
    titleAccent: 'بدون ما تحتار.',
    subtitle: 'قل للمرشد وينك ووين تبي تروح، وبيطلع مسارك مباشرة على مخطط الفعالية الأصلي.',
    location: 'موقعك الحالي',
    destination: 'الوجهة',
    chooseLocation: 'اختر موقعك الحالي',
    chooseDestination: 'وين تبي تروح؟',
    start: 'ابدأ الملاحة الصوتية',
    suggested: 'وجهات مشهورة',
    askStart: 'هلا والله! وين موقعك الحين؟',
    askDestination: 'زين، وين تبي تروح؟',
    tapVoice: 'اضغط المايك وقل لي وينك.',
    routeReady: 'مسارك جاهز. اضغط على أي خطوة عشان تسمعها مرة ثانية.',
    noMatch: 'ما قدرت أحدد المكان. جرّب تقول اسم واضح من الخريطة.',
    samePlace: 'وجهتك لازم تكون غير موقعك الحالي.',
    chooseBoth: 'اختر موقعك الحالي والوجهة عشان أرسم لك المسار.',
    heardAt: (name: string) => `تمام، أنت عند ${name}. وين تبي تروح؟`,
    selectedMapStart: (name: string) => `حددنا ${name} كموقعك الحالي. الحين اختر وجهتك.`,
    selectedMapDestination: (name: string) => `حددنا ${name} كوجهتك. مسارك جاهز للبدء.`,
    powered: 'مضبوطة على مخطط الفعالية المرفوع',
    estimate: 'المسافات تقديرية من المخطط. تقدم يدوياً بين الخطوات؛ التتبع الحي يحتاج حساسات داخل الموقع.',
  },
};

type VoiceTarget = 'start' | 'destination' | 'command';

export default function App() {
  const [language, setLanguage] = useState<Language>('en');
  const [startId, setStartId] = useState('');
  const [destinationId, setDestinationId] = useState('');
  const [picking, setPicking] = useState<'start' | 'destination'>('start');
  const [route, setRoute] = useState<RoutePlan | null>(null);
  const [activeStep, setActiveStep] = useState(0);
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>('idle');
  const [voiceMessage, setVoiceMessage] = useState(copy.en.askStart);
  const [transcript, setTranscript] = useState('');
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);

  const t = copy[language];
  const start = startId ? locationById.get(startId) : undefined;
  const destination = destinationId ? locationById.get(destinationId) : undefined;
  const currentPoint = route
    ? nodeById.get(route.steps[Math.min(activeStep, route.steps.length - 1)].fromNodeId)
    : start ? nodeById.get(start.nodeId) : undefined;

  const Recognition = typeof window !== 'undefined'
    ? window.SpeechRecognition ?? window.webkitSpeechRecognition
    : undefined;
  const recognitionSupported = Boolean(Recognition);

  useEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
  }, [language]);

  useEffect(() => () => {
    recognitionRef.current?.abort();
    window.speechSynthesis?.cancel();
  }, []);

  function speak(text: string, onComplete?: () => void) {
    if (!('speechSynthesis' in window)) {
      onComplete?.();
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = language === 'ar' ? 'ar-SA' : 'en-US';
    utterance.rate = language === 'ar' ? 0.92 : 0.96;
    utterance.pitch = 1.02;
    utterance.onstart = () => setVoiceStatus('speaking');
    utterance.onend = () => {
      setVoiceStatus('idle');
      onComplete?.();
    };
    utterance.onerror = () => setVoiceStatus('error');
    window.speechSynthesis.speak(utterance);
  }

  function listenFor(target: VoiceTarget) {
    if (!Recognition) return;
    recognitionRef.current?.abort();
    const recognition = new Recognition();
    recognitionRef.current = recognition;
    recognition.lang = language === 'ar' ? 'ar-SA' : 'en-US';
    recognition.continuous = false;
    recognition.interimResults = false;
    let handled = false;
    recognition.onstart = () => setVoiceStatus('listening');
    recognition.onresult = (event) => {
      handled = true;
      const heard = event.results[0][0].transcript.trim();
      setTranscript(heard);
      handleVoiceTranscript(target, heard);
    };
    recognition.onerror = () => {
      setVoiceStatus('error');
      setVoiceMessage(t.noMatch);
    };
    recognition.onend = () => {
      if (!handled) setVoiceStatus('idle');
    };
    recognition.start();
  }

  function activateRoute(fromId: string, toId: string, announce = true) {
    if (!fromId || !toId) {
      setVoiceMessage(t.chooseBoth);
      return;
    }
    if (fromId === toId) {
      setVoiceMessage(t.samePlace);
      speak(t.samePlace);
      return;
    }
    const plan = createRoute(fromId, toId, language);
    if (!plan) return;
    setRoute(plan);
    setActiveStep(0);
    setVoiceMessage(t.routeReady);
    if (announce) speak(routeSpeech(plan, language));
  }

  function handleVoiceTranscript(target: VoiceTarget, heard: string) {
    const normalized = heard.toLocaleLowerCase();
    if (target === 'command' && route) {
      if (/next|continue|التالي|كمل|كمّل/.test(normalized)) {
        goToNextStep();
        return;
      }
      if (/repeat|again|أعد|كرر|عيد/.test(normalized)) {
        speakCurrentStep();
        return;
      }
    }

    const matched = findLocationFromSpeech(heard);
    if (!matched) {
      setVoiceMessage(t.noMatch);
      speak(t.noMatch);
      return;
    }

    if (target === 'start' || !startId) {
      setStartId(matched.id);
      setPicking('destination');
      setRoute(null);
      const message = t.heardAt(locationName(matched, language));
      setVoiceMessage(message);
      speak(message, () => listenFor('destination'));
      return;
    }

    if (matched.id === startId) {
      setVoiceMessage(t.samePlace);
      speak(t.samePlace, () => listenFor('destination'));
      return;
    }

    setDestinationId(matched.id);
    activateRoute(startId, matched.id, true);
  }

  function onVoiceAction() {
    if (voiceStatus === 'speaking' || voiceStatus === 'listening') {
      recognitionRef.current?.abort();
      window.speechSynthesis?.cancel();
      setVoiceStatus('idle');
      return;
    }
    setTranscript('');
    if (route) {
      const prompt = language === 'ar'
        ? 'قل التالي، أو أعد الخطوة، أو اسم وجهة جديدة.'
        : 'Say next, repeat, or the name of a new destination.';
      setVoiceMessage(prompt);
      speak(prompt, () => listenFor('command'));
      return;
    }
    if (!startId) {
      setVoiceMessage(t.askStart);
      speak(t.askStart, () => listenFor('start'));
      return;
    }
    setVoiceMessage(t.askDestination);
    speak(t.askDestination, () => listenFor('destination'));
  }

  function onMapSelect(location: MapLocation) {
    if (picking === 'start') {
      setStartId(location.id);
      setRoute(null);
      setActiveStep(0);
      setPicking('destination');
      setVoiceMessage(t.selectedMapStart(locationName(location, language)));
      return;
    }
    if (location.id === startId) {
      setVoiceMessage(t.samePlace);
      return;
    }
    setDestinationId(location.id);
    setRoute(null);
    setActiveStep(0);
    setVoiceMessage(t.selectedMapDestination(locationName(location, language)));
  }

  function onStartChange(value: string) {
    setStartId(value);
    setRoute(null);
    setActiveStep(0);
    setPicking('destination');
    setVoiceMessage(value ? t.askDestination : t.askStart);
  }

  function onDestinationChange(value: string) {
    setDestinationId(value);
    setRoute(null);
    setActiveStep(0);
    setVoiceMessage(value ? t.tapVoice : t.askDestination);
  }

  function swapLocations() {
    if (!startId && !destinationId) return;
    setStartId(destinationId);
    setDestinationId(startId);
    setPicking(destinationId ? 'destination' : 'start');
    if (route && destinationId && startId) {
      const reversed = createRoute(destinationId, startId, language);
      setRoute(reversed);
      setActiveStep(0);
    } else {
      setRoute(null);
    }
  }

  function changeLanguage(nextLanguage: Language) {
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

  function speakCurrentStep() {
    if (!route) return;
    const step = route.steps[activeStep];
    speak(`${step.instruction} ${step.note ?? ''}`.trim());
  }

  function goToNextStep() {
    if (!route || activeStep >= route.steps.length - 1) return;
    const next = activeStep + 1;
    setActiveStep(next);
    const step = route.steps[next];
    speak(`${step.instruction} ${step.note ?? ''}`.trim());
  }

  function goToPreviousStep() {
    if (!route || activeStep === 0) return;
    setActiveStep((step) => step - 1);
  }

  const popularDestinationIds = ['leap-arena', 'deep-stage', 'food-zone', 'investors-stage'];

  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="#top" aria-label="LEAP Wayfinder home">
          <span className="brand-mark"><Navigation size={20} fill="currentColor" /></span>
          <span><strong>LEAP</strong><em>WAYFINDER</em></span>
        </a>

        <div className="header-center">
          <span className="system-status"><span /> {language === 'ar' ? 'الخريطة جاهزة' : 'MAP READY'}</span>
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
            status={voiceStatus}
            message={voiceMessage}
            transcript={transcript}
            recognitionSupported={recognitionSupported}
            onVoiceAction={onVoiceAction}
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
                          {locationName(location, language)} · {locationZone(location, language)}
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
                          {locationName(location, language)} · {locationZone(location, language)}
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

            <button className="start-button" onClick={() => activateRoute(startId, destinationId, true)}>
              <Route size={19} />
              {t.start}
              <ArrowRight size={19} className="start-button__arrow" />
            </button>
          </section>

          {route && (
            <RoutePanel
              language={language}
              route={route}
              activeStep={activeStep}
              onPrevious={goToPreviousStep}
              onNext={goToNextStep}
              onSpeakStep={speakCurrentStep}
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
