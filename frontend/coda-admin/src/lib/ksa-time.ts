/**
 * Riyadh wall-clock ↔ instant.
 *
 * Saudi Arabia is UTC+3 all year and has observed no DST since 1990, so a fixed
 * offset is exact here — not the approximation it would be for a zone that
 * shifts. That is what lets the conversion be arithmetic instead of a lookup,
 * and it is why the offset is written once, here, rather than inline.
 *
 * Client-safe: imported by the dashboard components as well as route code.
 */
export const KSA_OFFSET = '+03:00';
export const KSA_OFFSET_MS = 3 * 60 * 60 * 1000;

/** '2026-08-31' + '10:00' as typed in Riyadh → the UTC instant it names. */
export function ksaFieldsToIso(date: string, time: string): string {
  const parsed = new Date(`${date}T${time}:00${KSA_OFFSET}`);
  if (Number.isNaN(parsed.getTime())) throw new Error('invalid_publish_time');
  return parsed.toISOString();
}

/** The inverse: an instant → the date and time an operator in Riyadh sees. */
export function isoToKsaFields(iso: string): { date: string; time: string } {
  const shifted = new Date(new Date(iso).getTime() + KSA_OFFSET_MS);
  const stamp = shifted.toISOString();
  return { date: stamp.slice(0, 10), time: stamp.slice(11, 16) };
}

/** Human-readable Riyadh time, for tables and confirmations. */
export function formatKsa(iso?: string | null, withTime = true): string {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('en-GB', {
      dateStyle: 'medium',
      ...(withTime ? { timeStyle: 'short' as const } : {}),
      timeZone: 'Asia/Riyadh',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

/** 'in 3 hours' / '2 days ago', relative to now. */
export function relativeToNow(iso?: string | null): string {
  if (!iso) return '';
  const diff = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(diff)) return '';
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['day', 86_400_000],
    ['hour', 3_600_000],
    ['minute', 60_000],
  ];
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
  for (const [unit, ms] of units) {
    if (Math.abs(diff) >= ms) return formatter.format(Math.round(diff / ms), unit);
  }
  return formatter.format(Math.round(diff / 1000), 'second');
}

/** The next half-hour in Riyadh, as the form's default publish time. */
export function defaultPublishFields(): { date: string; time: string } {
  const now = new Date(Date.now() + KSA_OFFSET_MS);
  now.setUTCMinutes(now.getUTCMinutes() + 30 - (now.getUTCMinutes() % 30), 0, 0);
  const stamp = now.toISOString();
  return { date: stamp.slice(0, 10), time: stamp.slice(11, 16) };
}
