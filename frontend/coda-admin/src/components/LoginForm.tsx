'use client';

import { useRouter } from 'next/navigation';
import { FormEvent, useState } from 'react';
import { stripBasePath } from '@/lib/base-path';
import { adminFetch, apiErrorMessage } from './admin-api';
import type { AdminIdentity } from './admin-types';
import { Icon } from './ui/Icons';

export function LoginForm({ nextPath = '/' }: { nextPath?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setLoading(true);

    try {
      await adminFetch<{ admin: AdminIdentity }>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: email.trim(), password }),
      });
      let destination = '/';
      try {
        const requested = new URL(nextPath, window.location.origin);
        if (requested.origin === window.location.origin && !nextPath.includes('\\')) {
          // router.replace() re-adds the mount, so hand it an in-app path —
          // a `next` that already carries /admin would otherwise double it.
          destination = stripBasePath(`${requested.pathname}${requested.search}${requested.hash}`);
        }
      } catch {
        destination = '/';
      }
      router.replace(destination);
      router.refresh();
    } catch (loginError) {
      setError(apiErrorMessage(loginError));
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-page__glow login-page__glow--one" />
      <div className="login-page__glow login-page__glow--two" />
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-card__brand">
          <span className="login-logo" aria-hidden="true">
            <span /><span /><span />
          </span>
          <span>coda</span>
        </div>
        <div className="login-card__intro">
          <span className="login-card__secure"><Icon name="shield" size={14} /> Secure administration</span>
          <h1 id="login-title">Welcome back</h1>
          <p>Sign in with your dedicated administrator credentials.</p>
        </div>

        <form className="login-form" onSubmit={submit}>
          {error ? (
            <div className="form-alert" role="alert">
              <Icon name="warning" size={18} />
              <span>{error}</span>
            </div>
          ) : null}
          <label className="field">
            <span>Email address</span>
            <span className="input-shell">
              <Icon name="mail" size={18} />
              <input
                autoComplete="username"
                autoFocus
                inputMode="email"
                onChange={(event) => setEmail(event.target.value)}
                placeholder="admin@company.com"
                required
                type="email"
                value={email}
              />
            </span>
          </label>
          <label className="field">
            <span>Password</span>
            <span className="input-shell">
              <Icon name="shield" size={18} />
              <input
                autoComplete="current-password"
                minLength={24}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Enter your password"
                required
                type={showPassword ? 'text' : 'password'}
                value={password}
              />
              <button
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                className="input-shell__action"
                onClick={() => setShowPassword((visible) => !visible)}
                type="button"
              >
                <Icon name="eye" size={18} />
              </button>
            </span>
          </label>
          <button className="login-submit" disabled={loading} type="submit">
            {loading ? <span className="button__spinner" /> : null}
            {loading ? 'Verifying…' : 'Sign in securely'}
            {!loading ? <Icon name="arrow-right" size={18} /> : null}
          </button>
        </form>

        <div className="login-card__footer">
          <Icon name="shield" size={15} />
          Access is restricted, rate-limited, and recorded.
        </div>
      </section>
      <p className="login-page__caption">Coda operations · EraCoda AI</p>
    </main>
  );
}
