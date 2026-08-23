'use client';

import { useCallback, useEffect, useState } from 'react';
import { adminFetch, apiErrorMessage, queryString } from './admin-api';
import type { AuditEntry, PaginatedData } from './admin-types';
import { Icon } from './ui/Icons';
import { Badge, EmptyState, ErrorState, PageHeading, Pagination, TableSkeleton, formatDate, prettify } from './ui/Primitives';

function auditTone(action: string): 'green' | 'purple' | 'blue' | 'amber' | 'red' {
  const normalized = action.toLowerCase();
  if (normalized.includes('login') || normalized.includes('create')) return 'green';
  if (normalized.includes('plan') || normalized.includes('limit')) return 'purple';
  if (normalized.includes('booking')) return 'blue';
  if (normalized.includes('logout')) return 'amber';
  if (normalized.includes('revoke') || normalized.includes('delete')) return 'red';
  return 'blue';
}

function JsonSnapshot({ value, empty }: { value: unknown; empty: string }) {
  if (value === undefined || value === null) return <span className="audit-empty">{empty}</span>;
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

export function AuditDashboard() {
  const [result, setResult] = useState<PaginatedData<AuditEntry> | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [action, setAction] = useState('all');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(searchInput.trim()); setPage(1); }, 350);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const query = queryString({ search, action: action === 'all' ? undefined : action, page, pageSize: 25 });
      setResult(await adminFetch<PaginatedData<AuditEntry>>(`/api/audit${query}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [action, page, search]);

  useEffect(() => { void load(); }, [load]);

  return (
    <div className="page-stack">
      <PageHeading eyebrow="Security & accountability" title="Audit log" description="An immutable history of every sensitive limit and booking change." actions={<Badge tone="green" dot>Recording enabled</Badge>} />
      <div className="callout"><span><Icon name="shield" size={20} /></span><div><strong>Append-only change record</strong><p>Limit and booking mutations are recorded atomically and cannot be edited or deleted here. Times are shown in Cairo time.</p></div></div>
      <section className="panel table-panel">
        <div className="table-toolbar"><label className="search-field"><span className="sr-only">Search audit log</span><Icon name="search" size={18} /><input onChange={(event) => setSearchInput(event.target.value)} placeholder="Search actor or target…" type="search" value={searchInput} />{searchInput ? <button aria-label="Clear search" onClick={() => setSearchInput('')} type="button"><Icon name="close" size={15} /></button> : null}</label><div className="filter-group"><label className="select-field"><span className="sr-only">Filter audit action</span><select onChange={(event) => { setAction(event.target.value); setPage(1); }} value={action}><option value="all">All actions</option><option value="plan_limits.updated">Plan limits</option><option value="user_limits.updated">User overrides</option><option value="booking_status.updated">Booking status</option></select><Icon name="chevron-down" size={15} /></label></div></div>
        {error && !result ? <ErrorState message={error} onRetry={load} /> : null}
        {loading && !result ? <TableSkeleton rows={10} /> : null}
        {result ? <div className={loading ? 'content-refreshing' : ''}>{result.items.length ? <><div className="audit-list">{result.items.map((entry) => <article className="audit-entry" key={entry.id}><div className={`audit-entry__icon audit-entry__icon--${auditTone(entry.action)}`}><Icon name={entry.action.toLowerCase().includes('limit') ? 'settings' : entry.action.toLowerCase().includes('booking') ? 'calendar' : entry.action.toLowerCase().includes('login') ? 'shield' : 'file'} size={18} /></div><div className="audit-entry__main"><div className="audit-entry__headline"><p><strong>{entry.actorEmail || 'System'}</strong> <span>{prettify(entry.action)}</span></p><time dateTime={entry.createdAt}>{formatDate(entry.createdAt, true)}</time></div><div className="audit-entry__meta"><Badge tone="neutral">{entry.actorRole || 'system'}</Badge><span>Target: <strong>{prettify(entry.targetType)}</strong>{entry.targetId ? ` · ${entry.targetId}` : ''}</span>{entry.ipAddress ? <span>IP: {entry.ipAddress}</span> : null}</div>{entry.before !== undefined || entry.after !== undefined ? <details className="audit-diff"><summary><Icon name="chevron-right" size={15} /> View recorded change</summary><div className="audit-diff__grid"><div><h3>Before</h3><JsonSnapshot empty="No previous value" value={entry.before} /></div><div><h3>After</h3><JsonSnapshot empty="No resulting value" value={entry.after} /></div></div></details> : null}</div></article>)}</div><Pagination onChange={setPage} page={result.page} pageSize={result.pageSize} total={result.total} /></> : <EmptyState icon="file" title="No audit events found" description={search || action !== 'all' ? 'Try a broader search or a different action filter.' : 'Sensitive administrator activity will be recorded here.'} />}</div> : null}
      </section>
    </div>
  );
}
