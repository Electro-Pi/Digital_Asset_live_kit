'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { adminFetch, apiErrorMessage, queryString } from './admin-api';
import type {
  PaginatedData,
  PodcastEpisode,
  PodcastEpisodeStatus,
  PodcastSeat,
  PodcastSeatStatus,
  PodcastSubscriberPage,
  PodcastUploadTicket,
} from './admin-types';
import { Icon } from './ui/Icons';
import {
  Badge,
  EmptyState,
  ErrorState,
  PageHeading,
  Pagination,
  TableSkeleton,
  prettify,
  timeAgo,
} from './ui/Primitives';
import {
  defaultPublishFields,
  formatKsa,
  isoToKsaFields,
  ksaFieldsToIso,
  relativeToNow,
} from '@/lib/ksa-time';

type Tab = 'episodes' | 'seats' | 'subscribers';

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'episodes', label: 'Episodes' },
  { id: 'seats', label: 'Seat requests' },
  { id: 'subscribers', label: 'Alert list' },
];

const EPISODE_STATUSES: PodcastEpisodeStatus[] = ['draft', 'scheduled', 'archived'];
const SEAT_STATUSES: PodcastSeatStatus[] = ['new', 'confirmed', 'done', 'canceled'];

const VIDEO_EXTENSIONS = ['mp4', 'webm', 'mov', 'm4v'];
const IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'avif'];

function extensionOf(file: File): string {
  return (file.name.split('.').pop() ?? '').toLowerCase();
}

/**
 * Read a video's duration without uploading it, so the episode row carries a
 * length the operator never had to type. Failure is silent on purpose — a
 * container the browser cannot decode is still a file storage will serve.
 */
function readDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const probe = document.createElement('video');
    const done = (value: number | null) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    probe.preload = 'metadata';
    probe.onloadedmetadata = () =>
      done(Number.isFinite(probe.duration) ? Math.round(probe.duration) : null);
    probe.onerror = () => done(null);
    probe.src = url;
  });
}

/**
 * Send the file straight to storage with the one-shot URL the server minted.
 *
 * XHR rather than fetch because only XHR reports upload progress, and a
 * half-gigabyte episode uploading behind a silent spinner is indistinguishable
 * from one that has stalled.
 */
async function uploadFile(
  file: File,
  kind: 'video' | 'thumbnail',
  onProgress: (percent: number) => void,
): Promise<PodcastUploadTicket> {
  const extension = extensionOf(file);
  const allowed = kind === 'video' ? VIDEO_EXTENSIONS : IMAGE_EXTENSIONS;
  if (!allowed.includes(extension)) {
    throw new Error(`Unsupported file type .${extension}. Use ${allowed.join(', ')}.`);
  }

  const ticket = await adminFetch<PodcastUploadTicket>('/api/podcast/upload-url', {
    method: 'POST',
    body: JSON.stringify({ kind, extension }),
  });

  await new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('PUT', ticket.signedUrl, true);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onload = () =>
      request.status >= 200 && request.status < 300
        ? resolve()
        : reject(new Error(`Upload rejected by storage (${request.status})`));
    request.onerror = () => reject(new Error('The upload connection failed.'));
    request.onabort = () => reject(new Error('The upload was cancelled.'));

    // Multipart with the file under the empty field name is the shape the
    // storage upload endpoint expects from a browser.
    const body = new FormData();
    body.append('cacheControl', '3600');
    body.append('', file);
    request.send(body);
  });

  return ticket;
}

function episodeTone(episode: PodcastEpisode): 'green' | 'purple' | 'amber' | 'neutral' {
  if (episode.status === 'archived') return 'neutral';
  if (episode.status === 'draft') return 'amber';
  return episode.isLive ? 'green' : 'purple';
}

function episodeLabel(episode: PodcastEpisode): string {
  if (episode.status === 'archived') return 'Archived';
  if (episode.status === 'draft') return 'Draft';
  return episode.isLive ? 'Live' : 'Scheduled';
}

