'use client';

import { useCallback, useEffect, useState } from 'react';
import { adminFetch, apiErrorMessage, queryString } from './admin-api';
import type { ApiKey, ApiKeyScope, ApiKeyStatus, CreatedApiKey, PaginatedData } from './admin-types';
import { Icon } from './ui/Icons';
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  PageHeading,
  Pagination,
  TableSkeleton,
  formatDate,
  prettify,
  timeAgo,
} from './ui/Primitives';

const SCOPES: ApiKeyScope[] = ['agent', 'tts', 'stt', 'llm'];
const STATUSES: ApiKeyStatus[] = ['active', 'revoked', 'expired'];

function statusTone(status: ApiKeyStatus): 'green' | 'red' | 'amber' {
  return status === 'active' ? 'green' : status === 'revoked' ? 'red' : 'amber';
}

function scopeTone(scope: ApiKeyScope): 'purple' | 'blue' | 'neutral' {
  return scope === 'agent' ? 'purple' : scope === 'tts' ? 'blue' : 'neutral';
}

/** Minutes come from the RPC already divided; numeric() arrives as a string over JSON. */
function minutes(value: number | string | null | undefined): string {
  const parsed = typeof value === 'string' ? Number(value) : (value ?? 0);
  return Number.isFinite(parsed) ? parsed.toFixed(1) : '0.0';
}

