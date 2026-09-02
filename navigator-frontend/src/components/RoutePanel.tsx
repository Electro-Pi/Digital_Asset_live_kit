import { CornerUpLeft, CornerUpRight, MapPin, MoveUp, Navigation } from 'lucide-react';
import { locationById, locationDisplayName, type Language } from '../data/mapData';
import type { RoutePlan, RouteStep } from '../lib/navigation';

interface RoutePanelProps {
  language: Language;
  route: RoutePlan;
}

function ManeuverIcon({ maneuver, size = 22 }: { maneuver: RouteStep['maneuver']; size?: number }) {
  if (maneuver === 'left') return <CornerUpLeft size={size} />;
  if (maneuver === 'right') return <CornerUpRight size={size} />;
  if (maneuver === 'arrive') return <MapPin size={size} />;
  if (maneuver === 'depart') return <Navigation size={size} />;
  return <MoveUp size={size} />;
}

export function RoutePanel({ language, route }: RoutePanelProps) {
  const destination = locationById.get(route.destinationId)!;
  const numberFormatter = new Intl.NumberFormat(language === 'ar' ? 'ar-SA' : 'en-US');
  const directionCount = numberFormatter.format(route.steps.length);

  return (
    <section className="route-panel" aria-live="polite" aria-labelledby="route-directions-title">
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
          <strong>{locationDisplayName(destination, language)}</strong>
        </div>
      </div>

      <div className="route-directions">
        <div className="route-directions__header">
          <div>
            <small id="route-directions-title">{language === 'ar' ? 'المسار الكامل' : 'FULL ROUTE'}</small>
            <strong>{language === 'ar' ? 'كل التعليمات بالترتيب' : 'Every direction, in order'}</strong>
          </div>
          <span>
            {language === 'ar'
              ? `${directionCount} تعليمات`
              : `${directionCount} ${route.steps.length === 1 ? 'direction' : 'directions'}`}
          </span>
        </div>

        <ol className="route-directions__list" aria-label={language === 'ar' ? 'تعليمات المسار بالترتيب' : 'Ordered route directions'}>
          {route.steps.map((step, index) => (
            <li className="route-direction" key={step.id}>
              <span className={`maneuver maneuver--${step.maneuver}`} aria-hidden="true">
                <ManeuverIcon maneuver={step.maneuver} size={22} />
              </span>
              <span className="route-direction__copy">
                <small>
                  {language === 'ar'
                    ? `التعليمة ${numberFormatter.format(index + 1)}`
                    : `DIRECTION ${index + 1}`}
                </small>
                <strong>{step.instruction}</strong>
                {step.note && <em>{step.note}</em>}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
