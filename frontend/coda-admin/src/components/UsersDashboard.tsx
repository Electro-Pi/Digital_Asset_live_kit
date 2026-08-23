'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { adminFetch, apiErrorMessage, queryString } from './admin-api';
import type { AdminUser, PaginatedData } from './admin-types';
import { Icon } from './ui/Icons';
import {
  Avatar,
  Badge,
  EmptyState,
  ErrorState,
  PageHeading,
  Pagination,
  ProgressBar,
  TableSkeleton,
  formatNumber,
  timeAgo,
} from './ui/Primitives';

type UserSort = 'createdAt' | 'lastSignInAt' | 'email';

export function UsersDashboard() {
  const [result, setResult] = useState<PaginatedData<AdminUser> | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [plan, setPlan] = useState('all');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState<UserSort>('createdAt');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const query = queryString({
        search,
        plan: plan === 'all' ? undefined : plan,
        status: status === 'all' ? undefined : status,
        sort,
        direction: order,
        page,
        pageSize: 25,
      });
      setResult(await adminFetch<PaginatedData<AdminUser>>(`/api/users${query}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [order, page, plan, search, sort, status]);

  useEffect(() => { void load(); }, [load]);

  function changeSort(nextSort: UserSort) {
    if (sort === nextSort) setOrder((current) => current === 'asc' ? 'desc' : 'asc');
    else { setSort(nextSort); setOrder('desc'); }
    setPage(1);
  }

  const hasFilters = Boolean(search || plan !== 'all' || status !== 'all');

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Customer accounts"
        title="Users"
        description="Review every registered account, its plan, recent activity, and current quota usage."
        actions={result ? <Badge tone="purple">{formatNumber(result.total)} total users</Badge> : undefined}
      />

      <section className="panel table-panel">
        <div className="table-toolbar">
          <label className="search-field">
            <span className="sr-only">Search users</span>
            <Icon name="search" size={18} />
            <input
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search name or email…"
              type="search"
              value={searchInput}
            />
            {searchInput ? <button aria-label="Clear search" onClick={() => setSearchInput('')} type="button"><Icon name="close" size={15} /></button> : null}
          </label>
          <div className="filter-group">
            <label className="select-field"><span className="sr-only">Filter by plan</span><select onChange={(event) => { setPlan(event.target.value); setPage(1); }} value={plan}><option value="all">All plans</option><option value="free">Free</option><option value="pro">Pro</option></select><Icon name="chevron-down" size={15} /></label>
            <label className="select-field"><span className="sr-only">Filter by status</span><select onChange={(event) => { setStatus(event.target.value); setPage(1); }} value={status}><option value="all">All statuses</option><option value="confirmed">Confirmed</option><option value="unconfirmed">Unconfirmed</option></select><Icon name="chevron-down" size={15} /></label>
            <label className="select-field"><span className="sr-only">Sort users</span><select onChange={(event) => { setSort(event.target.value as UserSort); setOrder('desc'); setPage(1); }} value={sort}><option value="createdAt">Newest first</option><option value="lastSignInAt">Recent sign-in</option><option value="email">Email</option></select><Icon name="chevron-down" size={15} /></label>
          </div>
        </div>

        {error && !result ? <ErrorState message={error} onRetry={load} /> : null}
        {loading && !result ? <TableSkeleton rows={9} /> : null}
        {error && result ? <div className="inline-error"><Icon name="warning" size={16} /> {error}<button onClick={load}>Retry</button></div> : null}

        {result ? (
          <div className={loading ? 'content-refreshing' : ''}>
            {result.items.length ? (
              <>
                <div className="table-scroll users-table-wrap">
                  <table className="data-table users-table">
                    <thead><tr>
                      <th><button className="sort-button" onClick={() => changeSort('email')} type="button">User {sort === 'email' ? <Icon name={order === 'asc' ? 'arrow-up' : 'arrow-down'} size={13} /> : null}</button></th>
                      <th>Plan</th>
                      <th>Account</th>
                      <th>Recent activity</th>
                      <th>Agent quota</th>
                      <th>Request usage</th>
                      <th>Credits</th>
                      <th><span className="sr-only">Open user</span></th>
                    </tr></thead>
                    <tbody>{result.items.map((user) => {
                      const requestsUsed = user.ttsUsed + user.sttUsed + user.llmUsed;
                      const requestsLimit = user.ttsLimit + user.sttLimit + user.llmLimit;
                      return (
                        <tr key={user.id}>
                          <td><Link className="user-cell user-cell--link" href={`/users/${encodeURIComponent(user.id)}`}><Avatar email={user.email} image={user.avatarUrl} name={user.name} /><span><strong>{user.name || user.email.split('@')[0]}</strong><small>{user.email}</small></span></Link></td>
                          <td><Badge tone={user.plan.toLowerCase() === 'pro' ? 'purple' : 'neutral'}>{user.plan}</Badge></td>
                          <td><div className="stacked-value"><Badge dot tone={user.confirmed ? 'green' : 'amber'}>{user.confirmed ? 'Confirmed' : 'Pending'}</Badge><small>{user.provider || 'email'}</small></div></td>
                          <td><div className="stacked-value"><span>{timeAgo(user.lastActiveAt || user.lastSignInAt)}</span><small>Signed up {timeAgo(user.createdAt)}</small></div></td>
                          <td><div className="quota-cell"><div><strong>{formatNumber(user.agentUsedMinutes)}</strong><span> / {formatNumber(user.agentLimitMinutes)} min</span></div><ProgressBar compact max={user.agentLimitMinutes} value={user.agentUsedMinutes} /></div></td>
                          <td><div className="quota-cell"><div><strong>{formatNumber(requestsUsed)}</strong><span> / {formatNumber(requestsLimit)}</span></div><ProgressBar compact max={requestsLimit} value={requestsUsed} /></div></td>
                          <td><span className="credits-value">{formatNumber(user.credits)}</span></td>
                          <td><Link aria-label={`View ${user.email}`} className="row-link" href={`/users/${encodeURIComponent(user.id)}`}><Icon name="chevron-right" size={17} /></Link></td>
                        </tr>
                      );
                    })}</tbody>
                  </table>
                </div>
                <div className="mobile-record-list">
                  {result.items.map((user) => (
                    <Link className="mobile-user-card" href={`/users/${encodeURIComponent(user.id)}`} key={user.id}>
                      <div className="mobile-user-card__header"><Avatar email={user.email} image={user.avatarUrl} name={user.name} /><span><strong>{user.name || user.email.split('@')[0]}</strong><small>{user.email}</small></span><Icon name="chevron-right" size={17} /></div>
                      <div className="mobile-user-card__badges"><Badge tone={user.plan.toLowerCase() === 'pro' ? 'purple' : 'neutral'}>{user.plan}</Badge><Badge dot tone={user.confirmed ? 'green' : 'amber'}>{user.confirmed ? 'Confirmed' : 'Pending'}</Badge><span>{timeAgo(user.lastActiveAt || user.lastSignInAt)}</span></div>
                      <div className="mobile-user-card__quota"><div><span>Agent</span><strong>{formatNumber(user.agentUsedMinutes)} / {formatNumber(user.agentLimitMinutes)} min</strong></div><ProgressBar compact max={user.agentLimitMinutes} value={user.agentUsedMinutes} /></div>
                    </Link>
                  ))}
                </div>
                <Pagination onChange={setPage} page={result.page} pageSize={result.pageSize} total={result.total} />
              </>
            ) : (
              <EmptyState icon="users" title={hasFilters ? 'No users match these filters' : 'No users yet'} description={hasFilters ? 'Try clearing the search or selecting a different plan.' : 'Registered Coda accounts will appear here.'} />
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}
