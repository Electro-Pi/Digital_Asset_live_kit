'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { adminFetch } from './admin-api';
import { Icon, type IconName } from './ui/Icons';

const navigation: Array<{
  href: string;
  label: string;
  description: string;
  icon: IconName;
}> = [
  { href: '/', label: 'Overview', description: 'Business health', icon: 'gauge' },
  { href: '/users', label: 'Users', description: 'Accounts & usage', icon: 'users' },
  { href: '/limits', label: 'Limits', description: 'Plan defaults', icon: 'settings' },
  { href: '/keys', label: 'API keys', description: 'Programmatic access', icon: 'shield' },
  { href: '/bookings', label: 'Demo bookings', description: 'Sales pipeline', icon: 'calendar' },
  { href: '/audit', label: 'Audit log', description: 'Admin activity', icon: 'file' },
];

function isActive(pathname: string, href: string) {
  return href === '/' ? pathname === '/' : pathname.startsWith(href);
}

function CodaMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <span className="brand-mark__core" />
      <span className="brand-mark__ring brand-mark__ring--one" />
      <span className="brand-mark__ring brand-mark__ring--two" />
    </span>
  );
}

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => setMenuOpen(false), [pathname]);

  useEffect(() => {
    document.body.style.overflow = menuOpen ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [menuOpen]);

  const current = navigation.find((item) => isActive(pathname, item.href));

  async function logout() {
    setLoggingOut(true);
    try {
      await adminFetch<unknown>('/api/auth/logout', { method: 'POST' });
    } catch {
      // Clear the browser view even when the server already considers the session expired.
    } finally {
      router.replace('/login');
      router.refresh();
    }
  }

  return (
    <div className="admin-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <button
        aria-label="Close navigation"
        className={`sidebar-scrim ${menuOpen ? 'sidebar-scrim--visible' : ''}`}
        onClick={() => setMenuOpen(false)}
        type="button"
      />
      <aside className={`sidebar ${menuOpen ? 'sidebar--open' : ''}`}>
        <div className="sidebar__brand">
          <CodaMark />
          <div>
            <strong>coda</strong>
            <span>Admin console</span>
          </div>
          <button
            aria-label="Close navigation"
            className="icon-button sidebar__close"
            onClick={() => setMenuOpen(false)}
            type="button"
          >
            <Icon name="close" />
          </button>
        </div>

        <div className="sidebar__section-label">Workspace</div>
        <nav aria-label="Admin navigation" className="sidebar__nav">
          {navigation.map((item) => (
            <Link
              aria-current={isActive(pathname, item.href) ? 'page' : undefined}
              className={`nav-link ${isActive(pathname, item.href) ? 'nav-link--active' : ''}`}
              href={item.href}
              key={item.href}
            >
              <span className="nav-link__icon"><Icon name={item.icon} size={19} /></span>
              <span>
                <strong>{item.label}</strong>
                <small>{item.description}</small>
              </span>
              <Icon className="nav-link__chevron" name="chevron-right" size={15} />
            </Link>
          ))}
        </nav>

        <div className="sidebar__footer">
          <div className="secure-session">
            <span className="secure-session__icon"><Icon name="shield" size={17} /></span>
            <span><strong>Protected session</strong><small>Encrypted & monitored</small></span>
          </div>
          <button className="logout-button" disabled={loggingOut} onClick={logout} type="button">
            {loggingOut ? <span className="button__spinner" /> : <Icon name="logout" size={18} />}
            {loggingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </aside>

      <div className="admin-shell__content">
        <header className="topbar">
          <button
            aria-expanded={menuOpen}
            aria-label="Open navigation"
            className="icon-button topbar__menu"
            onClick={() => setMenuOpen(true)}
            type="button"
          >
            <Icon name="menu" />
          </button>
          <div className="topbar__title">
            <span>{current?.label ?? 'Admin console'}</span>
            <small>All times shown in Cairo time</small>
          </div>
          <div className="topbar__status">
            <span className="live-dot" />
            Live data
          </div>
        </header>
        <main className="main-content" id="main-content">{children}</main>
      </div>
    </div>
  );
}