// ── Episode form ────────────────────────────────────────────────────────────

type FormState = {
  titleAr: string;
  titleEn: string;
  summaryAr: string;
  summaryEn: string;
  guestName: string;
  videoUrl: string;
  videoPath: string | null;
  thumbnailUrl: string;
  durationSeconds: number | null;
  date: string;
  time: string;
  status: PodcastEpisodeStatus;
};

function blankForm(): FormState {
  const { date, time } = defaultPublishFields();
  return {
    titleAr: '',
    titleEn: '',
    summaryAr: '',
    summaryEn: '',
    guestName: '',
    videoUrl: '',
    videoPath: null,
    thumbnailUrl: '',
    durationSeconds: null,
    date,
    time,
    status: 'scheduled',
  };
}

function formFrom(episode: PodcastEpisode): FormState {
  const { date, time } = isoToKsaFields(episode.publishAt);
  return {
    titleAr: episode.titleAr,
    titleEn: episode.titleEn,
    summaryAr: episode.summaryAr ?? '',
    summaryEn: episode.summaryEn ?? '',
    guestName: episode.guestName ?? '',
    videoUrl: episode.videoUrl,
    videoPath: episode.videoPath,
    thumbnailUrl: episode.thumbnailUrl ?? '',
    durationSeconds: episode.durationSeconds,
    date,
    time,
    status: episode.status,
  };
}

