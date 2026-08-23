'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { adminFetch, apiErrorMessage } from './admin-api';
import type { LimitPayload, PlanLimits } from './admin-types';
import { Icon } from './ui/Icons';
import { Badge, Button, ErrorState, PageHeading, TableSkeleton, formatNumber } from './ui/Primitives';

type PlanForm = Record<keyof LimitPayload, string>;

const fields: Array<{ key: keyof LimitPayload; label: string; description: string; icon: 'wave' | 'volume' | 'microphone' | 'message'; unit: string }> = [
  { key: 'agentMinutes', label: 'Agent time', description: 'Voice-agent conversation time', icon: 'wave', unit: 'minutes' },
  { key: 'ttsRequests', label: 'Text to speech', description: 'Speech generation API calls', icon: 'volume', unit: 'requests' },
  { key: 'sttRequests', label: 'Speech to text', description: 'Transcription API calls', icon: 'microphone', unit: 'requests' },
  { key: 'llmRequests', label: 'Language model', description: 'LLM completion requests', icon: 'message', unit: 'requests' },
];

function planForm(plan: PlanLimits): PlanForm {
  return {
    agentMinutes: String(plan.agentMinutes),
    ttsRequests: String(plan.ttsRequests),
    sttRequests: String(plan.sttRequests),
    llmRequests: String(plan.llmRequests),
  };
}

function PlanCard({ plan, onUpdated }: { plan: PlanLimits; onUpdated: (plan: PlanLimits) => void }) {
  const [form, setForm] = useState<PlanForm>(() => planForm(plan));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => setForm(planForm(plan)), [plan]);
  const dirty = JSON.stringify(form) !== JSON.stringify(planForm(plan));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, Number(value)])) as unknown as LimitPayload;
    if (Object.values(payload).some((value) => !Number.isSafeInteger(value) || value < 0)) {
      setError('Every default must be a whole number of zero or more.');
      return;
    }
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const updated = await adminFetch<PlanLimits>(`/api/plans/${encodeURIComponent(plan.id)}/limits`, { method: 'PATCH', body: JSON.stringify(payload) });
      onUpdated(updated);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 3500);
    } catch (saveError) {
      setError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  }

  const isPro = plan.name.toLowerCase() === 'pro';
  return (
    <article className={`plan-card ${isPro ? 'plan-card--pro' : ''}`}>
      <header className="plan-card__header">
        <span className={`plan-card__icon ${isPro ? 'plan-card__icon--pro' : ''}`}><Icon name={isPro ? 'sparkles' : 'user'} size={22} /></span>
        <div><div><h2>{plan.displayName || plan.name}</h2>{isPro ? <Badge tone="purple">Paid plan</Badge> : <Badge tone="neutral">Base plan</Badge>}</div><p>Default allowance for every {plan.displayName || plan.name} user without an override.</p></div>
        <Badge dot tone={plan.isActive ? 'green' : 'amber'}>{plan.isActive ? 'Active' : 'Inactive'}</Badge>
      </header>
      <form className="plan-limit-form" onSubmit={submit}>
        {error ? <div className="form-alert" role="alert"><Icon name="warning" size={17} /><span>{error}</span></div> : null}
        {saved ? <div className="form-success" role="status"><Icon name="check" size={17} /><span>Defaults saved and applied immediately.</span></div> : null}
        <div className="plan-limit-grid">
          {fields.map((field) => (
            <label className="plan-limit-field" key={field.key}>
              <span className="plan-limit-field__icon"><Icon name={field.icon} size={18} /></span>
              <span className="plan-limit-field__copy"><strong>{field.label}</strong><small>{field.description}</small></span>
              <span className="number-input"><input aria-label={`${plan.displayName} ${field.label}`} inputMode="numeric" min="0" onChange={(event) => { setForm((current) => ({ ...current, [field.key]: event.target.value })); setSaved(false); }} required step="1" type="number" value={form[field.key]} /><small>{field.unit}</small></span>
            </label>
          ))}
        </div>
        <footer className="plan-card__footer">
          <div className="plan-card__current"><Icon name="activity" size={16} /><span>Agent storage: <strong>{formatNumber(Number(form.agentMinutes) * 60)} seconds</strong></span></div>
          <div className="form-actions"><button className="text-button" disabled={!dirty || saving} onClick={() => setForm(planForm(plan))} type="button">Discard</button><Button disabled={!dirty} icon="save" loading={saving} type="submit" variant="primary">Save {plan.displayName || plan.name}</Button></div>
        </footer>
      </form>
    </article>
  );
}

export function LimitsDashboard() {
  const [plans, setPlans] = useState<PlanLimits[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await adminFetch<{ items: PlanLimits[]; total: number }>('/api/plans');
      setPlans(response.items);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const activePlans = useMemo(() => plans.filter((plan) => plan.isActive).length, [plans]);

  return (
    <div className="page-stack">
      <PageHeading eyebrow="Quota controls" title="Plan limits" description="Set the default Agent, TTS, STT, and LLM allowances inherited by each plan." actions={plans.length ? <Badge tone="green" dot>{activePlans} active plans</Badge> : undefined} />
      <div className="callout callout--warning"><span><Icon name="warning" size={20} /></span><div><strong>Changes take effect immediately</strong><p>Existing usage is never reset. If a new limit is lower than a user&apos;s current usage, their remaining quota becomes zero until the next period.</p></div></div>
      {error ? <ErrorState message={error} onRetry={load} /> : null}
      {loading && !plans.length ? <section className="panel"><TableSkeleton rows={6} /></section> : null}
      {plans.length ? <div className={loading ? 'plan-list content-refreshing' : 'plan-list'}>{plans.map((plan) => <PlanCard key={plan.id} onUpdated={(updated) => setPlans((current) => current.map((item) => item.id === updated.id ? updated : item))} plan={plan} />)}</div> : null}
      {!loading && !error && !plans.length ? <section className="panel empty-state"><span className="empty-state__icon"><Icon name="settings" size={25} /></span><h3>No plans found</h3><p>Quota plans will appear here after they are configured.</p></section> : null}
      <div className="callout"><span><Icon name="shield" size={20} /></span><div><strong>User overrides stay in place</strong><p>Changing a plan default does not remove custom limits. Clear an override from the user profile to return that field to inheritance.</p></div></div>
    </div>
  );
}
