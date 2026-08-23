import { adminPath, stripBasePath } from '@/lib/base-path';
import type { ApiErrorBody } from './admin-types';

export class AdminApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'AdminApiError';
    this.status = status;
    this.code = code;
  }
}

function readCsrfToken() {
  if (typeof document === 'undefined') return '';
  const cookies = document.cookie.split('; ');
  const csrfCookie =
    cookies.find((entry) => entry.startsWith('__Secure-coda-admin-csrf=')) ??
    cookies.find((entry) => entry.startsWith('coda-admin-csrf='));
  if (!csrfCookie) return '';
  return decodeURIComponent(csrfCookie.slice(csrfCookie.indexOf('=') + 1));
}

async function parseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) return null;

  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function adminFetch<T>(
  input: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  headers.set('accept', 'application/json');

  if (init.body && !headers.has('content-type')) {
    headers.set('content-type', 'application/json');
  }

  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrfToken = readCsrfToken();
    if (csrfToken) headers.set('x-csrf-token', csrfToken);
  }

  // Callers pass in-app paths such as '/api/users'. fetch resolves them against
  // the document, not the router, so the mount has to be added here.
  const response = await fetch(adminPath(input), {
    ...init,
    method,
    headers,
    credentials: 'same-origin',
    cache: 'no-store',
  });
  const body = (await parseBody(response)) as
    | ({ data?: T } & ApiErrorBody)
    | null;

  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined') {
      // `next` is stored basePath-relative, matching what the proxy writes, so
      // that LoginForm's router.replace() adds the prefix exactly once.
      const next = `${stripBasePath(window.location.pathname)}${window.location.search}`;
      window.location.assign(adminPath(`/login?next=${encodeURIComponent(next)}`));
    }

    throw new AdminApiError(
      body?.error?.message ??
        body?.message ??
        (response.status >= 500
          ? 'The admin service is temporarily unavailable.'
          : 'The request could not be completed.'),
      response.status,
      body?.error?.code,
    );
  }

  if (!body || !('data' in body)) {
    throw new AdminApiError('The server returned an invalid response.', 502);
  }

  return body.data as T;
}

export function apiErrorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : 'Something went wrong. Please try again.';
}

export function queryString(
  values: Record<string, string | number | undefined | null>,
) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value));
    }
  }
  const serialized = query.toString();
  return serialized ? `?${serialized}` : '';
}
