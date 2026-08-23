'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { adminFetch, apiErrorMessage, queryString } from './admin-api';
import type { BookingStatus, DemoBooking, PaginatedData } from './admin-types';
import { Icon } from './ui/Icons';
import { Badge, Button, EmptyState, ErrorState, PageHeading, Pagination, TableSkeleton, formatDate, prettify, timeAgo } from './ui/Primitives';

const statuses: BookingStatus[] = ['new', 'contacted', 'scheduled', 'done', 'canceled'];

function statusTone(status: BookingStatus): 'blue' | 'amber' | 'purple' | 'green' | 'red' {
  return status === 'new' ? 'blue' : status === 'contacted' ? 'amber' : status === 'scheduled' ? 'purple' : status === 'done' ? 'green' : 'red';
}

export function BookingsDashboard() {
  const [result, setResult] = useState<PaginatedData<DemoBooking> | null>(null);
  const [selected, setSelected] = useState<DemoBooking | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [source, setSource] = useState('all');
  const [sort, setSort] = useState('newest');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [savingId, setSavingId] = useState('');

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(searchInput.trim()); setPage(1); }, 350);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const query = queryString({ search, status: status === 'all' ? undefined : status, source: source === 'all' ? undefined : source, sort: sort === 'name' || sort === 'status' ? sort : 'createdAt', direction: sort === 'oldest' || sort === 'name' ? 'asc' : 'desc', page, pageSize: 25 });
      setResult(await adminFetch<PaginatedData<DemoBooking>>(`/api/bookings${query}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [page, search, sort, source, status]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    document.body.style.overflow = selected ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [selected]);

  const sourceOptions = useMemo(() => Array.from(new Set((result?.items ?? []).map((item) => item.source).filter((item): item is string => Boolean(item)))), [result]);

  async function updateStatus(booking: DemoBooking, nextStatus: BookingStatus) {
    if (nextStatus === booking.status) return;
    setSavingId(booking.id);
    setActionError('');
    try {
      const updated = await adminFetch<Partial<DemoBooking>>(`/api/bookings/${encodeURIComponent(booking.id)}/status`, { method: 'PATCH', body: JSON.stringify({ status: nextStatus }) });
      const merged = { ...booking, ...updated };
      setResult((current) => current ? { ...current, items: current.items.map((item) => item.id === booking.id ? { ...item, ...updated } : item) } : current);
      setSelected((current) => current?.id === booking.id ? { ...current, ...merged } : current);
    } catch (updateError) {
      setActionError(apiErrorMessage(updateError));
    } finally {
      setSavingId('');
    }
  }

  return (
    <div className="page-stack">
      <PageHeading eyebrow="Sales pipeline" title="Demo bookings" description="Review every demo request and move qualified leads through your follow-up workflow." actions={result ? <Badge tone="purple">{result.total} total bookings</Badge> : undefined} />
      <section className="panel table-panel">
        <div className="table-toolbar">
          <label className="search-field"><span className="sr-only">Search bookings</span><Icon name="search" size={18} /><input onChange={(event) => setSearchInput(event.target.value)} placeholder="Search name, company, or email…" type="search" value={searchInput} />{searchInput ? <button aria-label="Clear search" onClick={() => setSearchInput('')} type="button"><Icon name="close" size={15} /></button> : null}</label>
          <div className="filter-group"><label className="select-field"><span className="sr-only">Filter booking status</span><select onChange={(event) => { setStatus(event.target.value); setPage(1); }} value={status}><option value="all">All statuses</option>{statuses.map((item) => <option key={item} value={item}>{prettify(item)}</option>)}</select><Icon name="chevron-down" size={15} /></label><label className="select-field"><span className="sr-only">Filter booking source</span><select onChange={(event) => { setSource(event.target.value); setPage(1); }} value={source}><option value="all">All sources</option>{sourceOptions.map((item) => <option key={item} value={item}>{prettify(item)}</option>)}</select><Icon name="chevron-down" size={15} /></label><label className="select-field"><span className="sr-only">Sort bookings</span><select onChange={(event) => { setSort(event.target.value); setPage(1); }} value={sort}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="name">Contact name</option><option value="status">Status</option></select><Icon name="chevron-down" size={15} /></label></div>
        </div>
        {actionError ? <div className="inline-error" role="alert"><Icon name="warning" size={16} /> {actionError}<button onClick={() => setActionError('')}>Dismiss</button></div> : null}
        {error && !result ? <ErrorState message={error} onRetry={load} /> : null}
        {loading && !result ? <TableSkeleton rows={9} /> : null}
        {result ? <div className={loading ? 'content-refreshing' : ''}>{result.items.length ? <>
          <div className="table-scroll bookings-table-wrap"><table className="data-table bookings-table"><thead><tr><th>Contact</th><th>Company</th><th>Source</th><th>Submitted</th><th>Status</th><th><span className="sr-only">Details</span></th></tr></thead><tbody>{result.items.map((booking) => <tr key={booking.id}><td><button className="contact-cell" onClick={() => setSelected(booking)} type="button"><span className="contact-cell__avatar">{booking.name.charAt(0).toUpperCase()}</span><span><strong>{booking.name}</strong><small>{booking.email}</small></span></button></td><td><div className="stacked-value"><span>{booking.company || '—'}</span><small>{booking.jobTitle || 'No role provided'}</small></div></td><td><Badge tone="neutral">{prettify(booking.source || 'Direct')}</Badge></td><td><div className="stacked-value"><span>{formatDate(booking.createdAt)}</span><small>{timeAgo(booking.createdAt)}</small></div></td><td><label className={`status-select status-select--${statusTone(booking.status)}`}><span className="sr-only">Update {booking.name}&apos;s status</span>{savingId === booking.id ? <span className="button__spinner" /> : <span className="badge__dot" />}<select disabled={savingId === booking.id} onChange={(event) => void updateStatus(booking, event.target.value as BookingStatus)} value={booking.status}>{statuses.map((item) => <option key={item} value={item}>{prettify(item)}</option>)}</select><Icon name="chevron-down" size={14} /></label></td><td><button aria-label={`View booking from ${booking.name}`} className="row-link" onClick={() => setSelected(booking)} type="button"><Icon name="chevron-right" size={17} /></button></td></tr>)}</tbody></table></div>
          <div className="mobile-record-list">{result.items.map((booking) => <article className="mobile-booking-card" key={booking.id}><button className="mobile-booking-card__open" onClick={() => setSelected(booking)} type="button"><span className="contact-cell__avatar">{booking.name.charAt(0).toUpperCase()}</span><span><strong>{booking.name}</strong><small>{booking.company || booking.email}</small></span><Icon name="chevron-right" size={17} /></button><div><Badge tone={statusTone(booking.status)} dot>{prettify(booking.status)}</Badge><span>{timeAgo(booking.createdAt)}</span></div></article>)}</div>
          <Pagination onChange={setPage} page={result.page} pageSize={result.pageSize} total={result.total} />
        </> : <EmptyState icon="calendar" title="No bookings found" description={search || status !== 'all' || source !== 'all' ? 'Try clearing your filters to see more requests.' : 'New demo requests will appear here.'} />}</div> : null}
      </section>

      {selected ? <div className="drawer-layer" role="presentation"><button aria-label="Close booking details" className="drawer-scrim" onClick={() => setSelected(null)} type="button" /><aside aria-label={`Booking from ${selected.name}`} className="detail-drawer"><header className="detail-drawer__header"><div><p>Demo request</p><h2>{selected.name}</h2></div><button aria-label="Close details" className="icon-button" onClick={() => setSelected(null)} type="button"><Icon name="close" /></button></header><div className="detail-drawer__body"><div className="booking-identity"><span className="contact-cell__avatar contact-cell__avatar--large">{selected.name.charAt(0).toUpperCase()}</span><div><h3>{selected.name}</h3><p>{selected.jobTitle ? `${selected.jobTitle}${selected.company ? ` at ${selected.company}` : ''}` : selected.company || 'Company not provided'}</p></div></div><div className="drawer-status"><label><span>Pipeline status</span><span className={`status-select status-select--${statusTone(selected.status)}`}>{savingId === selected.id ? <span className="button__spinner" /> : <span className="badge__dot" />}<select disabled={savingId === selected.id} onChange={(event) => void updateStatus(selected, event.target.value as BookingStatus)} value={selected.status}>{statuses.map((item) => <option key={item} value={item}>{prettify(item)}</option>)}</select><Icon name="chevron-down" size={14} /></span></label></div><dl className="booking-details"><div><dt><Icon name="mail" size={16} /> Email</dt><dd><a href={`mailto:${selected.email}`}>{selected.email}</a></dd></div><div><dt><Icon name="phone" size={16} /> Phone</dt><dd>{selected.phone ? <a href={`tel:${selected.phone}`}>{selected.phone}</a> : 'Not provided'}</dd></div><div><dt><Icon name="database" size={16} /> Source</dt><dd>{prettify(selected.source || 'Direct')}</dd></div><div><dt><Icon name="clock" size={16} /> Submitted</dt><dd>{formatDate(selected.createdAt, true)}</dd></div>{selected.preferredDate || selected.preferredTime ? <div><dt><Icon name="calendar" size={16} /> Preferred demo</dt><dd>{[selected.preferredDate ? formatDate(`${selected.preferredDate}T12:00:00`) : null, selected.preferredTime].filter(Boolean).join(' · ')}</dd></div> : null}{selected.userId ? <div><dt><Icon name="user" size={16} /> Coda account</dt><dd><a href={`/users/${encodeURIComponent(selected.userId)}`}>View registered user</a></dd></div> : null}</dl><div className="booking-message"><h3>Message</h3>{selected.message ? <p>{selected.message}</p> : <p className="muted-copy">No additional message was provided.</p>}</div></div><footer className="detail-drawer__footer"><a className="button button--primary" href={`mailto:${selected.email}`}><Icon name="mail" size={17} /> Email contact</a>{selected.phone ? <a className="button button--secondary" href={`tel:${selected.phone}`}><Icon name="phone" size={17} /> Call</a> : null}</footer></aside></div> : null}
    </div>
  );
}
