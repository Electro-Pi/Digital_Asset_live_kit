-- Minimal local fixture matching the Coda objects inspected in production on
-- 2026-08-18. It exists only so the admin migration and its SQL assertions can
-- be exercised in an ephemeral PostgreSQL cluster; it is never deployed.

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin; end if;
end;
$$;

create schema auth;

create table auth.users (
  id uuid primary key,
  email text,
  encrypted_password text,
  email_confirmed_at timestamptz,
  confirmed_at timestamptz,
  raw_app_meta_data jsonb default '{}'::jsonb,
  raw_user_meta_data jsonb default '{}'::jsonb,
  phone text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  last_sign_in_at timestamptz,
  banned_until timestamptz,
  deleted_at timestamptz
);

create table auth.identities (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  provider text not null
);

create table public.profiles (
  id uuid primary key references auth.users(id),
  full_name text,
  avatar_url text,
  created_at timestamptz not null default now(),
  phone text,
  company text
);

create table public.plans (
  id text primary key,
  name text not null,
  live_seconds_limit integer not null default 0,
  tts_limit integer not null default 0,
  stt_limit integer not null default 0,
  llm_limit integer not null default 0,
  reset_period text not null default 'month',
  price_cents integer not null default 0,
  stripe_price_id text,
  is_active boolean not null default true,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  currency text not null default 'SAR',
  period_days integer not null default 30,
  credits_grant bigint not null default 0,
  name_ar text,
  description text
);

insert into public.plans (
  id, name, live_seconds_limit, tts_limit, stt_limit, llm_limit, sort_order
) values
  ('free', 'Free', 300, 10, 10, 50, 0),
  ('pro', 'Pro', 36000, 5000, 5000, 100000, 1);

create table public.subscriptions (
  user_id uuid primary key references auth.users(id),
  plan_id text not null default 'free' references public.plans(id),
  status text not null default 'active',
  stripe_customer_id text,
  stripe_subscription_id text,
  current_period_end timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  is_pro boolean not null default false
);

create table public.usage_counters (
  user_id uuid not null references auth.users(id),
  period_start date not null,
  live_seconds_used integer not null default 0,
  tts_used integer not null default 0,
  stt_used integer not null default 0,
  llm_used integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (user_id, period_start)
);

create table public.usage_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id),
  kind text not null check (kind in ('live', 'tts', 'stt', 'llm')),
  amount integer not null default 1,
  api_key_id uuid,
  metadata jsonb,
  created_at timestamptz not null default now()
);

create table public.credit_balances (
  user_id uuid primary key references auth.users(id),
  balance bigint not null default 0 check (balance >= 0),
  lifetime_granted bigint not null default 0,
  lifetime_spent bigint not null default 0,
  updated_at timestamptz not null default now()
);

create table public.demo_bookings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id),
  name text not null,
  company text,
  email text not null,
  phone text not null,
  preferred_date date,
  preferred_time time,
  message text,
  status text not null default 'new' check (status in ('new', 'contacted', 'scheduled', 'done', 'canceled')),
  source text not null default 'landing' check (source in ('landing', 'dashboard')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.payments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  provider text not null default 'moyasar',
  provider_invoice_id text unique,
  provider_payment_id text unique,
  kind text not null check (kind in ('plan', 'credits')),
  plan_id text references public.plans(id),
  pack_id text,
  credits bigint not null default 0,
  amount integer not null,
  currency text not null default 'SAR',
  status text not null default 'initiated',
  checkout_url text,
  fulfilled_at timestamptz,
  raw jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create or replace function public.period_start_for(p_reset text)
returns date language sql immutable set search_path = '' as $$
  select case p_reset
    when 'day' then current_date
    when 'none' then date '1970-01-01'
    else date_trunc('month', now())::date
  end;
$$;

create or replace function public.sync_is_pro()
returns trigger language plpgsql as $$ begin return new; end; $$;

create or replace function public.plan_for_user(p_user uuid)
returns public.plans language sql stable security definer set search_path = public as $$
  select p from public.plans p where p.id = 'free';
$$;

create or replace function public.refund_quota(p_user uuid, p_kind text, p_amount integer)
returns void language sql security definer set search_path = public as $$ select; $$;

create or replace function public.activate_plan(uuid, text, integer, text)
returns jsonb language sql security definer set search_path = public as $$ select '{}'::jsonb; $$;
create or replace function public.grant_credits(uuid, bigint, text, text, jsonb)
returns jsonb language sql security definer set search_path = public as $$ select '{}'::jsonb; $$;
create or replace function public.refund_credits(uuid, bigint, text, uuid, jsonb)
returns jsonb language sql security definer set search_path = public as $$ select '{}'::jsonb; $$;
create or replace function public.spend_credits(uuid, bigint, text, uuid, jsonb)
returns jsonb language sql security definer set search_path = public as $$ select '{}'::jsonb; $$;
create or replace function public.credit_balance(uuid)
returns bigint language sql security definer set search_path = public as $$ select 0::bigint; $$;
create or replace function public.resolve_api_key_full(text)
returns jsonb language sql security definer set search_path = public as $$ select null::jsonb; $$;