function EpisodeForm({
  episode,
  onClose,
  onSaved,
}: {
  episode: PodcastEpisode | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<FormState>(() => (episode ? formFrom(episode) : blankForm()));
  const [videoProgress, setVideoProgress] = useState<number | null>(null);
  const [thumbProgress, setThumbProgress] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const videoInput = useRef<HTMLInputElement>(null);
  const thumbInput = useRef<HTMLInputElement>(null);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  let publishIso = '';
  let publishError = '';
  try {
    publishIso = ksaFieldsToIso(form.date, form.time);
  } catch {
    publishError = 'Enter a valid date and time.';
  }

  async function pickVideo(file: File) {
    setError('');
    setVideoProgress(0);
    try {
      const duration = await readDuration(file);
      const ticket = await uploadFile(file, 'video', setVideoProgress);
      setForm((current) => ({
        ...current,
        videoUrl: ticket.publicUrl,
        videoPath: ticket.path,
        durationSeconds: duration ?? current.durationSeconds,
      }));
    } catch (uploadError) {
      setError(apiErrorMessage(uploadError));
    } finally {
      setVideoProgress(null);
      if (videoInput.current) videoInput.current.value = '';
    }
  }

  async function pickThumbnail(file: File) {
    setError('');
    setThumbProgress(0);
    try {
      const ticket = await uploadFile(file, 'thumbnail', setThumbProgress);
      set('thumbnailUrl', ticket.publicUrl);
    } catch (uploadError) {
      setError(apiErrorMessage(uploadError));
    } finally {
      setThumbProgress(null);
      if (thumbInput.current) thumbInput.current.value = '';
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (saving) return;
    if (publishError) {
      setError(publishError);
      return;
    }
    if (!form.videoUrl.trim()) {
      setError('Upload a video file, or paste a link to one.');
      return;
    }

    setSaving(true);
    setError('');
    try {
      const payload = {
        titleAr: form.titleAr.trim(),
        titleEn: form.titleEn.trim(),
        summaryAr: form.summaryAr.trim() || null,
        summaryEn: form.summaryEn.trim() || null,
        guestName: form.guestName.trim() || null,
        videoUrl: form.videoUrl.trim(),
        videoPath: form.videoPath,
        thumbnailUrl: form.thumbnailUrl.trim() || null,
        durationSeconds: form.durationSeconds,
        publishAt: publishIso,
        status: form.status,
      };
      await adminFetch(
        episode ? `/api/podcast/episodes/${encodeURIComponent(episode.id)}` : '/api/podcast/episodes',
        { method: episode ? 'PATCH' : 'POST', body: JSON.stringify(payload) },
      );
      onSaved();
    } catch (saveError) {
      setError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  }

  const busy = videoProgress !== null || thumbProgress !== null;

  return (
    <div className="drawer-layer" role="presentation">
      <button aria-label="Close episode form" className="drawer-scrim" onClick={onClose} type="button" />
      <aside aria-label={episode ? 'Edit episode' : 'New episode'} className="detail-drawer">
        <header className="detail-drawer__header">
          <div>
            <p>Podcast</p>
            <h2>{episode ? 'Edit episode' : 'New episode'}</h2>
          </div>
          <button aria-label="Close" className="icon-button" onClick={onClose} type="button">
            <Icon name="close" />
          </button>
        </header>

        <form className="detail-drawer__body form-stack" id="episode-form" onSubmit={submit}>
          <fieldset className="field">
            <legend>Video</legend>
            {form.videoUrl ? (
              <video
                controls
                preload="metadata"
                src={form.videoUrl}
                style={{ width: '100%', borderRadius: 12, background: '#05060b', maxHeight: 220 }}
              />
            ) : null}
            <input
              accept="video/mp4,video/webm,video/quicktime,video/x-m4v"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void pickVideo(file);
              }}
              ref={videoInput}
              type="file"
            />
            {videoProgress !== null ? (
              <small>Uploading… {videoProgress}%</small>
            ) : (
              <small>
                Up to 500 MB. MP4 plays everywhere; MOV and WebM do not play in every browser.
              </small>
            )}
            <input
              onChange={(event) => {
                set('videoUrl', event.target.value);
                // A pasted link is not ours to delete later, so drop any
                // storage path the previous upload left behind.
                set('videoPath', null);
              }}
              placeholder="…or paste an https:// video link"
              type="url"
              value={form.videoUrl}
            />
          </fieldset>

          <fieldset className="field">
            <legend>Cover image</legend>
            <input
              accept="image/jpeg,image/png,image/webp,image/avif"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void pickThumbnail(file);
              }}
              ref={thumbInput}
              type="file"
            />
            {thumbProgress !== null ? <small>Uploading… {thumbProgress}%</small> : null}
            <input
              onChange={(event) => set('thumbnailUrl', event.target.value)}
              placeholder="…or paste an https:// image link"
              type="url"
              value={form.thumbnailUrl}
            />
            <small>Optional. Shown before the video plays, and inside the alert email.</small>
          </fieldset>

          <label className="field">
            <span>Title (Arabic)</span>
            <input
              maxLength={180}
              onChange={(event) => set('titleAr', event.target.value)}
              required
              type="text"
              value={form.titleAr}
            />
          </label>
          <label className="field">
            <span>Title (English)</span>
            <input
              maxLength={180}
              onChange={(event) => set('titleEn', event.target.value)}
              required
              type="text"
              value={form.titleEn}
            />
          </label>
          <label className="field">
            <span>Summary (Arabic)</span>
            <textarea
              maxLength={1200}
              onChange={(event) => set('summaryAr', event.target.value)}
              rows={3}
              value={form.summaryAr}
            />
          </label>
          <label className="field">
            <span>Summary (English)</span>
            <textarea
              maxLength={1200}
              onChange={(event) => set('summaryEn', event.target.value)}
              rows={3}
              value={form.summaryEn}
            />
          </label>
          <label className="field">
            <span>Guest</span>
            <input
              maxLength={160}
              onChange={(event) => set('guestName', event.target.value)}
              placeholder="Name shown under the episode"
              type="text"
              value={form.guestName}
            />
          </label>

          <div className="field">
            <span>Goes live at — Saudi time (AST, UTC+3)</span>
            <div className="filter-group">
              <input
                onChange={(event) => set('date', event.target.value)}
                required
                type="date"
                value={form.date}
              />
              <input
                onChange={(event) => set('time', event.target.value)}
                required
                step={60}
                type="time"
                value={form.time}
              />
            </div>
            <small>
              {publishError
                ? publishError
                : `Appears on /podcast ${relativeToNow(publishIso)} — ${formatKsa(publishIso)} Riyadh. Subscribers are emailed within a minute of that.`}
            </small>
          </div>

          <label className="field">
            <span>Status</span>
            <span className="select-field">
              <select
                onChange={(event) => set('status', event.target.value as PodcastEpisodeStatus)}
                value={form.status}
              >
                {EPISODE_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {prettify(status)}
                  </option>
                ))}
              </select>
              <Icon name="chevron-down" size={15} />
            </span>
            <small>
              Draft stays hidden and sends nothing. Scheduled publishes and emails at the time
              above. Archived pulls a published episode back off the page.
            </small>
          </label>

          {error ? (
            <div className="inline-error" role="alert">
              <Icon name="warning" size={16} /> {error}
            </div>
          ) : null}
        </form>

        <footer className="detail-drawer__footer">
          <button
            className="button button--primary"
            disabled={saving || busy}
            form="episode-form"
            type="submit"
          >
            {saving ? <span className="button__spinner" /> : <Icon name="save" size={17} />}
            {saving ? 'Saving…' : episode ? 'Save changes' : 'Create episode'}
          </button>
          <button className="button button--secondary" onClick={onClose} type="button">
            Cancel
          </button>
        </footer>
      </aside>
    </div>
  );
}

