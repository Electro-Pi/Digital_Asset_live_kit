import { ArrowLeft, ArrowRight, Check, ChevronRight, CornerUpLeft, CornerUpRight, MapPin, MoveUp, Navigation } from 'lucide-react';
import { locationById, type Language } from '../data/mapData';
import type { RoutePlan, RouteStep } from '../lib/navigation';

interface RoutePanelProps {
  language: Language;
  route: RoutePlan;
  activeStep: number;
  onPrevious: () => void;
  onNext: () => void;
  onSpeakStep: () => void;
}

function ManeuverIcon({ maneuver, size = 22 }: { maneuver: RouteStep['maneuver']; size?: number }) {
  if (maneuver === 'left') return <CornerUpLeft size={size} />;
  if (maneuver === 'right') return <CornerUpRight size={size} />;
  if (maneuver === 'arrive') return <MapPin size={size} />;
  if (maneuver === 'depart') return <Navigation size={size} />;
  return <MoveUp size={size} />;
}

export function RoutePanel({ language, route, activeStep, onPrevious, onNext, onSpeakStep }: RoutePanelProps) {
  const step = route.steps[activeStep];
  const destination = locationById.get(route.destinationId)!;
  const isLast = activeStep === route.steps.length - 1;

  return (
    <section className="route-panel" aria-live="polite">
      <div className="route-summary">
        <div className="route-summary__metric">
          <strong>{route.durationMinutes}</strong>
          <span>{language === 'ar' ? 'دقيقة' : route.durationMinutes === 1 ? 'min' : 'mins'}</span>
        </div>
        <span className="summary-divider" />
        <div className="route-summary__metric">
          <strong>{route.distanceMeters}</strong>
          <span>{language === 'ar' ? 'متر تقريباً' : 'metres · est.'}</span>
        </div>
        <div className="route-summary__destination">
          <span>{language === 'ar' ? 'إلى' : 'TO'}</span>
          <strong>{language === 'ar' ? destination.nameAr : destination.name}</strong>
        </div>
      </div>

      <button className="current-instruction" onClick={onSpeakStep}>
        <span className={`maneuver maneuver--${step.maneuver}`}><ManeuverIcon maneuver={step.maneuver} size={28} /></span>
        <span>
          <small>{language === 'ar' ? `الخطوة ${activeStep + 1} من ${route.steps.length}` : `STEP ${activeStep + 1} OF ${route.steps.length}`}</small>
          <strong>{step.instruction}</strong>
          {step.note && <em>{step.note}</em>}
        </span>
        <ChevronRight size={19} className="instruction-play" />
      </button>

      <div className="step-progress" aria-hidden="true">
        {route.steps.map((routeStep, index) => (
          <span key={routeStep.id} className={index <= activeStep ? 'is-complete' : ''} />
        ))}
      </div>

      <div className="route-actions">
        <button className="button button--secondary" onClick={onPrevious} disabled={activeStep === 0}>
          {language === 'ar' ? <ArrowRight size={17} /> : <ArrowLeft size={17} />}
          {language === 'ar' ? 'السابق' : 'Previous'}
        </button>
        <button className="button button--primary" onClick={onNext} disabled={isLast}>
          {isLast ? <Check size={18} /> : language === 'ar' ? <ArrowLeft size={18} /> : <ArrowRight size={18} />}
          {isLast ? language === 'ar' ? 'وصلت' : 'Arrived' : language === 'ar' ? 'الخطوة التالية' : 'Next step'}
        </button>
      </div>
    </section>
  );
}
