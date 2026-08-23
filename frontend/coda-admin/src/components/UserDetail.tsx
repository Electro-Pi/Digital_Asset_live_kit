'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { Area, AreaChart, CartesianGrid, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { adminFetch, apiErrorMessage } from './admin-api';
import type { OverridePayload, QuotaSnapshot, UserDetailData } from './admin-types';
import { Icon } from './ui/Icons';
import { Avatar, BackLink, Badge, Button, DashboardSkeleton, ErrorState, PageHeading, ProgressBar, formatDate, formatNumber, timeAgo } from './ui/Primitives';

const tooltipStyle = { background: '#161827', border: '1px solid rgba(255,255,255,.1)', borderRadius: '12px', color: '#f5f6fb', boxShadow: '0 14px 36px rgba(0,0,0,.35)', fontSize: '12px' };
type OverrideForm = Record<keyof OverridePayload, string>;

const quotaRows: Array<{ key: keyof OverridePayload; label: string; used: keyof QuotaSnapshot; limit: keyof QuotaSnapshot; unit: string }> = [
  { key: 'agentMinutes', label: 'Agent time', used: 'agentUsedMinutes', limit: 'agentLimitMinutes', unit: 'minutes' },
  { key: 'ttsRequests', label: 'TTS', used: 'ttsUsed', limit: 'ttsLimit', unit: 'requests' },
  { key: 'sttRequests', label: 'STT', used: 'sttUsed', limit: 'sttLimit', unit: 'requests' },
  { key: 'llmRequests', label: 'LLM', used: 'llmUsed', limit: 'llmLimit', unit: 'requests' },
];

function formFromOverride(override: OverridePayload): OverrideForm {
  return {
    agentMinutes: override.agentMinutes === null ? '' : String(override.agentMinutes),
    ttsRequests: override.ttsRequests === null ? '' : String(override.ttsRequests),
    sttRequests: override.sttRequests === null ? '' : String(override.sttRequests),
    llmRequests: override.llmRequests === null ? '' : String(override.llmRequests),
  };
}

