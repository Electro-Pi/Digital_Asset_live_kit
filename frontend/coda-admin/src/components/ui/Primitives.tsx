'use client';

import Link from 'next/link';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName } from './Icons';

export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-heading__actions">{actions}</div> : null}
    </header>
  );
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: IconName;
  loading?: boolean;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
}

export function Button({
  children,
  className = '',
  icon,
  loading = false,
  variant = 'secondary',
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`button button--${variant} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <span className="button__spinner" aria-hidden="true" /> : null}
      {!loading && icon ? <Icon name={icon} size={17} /> : null}
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = 'neutral',
  dot = false,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'green' | 'blue' | 'purple' | 'amber' | 'red';
  dot?: boolean;
}) {
  return (
    <span className={`badge badge--${tone}`}>
      {dot ? <span className="badge__dot" /> : null}
      {children}
    </span>
  );
}

export function ProgressBar({
  value,
  max,
  label,
  compact = false,
}: {
  value: number;
  max: number;
  label?: string;
  compact?: boolean;
}) {
  const percent = max <= 0 ? (value > 0 ? 100 : 0) : Math.min(100, (value / max) * 100);
  const tone = percent >= 100 ? 'danger' : percent >= 80 ? 'warning' : 'normal';

  return (
    <div className={`progress ${compact ? 'progress--compact' : ''}`}>
      {label ? (
        <div className="progress__meta">
          <span>{label}</span>
          <span>{Math.round(percent)}%</span>
        </div>
      ) : null}
      <div
        className="progress__track"
        role="progressbar"
        aria-label={label ?? 'Quota usage'}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <span
          className={`progress__fill progress__fill--${tone}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

export function Avatar({
  name,
  email,
  image,
  size = 'medium',
}: {
  name?: string | null;
  email: string;
  image?: string | null;
  size?: 'small' | 'medium' | 'large';
}) {
  const label = name?.trim() || email;
  const initials = label
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('');
  const safeImage = image && (image.startsWith('/') || image.startsWith('data:image/'))
    ? image
    : null;

  return (
    <span className={`avatar avatar--${size}`} aria-hidden="true">
      {safeImage ? <img src={safeImage} alt="" /> : initials || 'U'}
    </span>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <span className="state-panel__icon"><Icon name="warning" size={22} /></span>
      <div>
        <strong>We couldn&apos;t load this data</strong>
        <p>{message}</p>
      </div>
      {onRetry ? (
        <Button icon="refresh" onClick={onRetry}>Retry</Button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  icon = 'database',
  title,
  description,
}: {
  icon?: IconName;
  title: string;
  description: string;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon"><Icon name={icon} size={25} /></span>
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

export function TableSkeleton({ rows = 7 }: { rows?: number }) {
  return (
    <div className="table-skeleton" aria-label="Loading data">
      {Array.from({ length: rows }, (_, index) => (
        <div className="table-skeleton__row" key={index}>
          <span className="skeleton skeleton--avatar" />
          <span className="skeleton" />
          <span className="skeleton skeleton--short" />
          <span className="skeleton skeleton--medium" />
        </div>
      ))}
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="dashboard-skeleton" aria-label="Loading dashboard">
      <div className="summary-grid">
        {Array.from({ length: 4 }, (_, index) => (
          <div className="metric-card" key={index}>
            <span className="skeleton skeleton--short" />
            <span className="skeleton skeleton--value" />
            <span className="skeleton skeleton--medium" />
          </div>
        ))}
      </div>
      <div className="chart-grid">
        <div className="panel"><span className="skeleton skeleton--chart" /></div>
        <div className="panel"><span className="skeleton skeleton--chart" /></div>
      </div>
    </div>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  return (
    <div className="pagination">
      <p>
        Showing <strong>{start}</strong>–<strong>{end}</strong> of <strong>{total}</strong>
      </p>
      <div className="pagination__buttons">
        <Button
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
          variant="ghost"
        >
          <Icon name="arrow-left" size={17} />
        </Button>
        <span>Page {page} of {pages}</span>
        <Button
          aria-label="Next page"
          disabled={page >= pages}
          onClick={() => onChange(page + 1)}
          variant="ghost"
        >
          <Icon name="arrow-right" size={17} />
        </Button>
      </div>
    </div>
  );
}

export function BackLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link className="back-link" href={href}>
      <Icon name="arrow-left" size={16} />
      {children}
    </Link>
  );
}

export function formatNumber(value: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(value ?? 0);
}

export function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'SAR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value ?? 0);
}

export function formatDate(value?: string | null, withTime = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return '—';
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    ...(withTime ? { timeStyle: 'short' as const } : {}),
    timeZone: 'Africa/Cairo',
  }).format(date);
}

export function timeAgo(value?: string | null) {
  if (!value) return 'Never';
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return 'Never';
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
  const ranges: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['week', 604_800],
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
  ];
  for (const [unit, amount] of ranges) {
    if (Math.abs(seconds) >= amount) return formatter.format(Math.round(seconds / amount), unit);
  }
  return 'Just now';
}

export function prettify(value: string) {
  return value
    .replace(/[_-]/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
