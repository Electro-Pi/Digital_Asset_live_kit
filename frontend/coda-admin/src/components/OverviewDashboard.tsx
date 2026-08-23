'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { adminFetch, apiErrorMessage } from './admin-api';
import type { DateRange, OverviewData } from './admin-types';
import { Icon, type IconName } from './ui/Icons';
import {
  Avatar,
  Badge,
  DashboardSkeleton,
  EmptyState,
  ErrorState,
  PageHeading,
  formatCurrency,
  formatNumber,
} from './ui/Primitives';

const chartColors = ['#8b7cff', '#31d2b2', '#ffbd66', '#ff769c', '#5aa9ff'];
const tooltipStyle = {
  background: '#161827',
  border: '1px solid rgba(255,255,255,.1)',
  borderRadius: '12px',
  color: '#f5f6fb',
  boxShadow: '0 14px 36px rgba(0,0,0,.35)',
  fontSize: '12px',
};

function bucketLabel(bucket: string, label?: string) {
  if (label) return label;
  const date = new Date(bucket);
  if (Number.isNaN(date.valueOf())) return bucket;
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'Africa/Cairo' }).format(date);
}

function MetricCard({
  label,
  value,
  comparison,
  icon,
  tone,
  subtitle,
}: {
  label: string;
  value: string;
  comparison?: number;
  icon: IconName;
  tone: string;
  subtitle: string;
}) {
  const hasComparison = typeof comparison === 'number' && Number.isFinite(comparison);
  const positive = (comparison ?? 0) >= 0;

  return (
    <article className="metric-card">
      <div className="metric-card__top">
        <span className={`metric-card__icon metric-card__icon--${tone}`}><Icon name={icon} size={20} /></span>
        {hasComparison ? (
          <span className={`metric-delta ${positive ? 'metric-delta--up' : 'metric-delta--down'}`}>
            <Icon name={positive ? 'arrow-up' : 'arrow-down'} size={13} />
            {Math.abs(comparison ?? 0).toFixed(1)}%
          </span>
        ) : null}
      </div>
      <p>{label}</p>
      <strong>{value}</strong>
      <small>{subtitle}</small>
    </article>
  );
}