export function UserDetail({ userId }: { userId: string }) {
  const [data, setData] = useState<UserDetailData | null>(null);
  const [form, setForm] = useState<OverrideForm>({ agentMinutes: '', ttsRequests: '', sttRequests: '', llmRequests: '' });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const detail = await adminFetch<UserDetailData>(`/api/users/${encodeURIComponent(userId)}`);
      setData(detail);
      setForm(formFromOverride(detail.override));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { void load(); }, [load]);

  const chartData = useMemo(() => data?.recentUsage.map((item) => ({
    ...item,
    label: item.label ?? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'Africa/Cairo' }).format(new Date(item.bucket)),
  })) ?? [], [data]);

  async function saveOverrides(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value === '' ? null : Number(value)])) as unknown as OverridePayload;
    if (Object.values(payload).some((value) => value !== null && (!Number.isSafeInteger(value) || value < 0))) {
      setFormError('Every override must be a whole number of zero or more.');
      return;
    }
    setSaving(true);
    setFormError('');
    setSaved(false);
    try {
      await adminFetch<unknown>(`/api/users/${encodeURIComponent(userId)}/limits`, { method: 'PATCH', body: JSON.stringify(payload) });
      setSaved(true);
      await load();
      window.setTimeout(() => setSaved(false), 3500);
    } catch (saveError) {
      setFormError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  }

  const dirty = data ? JSON.stringify(form) !== JSON.stringify(formFromOverride(data.override)) : false;

  if (loading && !data) return <div className="page-stack"><BackLink href="/users">Back to users</BackLink><DashboardSkeleton /></div>;
  if (error && !data) return <div className="page-stack"><BackLink href="/users">Back to users</BackLink><ErrorState message={error} onRetry={load} /></div>;
  if (!data) return null;

  const { user, quota } = data;
  return (
    <div className={`page-stack ${loading ? 'content-refreshing' : ''}`}>
      <BackLink href="/users">Back to users</BackLink>
      <PageHeading title={user.name || user.email.split('@')[0]} description="Account, usage, and persistent per-user limit overrides." actions={<Badge tone={user.plan.toLowerCase() === 'pro' ? 'purple' : 'neutral'}>{user.plan} plan</Badge>} />
      {error ? <ErrorState message={error} onRetry={load} /> : null}

      <section className="panel user-profile-card">
        <div className="user-profile-card__identity"><Avatar email={user.email} image={user.avatarUrl} name={user.name} size="large" /><div><h2>{user.name || 'Unnamed user'}</h2><p>{user.email}</p><div><Badge dot tone={user.confirmed ? 'green' : 'amber'}>{user.confirmed ? 'Email confirmed' : 'Awaiting confirmation'}</Badge><Badge tone="neutral">{user.provider || 'email'}</Badge></div></div></div>
        <dl className="profile-facts">
          <div><dt>Joined</dt><dd>{formatDate(user.createdAt)}</dd></div>
          <div><dt>Last sign-in</dt><dd>{timeAgo(user.lastSignInAt)}</dd></div>
          <div><dt>Recent activity</dt><dd>{timeAgo(user.lastActiveAt)}</dd></div>
          <div><dt>API credits</dt><dd>{formatNumber(data.credits)} <span>read-only</span></dd></div>
        </dl>
      </section>

      <section>
        <div className="section-heading"><div><h2>Current usage</h2><p>{quota.periodStart && quota.periodEnd ? `${formatDate(quota.periodStart)} – ${formatDate(quota.periodEnd)}` : 'Current billing period'}</p></div></div>
        <div className="quota-card-grid">
          {quotaRows.map((row) => {
            const used = Number(quota[row.used] ?? 0);
            const limit = Number(quota[row.limit] ?? 0);
            const remaining = Math.max(0, limit - used);
            return (
              <article className="quota-card" key={row.key}>
                <div className="quota-card__top"><span className={`metric-card__icon metric-card__icon--${row.key === 'agentMinutes' ? 'purple' : row.key === 'ttsRequests' ? 'blue' : row.key === 'sttRequests' ? 'green' : 'amber'}`}><Icon name={row.key === 'agentMinutes' ? 'wave' : row.key === 'ttsRequests' ? 'volume' : row.key === 'sttRequests' ? 'microphone' : 'message'} size={19} /></span><span>{formatNumber(remaining)} remaining</span></div>
                <h3>{row.label}</h3>
                <p><strong>{formatNumber(used)}</strong> / {formatNumber(limit)} {row.unit}</p>
                <ProgressBar max={limit} value={used} />
              </article>
            );
          })}
        </div>
      </section>

      <div className="detail-grid">
        <section className="panel usage-history">
          <header className="panel__header"><div><h2>Recent usage</h2><p>Net usage events over time</p></div></header>
          <div className="usage-history__chart">
            {chartData.length ? <ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData} margin={{ top: 12, right: 8, bottom: 0, left: -18 }}><defs><linearGradient id="userUsageFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#8b7cff" stopOpacity=".32" /><stop offset="1" stopColor="#8b7cff" stopOpacity="0" /></linearGradient></defs><CartesianGrid stroke="rgba(255,255,255,.055)" vertical={false} /><XAxis axisLine={false} dataKey="label" tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} minTickGap={20} /><YAxis axisLine={false} tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} /><Tooltip contentStyle={tooltipStyle} /><Legend iconType="circle" iconSize={7} wrapperStyle={{ color: '#a8abbd', fontSize: 11, paddingTop: 8 }} /><Area dataKey="agentMinutes" fill="url(#userUsageFill)" name="Agent min" stroke="#8b7cff" strokeWidth={2} type="monotone" /><Line dataKey="tts" dot={false} name="TTS" stroke="#5aa9ff" strokeWidth={1.8} type="monotone" /><Line dataKey="stt" dot={false} name="STT" stroke="#31d2b2" strokeWidth={1.8} type="monotone" /><Line dataKey="llm" dot={false} name="LLM" stroke="#ffbd66" strokeWidth={1.8} type="monotone" /></AreaChart></ResponsiveContainer> : <div className="chart-empty"><Icon name="activity" size={24} /><strong>No recent usage</strong><span>Usage events will appear here.</span></div>}
          </div>
        </section>

        <section className="panel override-panel">
          <header className="panel__header"><div><h2>User overrides</h2><p>Blank fields inherit the {user.plan} plan defaults</p></div><Badge tone={Object.values(data.override).some((value) => value !== null) ? 'amber' : 'green'}>{Object.values(data.override).some((value) => value !== null) ? 'Custom limits' : 'Inheriting plan'}</Badge></header>
          <form className="limits-form" onSubmit={saveOverrides}>
            {formError ? <div className="form-alert" role="alert"><Icon name="warning" size={17} /><span>{formError}</span></div> : null}
            {saved ? <div className="form-success" role="status"><Icon name="check" size={17} /><span>Overrides saved and applied immediately.</span></div> : null}
            <div className="override-fields">
              {quotaRows.map((row) => {
                const used = Number(quota[row.used] ?? 0);
                const entered = form[row.key] === '' ? null : Number(form[row.key]);
                const belowUsage = entered !== null && Number.isFinite(entered) && entered < used;
                return <label className="field limit-field" key={row.key}><span>{row.label} <small>{row.unit}</small></span><input inputMode="numeric" min="0" onChange={(event) => { setForm((current) => ({ ...current, [row.key]: event.target.value })); setSaved(false); }} placeholder={`Inherit (${formatNumber(Number(quota[row.limit]))})`} step="1" type="number" value={form[row.key]} />{belowUsage ? <small className="field-warning">Below current usage; remaining becomes zero.</small> : <small>Effective: {form[row.key] === '' ? formatNumber(Number(quota[row.limit])) : formatNumber(Number(form[row.key]))}</small>}</label>;
              })}
            </div>
            <div className="form-actions"><button className="text-button" disabled={!dirty || saving} onClick={() => setForm(formFromOverride(data.override))} type="button">Discard changes</button><Button disabled={!dirty} icon="save" loading={saving} type="submit" variant="primary">Save overrides</Button></div>
          </form>
        </section>
      </div>
    </div>
  );
}