// ── Episodes ────────────────────────────────────────────────────────────────

function EpisodesTab() {
  const [result, setResult] = useState<PaginatedData<PodcastEpisode> | null>(null);
  const [status, setStatus] = useState('all');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [editing, setEditing] = useState<PodcastEpisode | null>(null);
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const query = queryString({
        status: status === 'all' ? undefined : status,
        page,
        pageSize: 25,
      });
      setResult(await adminFetch<PaginatedData<PodcastEpisode>>(`/api/podcast/episodes${query}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [page, status]);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove(episode: PodcastEpisode) {
    if (!window.confirm(`Delete “${episode.titleEn}”? The uploaded video is removed too.`)) return;
    setDeletingId(episode.id);
    setActionError('');
    try {
      await adminFetch(`/api/podcast/episodes/${encodeURIComponent(episode.id)}`, {
        method: 'DELETE',
      });
      await load();
    } catch (deleteError) {
      setActionError(apiErrorMessage(deleteError));
    } finally {
      setDeletingId('');
    }
  }

  return (
    <section className="panel table-panel">
      <div className="table-toolbar">
        <div className="filter-group">
          <label className="select-field">
            <span className="sr-only">Filter episodes</span>
            <select
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              value={status}
            >
              <option value="all">All episodes</option>
              <option value="live">Live now</option>
              <option value="upcoming">Scheduled ahead</option>
              <option value="draft">Drafts</option>
              <option value="archived">Archived</option>
            </select>
            <Icon name="chevron-down" size={15} />
          </label>
        </div>
        <button className="button button--primary" onClick={() => setCreating(true)} type="button">
          <Icon name="sparkles" size={17} /> New episode
        </button>
      </div>

      {actionError ? (
        <div className="inline-error" role="alert">
          <Icon name="warning" size={16} /> {actionError}
          <button onClick={() => setActionError('')}>Dismiss</button>
        </div>
      ) : null}
      {error && !result ? <ErrorState message={error} onRetry={load} /> : null}
      {loading && !result ? <TableSkeleton rows={6} /> : null}

      {result ? (
        <div className={loading ? 'content-refreshing' : ''}>
          {result.items.length ? (
            <>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Episode</th>
                      <th>Goes live (Riyadh)</th>
                      <th>State</th>
                      <th>Alert email</th>
                      <th>
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.items.map((episode) => (
                      <tr key={episode.id}>
                        <td>
                          <div className="stacked-value">
                            <span>{episode.titleEn}</span>
                            <small dir="rtl">{episode.titleAr}</small>
                          </div>
                        </td>
                        <td>
                          <div className="stacked-value">
                            <span>{formatKsa(episode.publishAt)}</span>
                            <small>{relativeToNow(episode.publishAt)}</small>
                          </div>
                        </td>
                        <td>
                          <Badge dot tone={episodeTone(episode)}>
                            {episodeLabel(episode)}
                          </Badge>
                        </td>
                        <td>
                          {episode.notifiedAt ? (
                            <div className="stacked-value">
                              <span>Sent</span>
                              <small>{timeAgo(episode.notifiedAt)}</small>
                            </div>
                          ) : (
                            <span className="muted-copy">
                              {episode.status === 'scheduled' ? 'Pending' : '—'}
                            </span>
                          )}
                        </td>
                        <td>
                          <div className="filter-group">
                            <button
                              className="button button--secondary"
                              onClick={() => setEditing(episode)}
                              type="button"
                            >
                              <Icon name="settings" size={15} /> Edit
                            </button>
                            <button
                              aria-label={`Delete ${episode.titleEn}`}
                              className="icon-button"
                              disabled={deletingId === episode.id}
                              onClick={() => void remove(episode)}
                              type="button"
                            >
                              {deletingId === episode.id ? (
                                <span className="button__spinner" />
                              ) : (
                                <Icon name="close" size={16} />
                              )}
                            </button>
                          </div>
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
              description="Upload a video and pick the Riyadh time it should appear on the podcast page."
              icon="wave"
              title="No episodes yet"
            />
          )}
        </div>
      ) : null}

      {creating || editing ? (
        <EpisodeForm
          episode={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={() => {
            setCreating(false);
            setEditing(null);
            void load();
          }}
        />
      ) : null}
    </section>
  );
}

// ── Seat requests ───────────────────────────────────────────────────────────

function SeatsTab() {
  const [result, setResult] = useState<PaginatedData<PodcastSeat> | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [savingId, setSavingId] = useState('');

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
        status: status === 'all' ? undefined : status,
        page,
        pageSize: 25,
      });
      setResult(await adminFetch<PaginatedData<PodcastSeat>>(`/api/podcast/seats${query}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [page, search, status]);

  useEffect(() => {
    void load();
  }, [load]);

  async function updateStatus(seat: PodcastSeat, next: PodcastSeatStatus) {
    if (next === seat.status) return;
    setSavingId(seat.id);
    setActionError('');
    try {
      await adminFetch(`/api/podcast/seats/${encodeURIComponent(seat.id)}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: next }),
      });
      setResult((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.id === seat.id ? { ...item, status: next } : item,
              ),
            }
          : current,
      );
    } catch (updateError) {
      setActionError(apiErrorMessage(updateError));
    } finally {
      setSavingId('');
    }
  }

  return (
    <section className="panel table-panel">
      <div className="table-toolbar">
        <label className="search-field">
          <span className="sr-only">Search seat requests</span>
          <Icon name="search" size={18} />
          <input
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search name, org, or mobile…"
            type="search"
            value={searchInput}
          />
        </label>
        <div className="filter-group">
          <label className="select-field">
            <span className="sr-only">Filter seat status</span>
            <select
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              value={status}
            >
              <option value="all">All statuses</option>
              {SEAT_STATUSES.map((item) => (
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
                      <th>Guest</th>
                      <th>Mobile</th>
                      <th>Slot</th>
                      <th>Angle</th>
                      <th>Submitted</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.items.map((seat) => (
                      <tr key={seat.id}>
                        <td>
                          <div className="stacked-value">
                            <span>{seat.fullName}</span>
                            <small>{seat.organisation || 'No organisation given'}</small>
                          </div>
                        </td>
                        <td>
                          <a href={`tel:${seat.mobile}`}>{seat.mobile}</a>
                        </td>
                        <td>
                          <div className="stacked-value">
                            <span>{seat.slotTime}</span>
                            <small>{seat.dayId}</small>
                          </div>
                        </td>
                        <td>{prettify(seat.angle)}</td>
                        <td>{timeAgo(seat.createdAt)}</td>
                        <td>
                          <label className="status-select">
                            <span className="sr-only">Update status for {seat.fullName}</span>
                            {savingId === seat.id ? (
                              <span className="button__spinner" />
                            ) : (
                              <span className="badge__dot" />
                            )}
                            <select
                              disabled={savingId === seat.id}
                              onChange={(event) =>
                                void updateStatus(seat, event.target.value as PodcastSeatStatus)
                              }
                              value={seat.status}
                            >
                              {SEAT_STATUSES.map((item) => (
                                <option key={item} value={item}>
                                  {prettify(item)}
                                </option>
                              ))}
                            </select>
                            <Icon name="chevron-down" size={14} />
                          </label>
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
              description="Reservations from the booking form on the podcast page land here."
              icon="calendar"
              title="No seat requests yet"
            />
          )}
        </div>
      ) : null}
    </section>
  );
}

// ── Alert list ──────────────────────────────────────────────────────────────

function SubscribersTab() {
  const [result, setResult] = useState<PodcastSubscriberPage | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('active');
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
        status: status === 'all' ? undefined : status,
        page,
        pageSize: 25,
      });
      setResult(await adminFetch<PodcastSubscriberPage>(`/api/podcast/subscribers${query}`));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [page, search, status]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="panel table-panel">
      <div className="table-toolbar">
        <label className="search-field">
          <span className="sr-only">Search subscribers</span>
          <Icon name="search" size={18} />
          <input
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search email…"
            type="search"
            value={searchInput}
          />
        </label>
        <div className="filter-group">
          {result ? <Badge tone="green">{result.active} active</Badge> : null}
          <label className="select-field">
            <span className="sr-only">Filter subscriber status</span>
            <select
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              value={status}
            >
              <option value="active">Active</option>
              <option value="unsubscribed">Unsubscribed</option>
              <option value="bounced">Bounced</option>
              <option value="all">All</option>
            </select>
            <Icon name="chevron-down" size={15} />
          </label>
        </div>
      </div>

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
                      <th>Email</th>
                      <th>Language</th>
                      <th>Status</th>
                      <th>Subscribed</th>
                      <th>Last alert</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.items.map((subscriber) => (
                      <tr key={subscriber.id}>
                        <td>
                          <a href={`mailto:${subscriber.email}`}>{subscriber.email}</a>
                        </td>
                        <td>{subscriber.lang === 'ar' ? 'Arabic' : 'English'}</td>
                        <td>
                          <Badge
                            dot
                            tone={
                              subscriber.status === 'active'
                                ? 'green'
                                : subscriber.status === 'bounced'
                                  ? 'red'
                                  : 'neutral'
                            }
                          >
                            {prettify(subscriber.status)}
                          </Badge>
                        </td>
                        <td>{timeAgo(subscriber.createdAt)}</td>
                        <td>
                          {subscriber.lastNotifiedAt ? (
                            timeAgo(subscriber.lastNotifiedAt)
                          ) : (
                            <span className="muted-copy">Never</span>
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
              description="Visitors who tap “Subscribe to alerts” on the podcast page appear here."
              icon="mail"
              title="Nobody on the alert list yet"
            />
          )}
        </div>
      ) : null}
    </section>
  );
}

export function PodcastDashboard() {
  const [tab, setTab] = useState<Tab>('episodes');

  return (
    <div className="page-stack">
      <PageHeading
        description="Upload an episode, set the Riyadh time it appears on the public page, and everyone on the alert list is emailed the moment it does."
        eyebrow="Podcast"
        title="Coda Podcast"
      />

      <div className="segmented-control" role="tablist">
        {TABS.map((item) => (
          <button
            aria-selected={tab === item.id}
            className={tab === item.id ? 'is-active' : ''}
            key={item.id}
            onClick={() => setTab(item.id)}
            role="tab"
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'episodes' ? <EpisodesTab /> : null}
      {tab === 'seats' ? <SeatsTab /> : null}
      {tab === 'subscribers' ? <SubscribersTab /> : null}
    </div>
  );
}