function ChartPanel({
  title,
  description,
  children,
  legend,
  className = '',
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  legend?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel chart-panel ${className}`}>
      <header className="panel__header">
        <div><h2>{title}</h2><p>{description}</p></div>
        {legend}
      </header>
      <div className="chart-panel__body">{children}</div>
    </section>
  );
}

function DonutChart({ data }: { data: Array<{ name: string; value: number }> }) {
  const total = data.reduce((sum, item) => sum + item.value, 0);
  if (!data.length || total === 0) {
    return <EmptyState title="No plan data yet" description="Plan distribution will appear as users join." />;
  }

  return (
    <div className="donut-layout">
      <div className="donut-chart">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="value" innerRadius={55} outerRadius={76} paddingAngle={4} stroke="none">
              {data.map((item, index) => <Cell fill={chartColors[index % chartColors.length]} key={item.name} />)}
            </Pie>
            <Tooltip contentStyle={tooltipStyle} />
          </PieChart>
        </ResponsiveContainer>
        <div className="donut-chart__center"><strong>{formatNumber(total)}</strong><span>users</span></div>
      </div>
      <div className="donut-legend">
        {data.map((item, index) => (
          <div key={item.name}>
            <span style={{ background: chartColors[index % chartColors.length] }} />
            <p><strong>{item.name}</strong><small>{total ? Math.round((item.value / total) * 100) : 0}% · {formatNumber(item.value)}</small></p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function OverviewDashboard() {
  const [range, setRange] = useState<DateRange>('month');
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await adminFetch<OverviewData>(`/api/overview?range=${range}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => { void load(); }, [load]);

  const growth = useMemo(
    () => data?.growth.map((item) => ({ ...item, label: bucketLabel(item.bucket, item.label) })) ?? [],
    [data],
  );
  const agentUsage = useMemo(
    () => data?.agentUsage.map((item) => ({ ...item, label: bucketLabel(item.bucket, item.label) })) ?? [],
    [data],
  );
  const requestUsage = useMemo(
    () => data?.requestUsage.map((item) => ({ ...item, label: bucketLabel(item.bucket, item.label) })) ?? [],
    [data],
  );
  const bookingTrend = useMemo(
    () => (data?.bookingTrend ?? []).map((item) => ({ ...item, label: bucketLabel(item.bucket, item.label) })),
    [data],
  );

  const rangeCopy = range === 'week' ? 'last 7 days' : range === 'month' ? 'last 30 days' : 'last 12 months';

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Operations overview"
        title="Your business at a glance"
        description="Track customer growth, product usage, and your sales pipeline from one place."
        actions={(
          <div className="segmented-control" aria-label="Analytics range">
            {(['week', 'month', 'year'] as DateRange[]).map((option) => (
              <button
                aria-pressed={range === option}
                className={range === option ? 'is-active' : ''}
                key={option}
                onClick={() => setRange(option)}
                type="button"
              >
                {option === 'week' ? '7 days' : option === 'month' ? '30 days' : '12 months'}
              </button>
            ))}
          </div>
        )}
      />

      {error && !data ? <ErrorState message={error} onRetry={load} /> : null}
      {loading && !data ? <DashboardSkeleton /> : null}

      {data ? (
        <div className={loading ? 'content-refreshing' : ''}>
          {error ? <ErrorState message={error} onRetry={load} /> : null}
          <section className="summary-grid" aria-label="Key metrics">
            <MetricCard label="Total users" value={formatNumber(data.summary.totalUsers)} comparison={data.summary.comparisons?.totalUsers} icon="users" tone="purple" subtitle="All registered accounts" />
            <MetricCard label="New users" value={formatNumber(data.summary.newUsers)} comparison={data.summary.comparisons?.newUsers} icon="sparkles" tone="blue" subtitle={`Joined in the ${rangeCopy}`} />
            <MetricCard label="Active users" value={formatNumber(data.summary.activeUsers)} comparison={data.summary.comparisons?.activeUsers} icon="activity" tone="green" subtitle={`Used Coda in the ${rangeCopy}`} />
            <MetricCard label="Pro customers" value={formatNumber(data.summary.proUsers)} comparison={data.summary.comparisons?.proUsers} icon="shield" tone="amber" subtitle={`${formatNumber(data.summary.proAdoptionRate ?? (data.summary.totalUsers ? (data.summary.proUsers / data.summary.totalUsers) * 100 : 0))}% adoption rate`} />
            <MetricCard label="Paid revenue" value={formatCurrency(data.summary.revenue)} comparison={data.summary.comparisons?.revenue} icon="money" tone="green" subtitle={`Fulfilled in the ${rangeCopy}`} />
            <MetricCard label="Demo bookings" value={formatNumber(data.summary.bookings)} comparison={data.summary.comparisons?.bookings} icon="calendar" tone="pink" subtitle={`Created in the ${rangeCopy}`} />
          </section>

          <div className="chart-grid chart-grid--primary">
            <ChartPanel title="User growth" description={`New accounts across the ${rangeCopy}`} legend={<Badge tone="purple" dot>New users</Badge>}>
              {growth.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={growth} margin={{ top: 12, right: 8, bottom: 0, left: -24 }}>
                    <defs>
                      <linearGradient id="growthFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#8b7cff" stopOpacity=".38" /><stop offset="1" stopColor="#8b7cff" stopOpacity="0" /></linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(255,255,255,.055)" vertical={false} />
                    <XAxis axisLine={false} dataKey="label" tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} minTickGap={24} />
                    <YAxis allowDecimals={false} axisLine={false} tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: 'rgba(139,124,255,.35)' }} />
                    <Area dataKey="users" fill="url(#growthFill)" name="New users" stroke="#9c8fff" strokeWidth={2.5} type="monotone" />
                  </AreaChart>
                </ResponsiveContainer>
              ) : <EmptyState title="No signups in this period" description="New accounts will appear here as customers register." />}
            </ChartPanel>

            <ChartPanel title="Agent usage" description="Conversation time consumed" legend={<Badge tone="green" dot>Minutes</Badge>}>
              {agentUsage.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={agentUsage} margin={{ top: 12, right: 8, bottom: 0, left: -18 }}>
                    <CartesianGrid stroke="rgba(255,255,255,.055)" vertical={false} />
                    <XAxis axisLine={false} dataKey="label" tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} minTickGap={24} />
                    <YAxis axisLine={false} tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: 'rgba(49,210,178,.25)' }} />
                    <Line dataKey="minutes" dot={false} name="Agent minutes" stroke="#31d2b2" strokeWidth={2.5} type="monotone" />
                  </LineChart>
                </ResponsiveContainer>
              ) : <EmptyState title="No agent usage yet" description="Voice-agent minutes will be charted here." />}
            </ChartPanel>
          </div>

          <ChartPanel className="chart-panel--wide" title="Request usage" description="TTS, STT, and LLM requests are shown separately from agent minutes">
            {requestUsage.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={requestUsage} margin={{ top: 12, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid stroke="rgba(255,255,255,.055)" vertical={false} />
                  <XAxis axisLine={false} dataKey="label" tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} minTickGap={24} />
                  <YAxis axisLine={false} tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: 'rgba(255,255,255,.13)' }} />
                  <Legend iconType="circle" iconSize={7} wrapperStyle={{ color: '#a8abbd', fontSize: 12, paddingTop: 10 }} />
                  <Line dataKey="tts" dot={false} name="TTS requests" stroke="#8b7cff" strokeWidth={2.2} type="monotone" />
                  <Line dataKey="stt" dot={false} name="STT requests" stroke="#31d2b2" strokeWidth={2.2} type="monotone" />
                  <Line dataKey="llm" dot={false} name="LLM requests" stroke="#ffbd66" strokeWidth={2.2} type="monotone" />
                </LineChart>
              </ResponsiveContainer>
            ) : <EmptyState title="No request usage yet" description="Request volume will appear here when customers use the APIs." />}
          </ChartPanel>

          <div className="insight-grid">
            <ChartPanel title="Plan distribution" description="Free and paid customer mix">
              <DonutChart data={data.planDistribution} />
            </ChartPanel>
            <section className="panel quota-health">
              <header className="panel__header"><div><h2>Quota health</h2><p>Users approaching their current limits</p></div></header>
              {Object.values(data.quotaHealth).some(Boolean) ? (
                <div className="quota-health__list">
                  {[
                    { key: 'healthy' as const, label: 'Healthy', description: 'Under 80% used', color: 'green' },
                    { key: 'warning' as const, label: 'Near limit', description: '80–99% used', color: 'amber' },
                    { key: 'exhausted' as const, label: 'Exhausted', description: 'No quota remaining', color: 'red' },
                  ].map((item) => {
                    const total = data.quotaHealth.healthy + data.quotaHealth.warning + data.quotaHealth.exhausted;
                    const count = data.quotaHealth[item.key];
                    return (
                      <div className="quota-health__item" key={item.key}>
                        <span className={`quota-health__dot quota-health__dot--${item.color}`} />
                        <p><strong>{item.label}</strong><small>{item.description}</small></p>
                        <strong>{formatNumber(count)}</strong>
                        <div className="quota-health__track"><span className={`quota-health__fill quota-health__fill--${item.color}`} style={{ width: `${total ? (count / total) * 100 : 0}%` }} /></div>
                      </div>
                    );
                  })}
                </div>
              ) : <EmptyState title="No quota data yet" description="Quota health appears once customers start using Coda." />}
            </section>
          </div>

          <section className="panel top-users-panel">
            <header className="panel__header"><div><h2>Top users</h2><p>Highest product consumption in the selected period</p></div></header>
            {data.topUsers.length ? (
              <div className="table-scroll"><table className="data-table"><thead><tr><th>User</th><th>Plan</th><th>Agent time</th><th>API requests</th></tr></thead><tbody>
                {data.topUsers.map((user, index) => (
                  <tr key={user.id}>
                    <td><div className="user-cell"><span className="rank-number">{index + 1}</span><Avatar email={user.email} name={user.name} size="small" /><span><strong>{user.name || user.email.split('@')[0]}</strong><small>{user.email}</small></span></div></td>
                    <td><Badge tone={user.plan.toLowerCase() === 'pro' ? 'purple' : 'neutral'}>{user.plan}</Badge></td>
                    <td><strong>{formatNumber(user.agentMinutes)}</strong> min</td>
                    <td>{formatNumber(user.requests)}</td>
                  </tr>
                ))}
              </tbody></table></div>
            ) : <EmptyState title="No active users yet" description="Your most active customers will be ranked here." />}
          </section>

          <div className="pipeline-grid">
            <ChartPanel title="Booking trend" description={`New demo requests in the ${rangeCopy}`}>
              {bookingTrend.length ? (
                <ResponsiveContainer width="100%" height="100%"><AreaChart data={bookingTrend} margin={{ top: 12, right: 8, bottom: 0, left: -26 }}><defs><linearGradient id="bookingFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ff769c" stopOpacity=".3" /><stop offset="1" stopColor="#ff769c" stopOpacity="0" /></linearGradient></defs><CartesianGrid stroke="rgba(255,255,255,.055)" vertical={false} /><XAxis axisLine={false} dataKey="label" tick={{ fill: '#777b91', fontSize: 10 }} tickLine={false} minTickGap={22} /><YAxis allowDecimals={false} axisLine={false} tick={{ fill: '#777b91', fontSize: 10 }} tickLine={false} /><Tooltip contentStyle={tooltipStyle} /><Area dataKey="bookings" fill="url(#bookingFill)" name="Bookings" stroke="#ff769c" strokeWidth={2.2} type="monotone" /></AreaChart></ResponsiveContainer>
              ) : <EmptyState title="No bookings in this period" description="New demo requests will be charted here." />}
            </ChartPanel>
            <ChartPanel title="Booking funnel" description="Current lead status distribution">
              {data.bookingStatus.length ? (
                <ResponsiveContainer width="100%" height="100%"><BarChart data={data.bookingStatus} layout="vertical" margin={{ top: 0, right: 12, bottom: 0, left: 10 }}><CartesianGrid horizontal={false} stroke="rgba(255,255,255,.055)" /><XAxis axisLine={false} tick={{ fill: '#777b91', fontSize: 11 }} tickLine={false} type="number" /><YAxis axisLine={false} dataKey="name" tick={{ fill: '#a8abbd', fontSize: 11 }} tickLine={false} type="category" width={70} /><Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,.03)' }} /><Bar dataKey="value" fill="#8b7cff" name="Bookings" radius={[0, 5, 5, 0]} /></BarChart></ResponsiveContainer>
              ) : <EmptyState title="No bookings yet" description="Lead statuses will form a funnel here." />}
            </ChartPanel>
            <ChartPanel title="Booking sources" description="Where demo interest originates">
              <DonutChart data={data.bookingSources} />
            </ChartPanel>
            <ChartPanel title="Payment funnel" description="Paid-plan transaction outcomes">
              {data.paymentStatus.length ? (
                <div className="status-list">{data.paymentStatus.map((item, index) => { const total = data.paymentStatus.reduce((sum, row) => sum + row.value, 0); return <div key={item.name}><span style={{ background: chartColors[index % chartColors.length] }} /><p><strong>{item.name}</strong><small>{total ? Math.round((item.value / total) * 100) : 0}% of payments</small></p><b>{formatNumber(item.value)}</b></div>; })}</div>
              ) : <EmptyState title="No payment activity" description="Fulfilled and failed payment counts will appear here." />}
            </ChartPanel>
          </div>
        </div>
      ) : null}
    </div>
  );
}