export function KeysDashboard() {
  const [result, setResult] = useState<PaginatedData<ApiKey> | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [scope, setScope] = useState('all');
  const [status, setStatus] = useState('all');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');

  const [creating, setCreating] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [issued, setIssued] = useState<CreatedApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokingId, setRevokingId] = useState('');

  const [email, setEmail] = useState('');
  const [label, setLabel] = useState('');
  const [formScopes, setFormScopes] = useState<ApiKeyScope[]>(['agent']);
  const [maxConcurrent, setMaxConcurrent] = useState(1);
  const [expiresAt, setExpiresAt] = useState('');

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
        scope: scope === 'all' ? undefined : scope,
        status: status === 'all' ? undefined : status,
        page,
        pageSize: 25,
      });
      setResult(await adminFetch<PaginatedData<ApiKey>>(`/api/keys${query}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [page, search, scope, status]);

  useEffect(() => {
    void load();
  }, [load]);

  // The issued-key panel is the only place the secret ever exists in the UI.
  // Locking the background scroll keeps it from being dismissed by accident
  // before it has been copied.
  useEffect(() => {
    document.body.style.overflow = dialogOpen || issued ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [dialogOpen, issued]);

  function resetForm() {
    setEmail('');
    setLabel('');
    setFormScopes(['agent']);
    setMaxConcurrent(1);
    setExpiresAt('');
  }

  function toggleScope(value: ApiKeyScope) {
    setFormScopes((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!formScopes.length) {
      setActionError('Choose at least one scope.');
      return;
    }
    setCreating(true);
    setActionError('');
    try {
      const created = await adminFetch<CreatedApiKey>('/api/keys', {
        method: 'POST',
        body: JSON.stringify({
          email,
          label,
          scopes: formScopes,
          maxConcurrent,
          // datetime-local has no zone; interpret it in the operator's own.
          expiresAt: expiresAt ? new Date(expiresAt).toISOString() : null,
        }),
      });
      setDialogOpen(false);
      resetForm();
      setIssued(created);
      setCopied(false);
      void load();
    } catch (createError) {
      setActionError(apiErrorMessage(createError));
    } finally {
      setCreating(false);
    }
  }

  async function revoke(key: ApiKey) {
    if (!window.confirm(`Revoke "${key.label}"? Any integration using it stops working immediately.`)) {
      return;
    }
    setRevokingId(key.id);
    setActionError('');
    try {
      await adminFetch<unknown>(`/api/keys/${encodeURIComponent(key.id)}`, { method: 'DELETE' });
      void load();
    } catch (revokeError) {
      setActionError(apiErrorMessage(revokeError));
    } finally {
      setRevokingId('');
    }
  }

  async function copyKey() {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.key);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="Programmatic access"
        title="API keys"
        description="Issue scoped keys for the voice agent and the speech APIs, and see how many agent minutes each one has spent."
        actions={
          <Button icon="shield" onClick={() => setDialogOpen(true)} variant="primary">
            Issue key
          </Button>
        }
      />

      <section className="panel table-panel">
        <div className="table-toolbar">
          <label className="search-field">
            <span className="sr-only">Search keys</span>
            <Icon name="search" size={18} />
            <input
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search label or owner email…"
              type="search"
              value={searchInput}
            />
            {searchInput ? (
              <button aria-label="Clear search" onClick={() => setSearchInput('')} type="button">
                <Icon name="close" size={15} />
              </button>
            ) : null}
          </label>
          <div className="filter-group">
            <label className="select-field">
              <span className="sr-only">Filter by scope</span>
              <select
                onChange={(event) => {
                  setScope(event.target.value);
                  setPage(1);
                }}
                value={scope}
              >
                <option value="all">All scopes</option>
                {SCOPES.map((item) => (
                  <option key={item} value={item}>
                    {prettify(item)}
                  </option>
                ))}
              </select>
              <Icon name="chevron-down" size={15} />
            </label>
            <label className="select-field">
              <span className="sr-only">Filter by status</span>
              <select
                onChange={(event) => {
                  setStatus(event.target.value);
                  setPage(1);
                }}
                value={status}
              >
                <option value="all">All statuses</option>
                {STATUSES.map((item) => (
                  <option key={item} value={item}>
                    {prettify(item)}
                  </option>
                ))}
              </select>
              <Icon name="chevron-down" size={15} />
            </label>
          </div>
        </div>

        {actionError ? (
          <div className="inline-error" role="alert">
            <Icon name="warning" size={16} /> {actionError}
            <button onClick={() => setActionError('')}>Dismiss</button>
          </div>
        ) : null}
        {error && !result ? <ErrorState message={error} onRetry={load} /> : null}
        {loading && !result ? <TableSkeleton rows={8} /> : null}

        {result ? (
          <div className={loading ? 'content-refreshing' : ''}>
            {result.items.length ? (
              <>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Key</th>
                        <th>Owner</th>
                        <th>Scopes</th>
                        <th>Agent minutes (30d)</th>
                        <th>Lifetime</th>
                        <th>Last used</th>
                        <th>Status</th>
                        <th>
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.items.map((key) => (
                        <tr key={key.id}>
                          <td>
                            <div className="stacked-value">
                              <strong>{key.label}</strong>
                              <small>
                                <code>{key.masked}</code>
                              </small>
                            </div>
                          </td>
                          <td>
                            <div className="stacked-value">
                              <span>{key.ownerEmail}</span>
                              <small>{key.maxConcurrent} concurrent max</small>
                            </div>
                          </td>
                          <td>
                            <div className="badge-row">
                              {key.scopes.map((item) => (
                                <Badge key={item} tone={scopeTone(item)}>
                                  {item}
                                </Badge>
                              ))}
                            </div>
                          </td>
                          <td>
                            <strong>{minutes(key.liveMinutes)}</strong>
                          </td>
                          <td>{minutes(key.liveMinutesLifetime)}</td>
                          <td>
                            <div className="stacked-value">
                              <span>{key.lastUsedAt ? timeAgo(key.lastUsedAt) : 'Never'}</span>
                              <small>Created {formatDate(key.createdAt)}</small>
                            </div>
                          </td>
                          <td>
                            <Badge dot tone={statusTone(key.status)}>
                              {prettify(key.status)}
                            </Badge>
                          </td>
                          <td>
                            {key.status === 'revoked' ? null : (
                              <Button
                                loading={revokingId === key.id}
                                onClick={() => void revoke(key)}
                                variant="danger"
                              >
                                Revoke
                              </Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  onChange={setPage}
                  page={result.page}
                  pageSize={result.pageSize}
                  total={result.total}
                />
              </>
            ) : (
              <EmptyState
                description={
                  search || scope !== 'all' || status !== 'all'
                    ? 'Try clearing your filters to see more keys.'
                    : 'Issue a key to give a partner programmatic access to the agent.'
                }
                icon="shield"
                title="No API keys found"
              />
            )}
          </div>
        ) : null}
      </section>

      {dialogOpen ? (
        <div className="drawer-layer" role="presentation">
          <button
            aria-label="Cancel"
            className="drawer-scrim"
            onClick={() => setDialogOpen(false)}
            type="button"
          />
          <aside aria-label="Issue an API key" className="detail-drawer">
            <header className="detail-drawer__header">
              <div>
                <p>Programmatic access</p>
                <h2>Issue API key</h2>
              </div>
              <button
                aria-label="Close"
                className="icon-button"
                onClick={() => setDialogOpen(false)}
                type="button"
              >
                <Icon name="close" />
              </button>
            </header>
            <form className="detail-drawer__body form-stack" id="issue-key-form" onSubmit={submit}>
              <label className="field">
                <span>Owner email</span>
                <input
                  autoComplete="off"
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="partner@example.com"
                  required
                  type="email"
                  value={email}
                />
                <small>
                  A Coda account is created for this address if it does not have one. Usage and
                  quota belong to that account.
                </small>
              </label>
              <label className="field">
                <span>Label</span>
                <input
                  maxLength={60}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="Partner name or integration"
                  required
                  type="text"
                  value={label}
                />
              </label>
              <fieldset className="field">
                <legend>Scopes</legend>
                <div className="checkbox-row">
                  {SCOPES.map((item) => (
                    <label className="checkbox-field" key={item}>
                      <input
                        checked={formScopes.includes(item)}
                        onChange={() => toggleScope(item)}
                        type="checkbox"
                      />
                      <span>{item}</span>
                    </label>
                  ))}
                </div>
                <small>
                  <code>agent</code> grants live voice sessions. Existing keys carry only{' '}
                  <code>tts</code>.
                </small>
              </fieldset>
              <label className="field">
                <span>Max concurrent sessions</span>
                <input
                  max={100}
                  min={1}
                  onChange={(event) => setMaxConcurrent(Number(event.target.value))}
                  required
                  type="number"
                  value={maxConcurrent}
                />
                <small>
                  A live session bills per second while it is open, so this is the ceiling on what
                  one leaked key can spend at once.
                </small>
              </label>
              <label className="field">
                <span>Expires (optional)</span>
                <input
                  onChange={(event) => setExpiresAt(event.target.value)}
                  type="datetime-local"
                  value={expiresAt}
                />
              </label>
            </form>
            <footer className="detail-drawer__footer">
              <Button form="issue-key-form" loading={creating} type="submit" variant="primary">
                Issue key
              </Button>
              <Button onClick={() => setDialogOpen(false)} type="button" variant="secondary">
                Cancel
              </Button>
            </footer>
          </aside>
        </div>
      ) : null}

      {issued ? (
        <div className="drawer-layer" role="presentation">
          <span className="drawer-scrim" />
          <aside aria-label="Newly issued API key" className="detail-drawer">
            <header className="detail-drawer__header">
              <div>
                <p>Copy it now</p>
                <h2>{issued.label}</h2>
              </div>
            </header>
            <div className="detail-drawer__body">
              <div className="inline-error" role="alert">
                <Icon name="warning" size={16} />
                This is the only time this key is shown. It is stored only as a hash, so it cannot
                be displayed again — if it is lost, revoke it and issue another.
              </div>
              <label className="field">
                <span>API key</span>
                <textarea className="key-reveal" readOnly rows={3} value={issued.key} />
              </label>
              <dl className="booking-details">
                <div>
                  <dt>
                    <Icon name="user" size={16} /> Owner
                  </dt>
                  <dd>{issued.ownerEmail}</dd>
                </div>
                <div>
                  <dt>
                    <Icon name="shield" size={16} /> Scopes
                  </dt>
                  <dd>{issued.scopes.join(', ')}</dd>
                </div>
                <div>
                  <dt>
                    <Icon name="activity" size={16} /> Max concurrent
                  </dt>
                  <dd>{issued.maxConcurrent}</dd>
                </div>
                <div>
                  <dt>
                    <Icon name="clock" size={16} /> Expires
                  </dt>
                  <dd>{issued.expiresAt ? formatDate(issued.expiresAt, true) : 'Never'}</dd>
                </div>
              </dl>
            </div>
            <footer className="detail-drawer__footer">
              <Button icon={copied ? 'check' : 'file'} onClick={() => void copyKey()} variant="primary">
                {copied ? 'Copied' : 'Copy key'}
              </Button>
              <Button onClick={() => setIssued(null)} variant="secondary">
                {copied ? 'Done' : 'Close without copying'}
              </Button>
            </footer>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
