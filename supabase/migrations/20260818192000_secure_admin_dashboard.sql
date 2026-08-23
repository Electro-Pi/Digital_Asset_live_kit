-- Secure server-side data layer for the Coda admin dashboard.
--
-- The `private` schema must NOT be added to Supabase's exposed API schemas.
-- Every callable function created here is explicitly service-role-only.  The
-- dashboard passes HMAC-SHA256 hashes of its opaque session and CSRF tokens;
-- raw tokens are never stored in Postgres.

begin;

create schema if not exists private;
revoke all on schema private from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Close existing privilege escalations first. PostgreSQL grants EXECUTE on new
-- functions to PUBLIC by default, which includes both anon and authenticated.
-- ---------------------------------------------------------------------------

revoke all on function public.activate_plan(uuid, text, integer, text)
  from public, anon, authenticated;
revoke all on function public.grant_credits(uuid, bigint, text, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.refund_credits(uuid, bigint, text, uuid, jsonb)
  from public, anon, authenticated;
revoke all on function public.spend_credits(uuid, bigint, text, uuid, jsonb)
  from public, anon, authenticated;
revoke all on function public.credit_balance(uuid)
  from public, anon, authenticated;
revoke all on function public.resolve_api_key_full(text)
  from public, anon, authenticated;

grant execute on function public.activate_plan(uuid, text, integer, text) to service_role;
grant execute on function public.grant_credits(uuid, bigint, text, text, jsonb) to service_role;
grant execute on function public.refund_credits(uuid, bigint, text, uuid, jsonb) to service_role;
grant execute on function public.spend_credits(uuid, bigint, text, uuid, jsonb) to service_role;
grant execute on function public.credit_balance(uuid) to service_role;
grant execute on function public.resolve_api_key_full(text) to service_role;

-- The only pre-existing public function without a pinned search path is a
-- trigger that references no relations. An empty path is therefore sufficient.
alter function public.sync_is_pro() set search_path = '';
-- period_start_for reads current_date/now(), so IMMUTABLE can cache a stale
-- billing period across a day/month boundary. STABLE is the correct volatility.
alter function public.period_start_for(text) stable;

-- ---------------------------------------------------------------------------
-- Private admin state. RLS is defence in depth; there are deliberately no
-- policies and no direct grants. SECURITY DEFINER RPCs are the sole access path.
-- ---------------------------------------------------------------------------

create table if not exists private.admin_users (
  user_id             uuid primary key references auth.users(id) on delete cascade,
  role                text not null check (role in ('owner', 'operator', 'viewer')),
  is_active           boolean not null default true,
  credential_version  bigint not null default 1 check (credential_version > 0),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create table if not exists private.admin_sessions (
  id                   uuid primary key default gen_random_uuid(),
  admin_user_id        uuid not null references private.admin_users(user_id) on delete cascade,
  token_hash           text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  csrf_hash            text not null check (csrf_hash ~ '^[0-9a-f]{64}$'),
  credential_version   bigint not null check (credential_version > 0),
  ip_address           inet,
  user_agent           text,
  created_at           timestamptz not null default now(),
  last_seen_at         timestamptz not null default now(),
  idle_expires_at      timestamptz not null default (now() + interval '30 minutes'),
  absolute_expires_at  timestamptz not null default (now() + interval '8 hours'),
  revoked_at           timestamptz,
  revoke_reason        text,
  check (idle_expires_at <= absolute_expires_at),
  check (revoked_at is null or revoke_reason is not null)
);

create table if not exists private.admin_login_attempts (
  id                bigint generated always as identity primary key,
  email_normalized  text not null,
  ip_address        inet not null,
  -- NULL is a rate-limit reservation made immediately before password auth.
  -- It counts as a failure until admin_record_login_attempt completes it.
  succeeded         boolean,
  attempted_at      timestamptz not null default now(),
  completed_at      timestamptz
);

create table if not exists private.admin_audit_log (
  id             bigint generated always as identity primary key,
  actor_user_id  uuid not null,
  actor_email    text,
  actor_role     text not null check (actor_role in ('owner', 'operator', 'viewer')),
  action         text not null,
  target_type    text not null,
  target_id      text not null,
  before_state   jsonb,
  after_state    jsonb,
  request_id     uuid not null,
  ip_address     inet,
  created_at     timestamptz not null default now()
);

create table if not exists private.user_quota_overrides (
  user_id             uuid primary key references auth.users(id) on delete cascade,
  live_seconds_limit  integer check (live_seconds_limit >= 0),
  tts_limit           integer check (tts_limit >= 0),
  stt_limit           integer check (stt_limit >= 0),
  llm_limit           integer check (llm_limit >= 0),
  updated_by          uuid not null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  check (
    live_seconds_limit is not null or tts_limit is not null or
    stt_limit is not null or llm_limit is not null
  )
);

alter table private.admin_users enable row level security;
alter table private.admin_sessions enable row level security;
alter table private.admin_login_attempts enable row level security;
alter table private.admin_audit_log enable row level security;
alter table private.user_quota_overrides enable row level security;

revoke all on all tables in schema private from public, anon, authenticated, service_role;
revoke all on all sequences in schema private from public, anon, authenticated, service_role;

create index if not exists admin_sessions_user_active_idx
  on private.admin_sessions (admin_user_id, revoked_at, absolute_expires_at desc);
create index if not exists admin_login_attempts_pair_idx
  on private.admin_login_attempts (email_normalized, ip_address, attempted_at desc)
  where succeeded is not true;
create index if not exists admin_login_attempts_ip_idx
  on private.admin_login_attempts (ip_address, attempted_at desc)
  where succeeded is not true;
create index if not exists admin_audit_created_idx
  on private.admin_audit_log (created_at desc, id desc);
create index if not exists admin_audit_actor_idx
  on private.admin_audit_log (actor_user_id, created_at desc);
create index if not exists admin_audit_action_idx
  on private.admin_audit_log (action, created_at desc);

create or replace function private.reject_admin_audit_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'admin audit records are immutable' using errcode = '55000';
end;
$$;

revoke all on function private.reject_admin_audit_mutation() from public, anon, authenticated, service_role;

drop trigger if exists admin_audit_log_immutable on private.admin_audit_log;
create trigger admin_audit_log_immutable
before update or delete on private.admin_audit_log
for each row execute function private.reject_admin_audit_mutation();

-- A pre-provisioned Auth user may be tagged through the Admin API with
-- app_metadata.coda_admin_role = "owner" before this migration. Seed exactly
-- that one account when present; otherwise admin_bootstrap_owner() below is the
-- explicit one-time provisioning path after the Auth account is created.
do $$
declare
  v_count integer;
begin
  select count(*) into v_count
  from auth.users
  where raw_app_meta_data ->> 'coda_admin_role' = 'owner'
    and deleted_at is null;

  if v_count > 1 then
    raise exception 'more than one Auth user is tagged coda_admin_role=owner';
  elsif v_count = 1 and not exists (select 1 from private.admin_users) then
    insert into private.admin_users (user_id, role)
    select id, 'owner'
    from auth.users
    where raw_app_meta_data ->> 'coda_admin_role' = 'owner'
      and deleted_at is null;
  end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- Shared private helpers.
-- ---------------------------------------------------------------------------

create or replace function private.normalized_admin_email(p_email text)
returns text
language sql
immutable
set search_path = ''
as $$
  select lower(btrim(coalesce(p_email, '')));
$$;

create or replace function private.require_admin(
  p_token_hash text,
  p_csrf_hash text default null,
  p_allowed_roles text[] default array['owner', 'operator', 'viewer']::text[]
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_session private.admin_sessions;
  v_admin private.admin_users;
  v_auth auth.users;
begin
  if p_token_hash is null or p_token_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid admin session' using errcode = '28000';
  end if;

  select * into v_session
  from private.admin_sessions
  where token_hash = p_token_hash
  for update;

  if not found
     or v_session.revoked_at is not null
     or v_session.idle_expires_at <= now()
     or v_session.absolute_expires_at <= now() then
    raise exception 'invalid admin session' using errcode = '28000';
  end if;

  select * into v_admin
  from private.admin_users
  where user_id = v_session.admin_user_id
    and is_active;

  if not found
     or v_admin.credential_version <> v_session.credential_version
     or not (v_admin.role = any(p_allowed_roles)) then
    raise exception 'admin access denied' using errcode = '42501';
  end if;

  select * into v_auth
  from auth.users
  where id = v_admin.user_id
    and deleted_at is null
    and email_confirmed_at is not null
    and coalesce(encrypted_password, '') <> ''
    and (banned_until is null or banned_until <= now());

  if not found then
    raise exception 'admin access denied' using errcode = '42501';
  end if;

  if p_csrf_hash is not null and
     (p_csrf_hash !~ '^[0-9a-f]{64}$' or p_csrf_hash <> v_session.csrf_hash) then
    raise exception 'admin csrf validation failed' using errcode = '42501';
  end if;

  update private.admin_sessions
  set last_seen_at = now(),
      idle_expires_at = least(now() + interval '30 minutes', absolute_expires_at)
  where id = v_session.id;

  return jsonb_build_object(
    'session_id', v_session.id,
    'user_id', v_admin.user_id,
    'email', v_auth.email,
    'role', v_admin.role,
    'absolute_expires_at', v_session.absolute_expires_at
  );
end;
$$;

create or replace function private.write_admin_audit(
  p_context jsonb,
  p_action text,
  p_target_type text,
  p_target_id text,
  p_before jsonb,
  p_after jsonb,
  p_request_id uuid,
  p_ip inet
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_request_id is null then
    raise exception 'request id is required' using errcode = '22023';
  end if;

  insert into private.admin_audit_log (
    actor_user_id, actor_email, actor_role, action, target_type, target_id,
    before_state, after_state, request_id, ip_address
  ) values (
    (p_context ->> 'user_id')::uuid,
    nullif(p_context ->> 'email', ''),
    p_context ->> 'role',
    p_action,
    p_target_type,
    p_target_id,
    p_before,
    p_after,
    p_request_id,
    p_ip
  );
end;
$$;

revoke all on function private.normalized_admin_email(text) from public, anon, authenticated, service_role;
revoke all on function private.require_admin(text, text, text[]) from public, anon, authenticated, service_role;
revoke all on function private.write_admin_audit(jsonb, text, text, text, jsonb, jsonb, uuid, inet)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Quota overrides preserve the public plan_for_user() composite contract.
-- ---------------------------------------------------------------------------

create or replace function public.plan_for_user(p_user uuid)
returns public.plans
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_plan public.plans;
  v_override private.user_quota_overrides;
begin
  select pl.* into v_plan
  from public.subscriptions s
  join public.plans pl on pl.id = s.plan_id
  where s.user_id = p_user
    and s.status in ('active', 'trialing');

  if not found then
    select * into v_plan from public.plans where id = 'free';
  end if;

  if v_plan.id is null then
    raise exception 'free plan is not configured' using errcode = '55000';
  end if;

  select * into v_override
  from private.user_quota_overrides
  where user_id = p_user;

  if found then
    v_plan.live_seconds_limit := coalesce(v_override.live_seconds_limit, v_plan.live_seconds_limit);
    v_plan.tts_limit := coalesce(v_override.tts_limit, v_plan.tts_limit);
    v_plan.stt_limit := coalesce(v_override.stt_limit, v_plan.stt_limit);
    v_plan.llm_limit := coalesce(v_override.llm_limit, v_plan.llm_limit);
  end if;

  return v_plan;
end;
$$;

revoke all on function public.plan_for_user(uuid) from public, anon, authenticated;
grant execute on function public.plan_for_user(uuid) to service_role;

create or replace function public.refund_quota(
  p_user uuid,
  p_kind text,
  p_amount integer
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_plan public.plans;
  v_period date;
  v_used integer;
  v_refunded integer;
begin
  if p_kind not in ('live', 'tts', 'stt', 'llm') then
    raise exception 'invalid kind %', p_kind using errcode = '22023';
  end if;
  if p_amount is null or p_amount <= 0 then
    return;
  end if;

  v_plan := public.plan_for_user(p_user);
  v_period := public.period_start_for(v_plan.reset_period);

  select case p_kind
           when 'live' then live_seconds_used
           when 'tts' then tts_used
           when 'stt' then stt_used
           when 'llm' then llm_used
         end
  into v_used
  from public.usage_counters
  where user_id = p_user and period_start = v_period
  for update;

  if not found then
    return;
  end if;

  v_refunded := least(v_used, p_amount);
  if v_refunded <= 0 then
    return;
  end if;

  update public.usage_counters
  set live_seconds_used = greatest(0, live_seconds_used - case when p_kind = 'live' then v_refunded else 0 end),
      tts_used = greatest(0, tts_used - case when p_kind = 'tts' then v_refunded else 0 end),
      stt_used = greatest(0, stt_used - case when p_kind = 'stt' then v_refunded else 0 end),
      llm_used = greatest(0, llm_used - case when p_kind = 'llm' then v_refunded else 0 end),
      updated_at = now()
  where user_id = p_user and period_start = v_period;

  insert into public.usage_events (user_id, kind, amount, metadata)
  values (
    p_user,
    p_kind,
    -v_refunded,
    jsonb_build_object('event', 'refund', 'requested_amount', p_amount)
  );
end;
$$;

revoke all on function public.refund_quota(uuid, text, integer) from public, anon, authenticated;
grant execute on function public.refund_quota(uuid, text, integer) to service_role;

-- ---------------------------------------------------------------------------
-- Reliable timestamps and analytics indexes.
-- ---------------------------------------------------------------------------

create or replace function private.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function private.touch_updated_at() from public, anon, authenticated, service_role;

drop trigger if exists demo_bookings_touch_updated_at on public.demo_bookings;
create trigger demo_bookings_touch_updated_at
before update on public.demo_bookings
for each row execute function private.touch_updated_at();

drop trigger if exists admin_users_touch_updated_at on private.admin_users;
create trigger admin_users_touch_updated_at
before update on private.admin_users
for each row execute function private.touch_updated_at();

create index if not exists usage_events_kind_created_user_idx
  on public.usage_events (kind, created_at desc, user_id);
create index if not exists usage_events_created_idx
  on public.usage_events (created_at desc);
create index if not exists demo_bookings_source_created_idx
  on public.demo_bookings (source, created_at desc);
create index if not exists payments_status_fulfilled_idx
  on public.payments (status, fulfilled_at desc)
  where fulfilled_at is not null;
create index if not exists payments_created_status_idx
  on public.payments (created_at desc, status);
create index if not exists subscriptions_plan_status_idx
  on public.subscriptions (plan_id, status);

-- ---------------------------------------------------------------------------
-- Authentication/session RPCs. These remain callable only with the server-side
-- Supabase service-role key. Login errors deliberately do not reveal whether an
-- email belongs to an admin.
-- ---------------------------------------------------------------------------

create or replace function private.login_rate_state(p_email text, p_ip inet)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_pair integer;
  v_ip integer;
begin
  select count(*) into v_pair
  from private.admin_login_attempts
  where email_normalized = p_email
    and ip_address = p_ip
    and succeeded is not true
    and attempted_at >= now() - interval '15 minutes';

  select count(*) into v_ip
  from private.admin_login_attempts
  where ip_address = p_ip
    and succeeded is not true
    and attempted_at >= now() - interval '1 hour';

  return jsonb_build_object(
    'allowed', v_pair < 5 and v_ip < 30,
    'pair_failures', v_pair,
    'ip_failures', v_ip,
    'pair_remaining', greatest(5 - v_pair, 0),
    'ip_remaining', greatest(30 - v_ip, 0),
    'retry_after', 900
  );
end;
$$;

revoke all on function private.login_rate_state(text, inet)
  from public, anon, authenticated, service_role;

create or replace function public.admin_check_login_rate_limit(
  p_email text,
  p_ip inet
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_email text := private.normalized_admin_email(p_email);
  v_pair integer;
  v_ip integer;
  v_attempt_id bigint;
begin
  if v_email = '' or p_ip is null then
    raise exception 'invalid login context' using errcode = '22023';
  end if;

  -- The IP lock serializes all attempts sharing an address; the pair lock also
  -- protects the per-email/IP bucket if the policy is extended later.  Unlike a
  -- read-only check followed by a separate record call, the reservation below
  -- means parallel password guesses cannot all slip through before any fails.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('coda-admin-login-ip:' || p_ip::text, 0)
  );
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('coda-admin-login-pair:' || v_email || ':' || p_ip::text, 0)
  );

  select count(*) into v_pair
  from private.admin_login_attempts
  where email_normalized = v_email
    and ip_address = p_ip
    and succeeded is not true
    and attempted_at >= now() - interval '15 minutes';

  select count(*) into v_ip
  from private.admin_login_attempts
  where ip_address = p_ip
    and succeeded is not true
    and attempted_at >= now() - interval '1 hour';

  if v_pair >= 5 or v_ip >= 30 then
    return private.login_rate_state(v_email, p_ip);
  end if;

  insert into private.admin_login_attempts (email_normalized, ip_address, succeeded)
  values (v_email, p_ip, null)
  returning id into v_attempt_id;

  return private.login_rate_state(v_email, p_ip)
    || jsonb_build_object('allowed', true, 'attempt_id', v_attempt_id);
end;
$$;

create or replace function public.admin_record_login_attempt(
  p_email text,
  p_ip inet,
  p_succeeded boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_email text := private.normalized_admin_email(p_email);
  v_attempt_id bigint;
begin
  if v_email = '' or p_ip is null or p_succeeded is null then
    raise exception 'invalid login context' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('coda-admin-login-ip:' || p_ip::text, 0)
  );
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('coda-admin-login-pair:' || v_email || ':' || p_ip::text, 0)
  );

  select id into v_attempt_id
  from private.admin_login_attempts
  where email_normalized = v_email
    and ip_address = p_ip
    and succeeded is null
    and attempted_at >= now() - interval '15 minutes'
  order by attempted_at, id
  limit 1
  for update;

  if found then
    update private.admin_login_attempts
    set succeeded = p_succeeded, completed_at = now()
    where id = v_attempt_id;
  else
    -- Supports operational/manual callers while the normal login flow always
    -- completes a reservation made by admin_check_login_rate_limit.
    insert into private.admin_login_attempts (
      email_normalized, ip_address, succeeded, completed_at
    ) values (v_email, p_ip, p_succeeded, now());
  end if;

  return private.login_rate_state(v_email, p_ip);
end;
$$;

create or replace function public.admin_bootstrap_owner(p_auth_user uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_auth auth.users;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('coda-admin-owner-bootstrap', 0));

  if exists (select 1 from private.admin_users) then
    raise exception 'admin owner has already been provisioned' using errcode = '42501';
  end if;

  select * into v_auth
  from auth.users
  where id = p_auth_user
    and deleted_at is null
    and email_confirmed_at is not null
    and coalesce(encrypted_password, '') <> ''
    and (banned_until is null or banned_until <= now());

  if not found then
    raise exception 'eligible password Auth user not found' using errcode = '22023';
  end if;

  insert into private.admin_users (user_id, role) values (p_auth_user, 'owner');

  return jsonb_build_object('user_id', p_auth_user, 'email', v_auth.email, 'role', 'owner');
end;
$$;

create or replace function public.admin_create_session(
  p_auth_user uuid,
  p_token_hash text,
  p_csrf_hash text,
  p_ip inet,
  p_user_agent text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_admin private.admin_users;
  v_auth auth.users;
  v_session private.admin_sessions;
begin
  if p_token_hash is null or p_token_hash !~ '^[0-9a-f]{64}$'
     or p_csrf_hash is null or p_csrf_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid session material' using errcode = '22023';
  end if;

  select * into v_admin
  from private.admin_users
  where user_id = p_auth_user and is_active
  for update;

  if not found then
    raise exception 'admin access denied' using errcode = 'P0001';
  end if;

  select * into v_auth
  from auth.users
  where id = p_auth_user
    and deleted_at is null
    and email_confirmed_at is not null
    and coalesce(encrypted_password, '') <> ''
    and (banned_until is null or banned_until <= now());

  if not found then
    raise exception 'admin access denied' using errcode = 'P0001';
  end if;

  update private.admin_sessions
  set revoked_at = now(), revoke_reason = 'superseded'
  where admin_user_id = p_auth_user and revoked_at is null;

  insert into private.admin_sessions (
    admin_user_id, token_hash, csrf_hash, credential_version,
    ip_address, user_agent
  ) values (
    p_auth_user, p_token_hash, p_csrf_hash, v_admin.credential_version,
    p_ip, left(nullif(p_user_agent, ''), 1024)
  ) returning * into v_session;

  return jsonb_build_object(
    'valid', true,
    'admin_id', p_auth_user,
    'auth_user_id', p_auth_user,
    'session_id', v_session.id,
    'email', v_auth.email,
    'role', v_admin.role,
    'idle_expires_at', v_session.idle_expires_at,
    'absolute_expires_at', v_session.absolute_expires_at
  );
end;
$$;

create or replace function public.admin_validate_session(
  p_token_hash text,
  p_touch boolean default true
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_session private.admin_sessions;
  v_admin private.admin_users;
  v_auth auth.users;
begin
  if p_token_hash is null or p_token_hash !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('valid', false);
  end if;

  select * into v_session
  from private.admin_sessions
  where token_hash = p_token_hash
  for update;

  if not found
     or v_session.revoked_at is not null
     or v_session.idle_expires_at <= now()
     or v_session.absolute_expires_at <= now() then
    return jsonb_build_object('valid', false);
  end if;

  select * into v_admin
  from private.admin_users
  where user_id = v_session.admin_user_id and is_active;

  select * into v_auth
  from auth.users
  where id = v_session.admin_user_id
    and deleted_at is null
    and email_confirmed_at is not null
    and coalesce(encrypted_password, '') <> ''
    and (banned_until is null or banned_until <= now());

  if v_admin.user_id is null
     or v_auth.id is null
     or v_admin.credential_version <> v_session.credential_version then
    update private.admin_sessions
    set revoked_at = now(), revoke_reason = 'account_invalidated'
    where id = v_session.id and revoked_at is null;
    return jsonb_build_object('valid', false);
  end if;

  if coalesce(p_touch, true) then
    update private.admin_sessions
    set last_seen_at = now(),
        idle_expires_at = least(now() + interval '30 minutes', absolute_expires_at)
    where id = v_session.id
    returning * into v_session;
  end if;

  return jsonb_build_object(
    'valid', true,
    'admin_id', v_admin.user_id,
    'auth_user_id', v_admin.user_id,
    'session_id', v_session.id,
    'email', v_auth.email,
    'role', v_admin.role,
    'csrf_hash', v_session.csrf_hash,
    'idle_expires_at', v_session.idle_expires_at,
    'absolute_expires_at', v_session.absolute_expires_at
  );
end;
$$;

create or replace function public.admin_revoke_session(
  p_token_hash text,
  p_reason text default 'logout'
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_changed integer;
begin
  if p_token_hash is null or p_token_hash !~ '^[0-9a-f]{64}$' then
    return false;
  end if;

  update private.admin_sessions
  set revoked_at = now(), revoke_reason = left(coalesce(nullif(p_reason, ''), 'logout'), 120)
  where token_hash = p_token_hash and revoked_at is null;

  get diagnostics v_changed = row_count;
  return v_changed > 0;
end;
$$;

-- Out-of-band password resets must call this RPC after changing the Auth
-- password. It revokes every opaque session and advances the version checked on
-- every request.
create or replace function public.admin_invalidate_credentials(p_auth_user uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update private.admin_users
  set credential_version = credential_version + 1
  where user_id = p_auth_user;
  if not found then return false; end if;

  update private.admin_sessions
  set revoked_at = now(), revoke_reason = 'credentials_changed'
  where admin_user_id = p_auth_user and revoked_at is null;
  return true;
end;
$$;

-- ---------------------------------------------------------------------------
-- Transactional admin mutations.
-- ---------------------------------------------------------------------------

create or replace function public.admin_update_plan_limits(
  p_token_hash text,
  p_csrf_hash text,
  p_plan_id text,
  p_agent_minutes integer,
  p_tts_requests integer,
  p_stt_requests integer,
  p_llm_requests integer,
  p_request_id uuid,
  p_ip inet
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_context jsonb;
  v_before public.plans;
  v_after public.plans;
  v_agent_seconds integer;
  v_before_json jsonb;
  v_after_json jsonb;
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  if p_plan_id not in ('free', 'pro') then
    raise exception 'only free and pro plan limits are editable' using errcode = '22023';
  end if;
  if p_agent_minutes is null or p_agent_minutes < 0 or p_agent_minutes > 35791394
     or p_tts_requests is null or p_tts_requests < 0
     or p_stt_requests is null or p_stt_requests < 0
     or p_llm_requests is null or p_llm_requests < 0 then
    raise exception 'limits must be nonnegative integers within range' using errcode = '22023';
  end if;

  v_agent_seconds := (p_agent_minutes::bigint * 60)::integer;

  select * into v_before from public.plans where id = p_plan_id for update;
  if not found then
    raise exception 'plan not found' using errcode = 'P0002';
  end if;

  update public.plans
  set live_seconds_limit = v_agent_seconds,
      tts_limit = p_tts_requests,
      stt_limit = p_stt_requests,
      llm_limit = p_llm_requests
  where id = p_plan_id
  returning * into v_after;

  v_before_json := jsonb_build_object(
    'agentMinutes', v_before.live_seconds_limit / 60,
    'ttsRequests', v_before.tts_limit,
    'sttRequests', v_before.stt_limit,
    'llmRequests', v_before.llm_limit
  );
  v_after_json := jsonb_build_object(
    'agentMinutes', v_after.live_seconds_limit / 60,
    'ttsRequests', v_after.tts_limit,
    'sttRequests', v_after.stt_limit,
    'llmRequests', v_after.llm_limit
  );

  perform private.write_admin_audit(
    v_context, 'plan_limits.updated', 'plan', p_plan_id,
    v_before_json, v_after_json, p_request_id, p_ip
  );

  return jsonb_build_object(
    'id', v_after.id,
    'name', v_after.id,
    'displayName', v_after.name,
    'agentMinutes', v_after.live_seconds_limit / 60,
    'ttsRequests', v_after.tts_limit,
    'sttRequests', v_after.stt_limit,
    'llmRequests', v_after.llm_limit,
    'isActive', v_after.is_active
  );
end;
$$;

create or replace function public.admin_update_user_limits(
  p_token_hash text,
  p_csrf_hash text,
  p_user_id uuid,
  p_agent_minutes integer default null,
  p_tts_requests integer default null,
  p_stt_requests integer default null,
  p_llm_requests integer default null,
  p_request_id uuid default gen_random_uuid(),
  p_ip inet default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_context jsonb;
  v_before private.user_quota_overrides;
  v_after private.user_quota_overrides;
  v_before_json jsonb;
  v_after_json jsonb;
  v_effective public.plans;
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  if p_user_id is null or not exists (select 1 from auth.users where id = p_user_id) then
    raise exception 'user not found' using errcode = 'P0002';
  end if;
  if (p_agent_minutes is not null and (p_agent_minutes < 0 or p_agent_minutes > 35791394))
     or (p_tts_requests is not null and p_tts_requests < 0)
     or (p_stt_requests is not null and p_stt_requests < 0)
     or (p_llm_requests is not null and p_llm_requests < 0) then
    raise exception 'limits must be null or nonnegative integers within range' using errcode = '22023';
  end if;

  select * into v_before
  from private.user_quota_overrides
  where user_id = p_user_id
  for update;

  if p_agent_minutes is null and p_tts_requests is null
     and p_stt_requests is null and p_llm_requests is null then
    delete from private.user_quota_overrides where user_id = p_user_id;
    v_after_json := null;
  else
    insert into private.user_quota_overrides (
      user_id, live_seconds_limit, tts_limit, stt_limit, llm_limit, updated_by
    ) values (
      p_user_id,
      case when p_agent_minutes is null then null else (p_agent_minutes::bigint * 60)::integer end,
      p_tts_requests, p_stt_requests, p_llm_requests,
      (v_context ->> 'user_id')::uuid
    )
    on conflict (user_id) do update
    set live_seconds_limit = excluded.live_seconds_limit,
        tts_limit = excluded.tts_limit,
        stt_limit = excluded.stt_limit,
        llm_limit = excluded.llm_limit,
        updated_by = excluded.updated_by,
        updated_at = now()
    returning * into v_after;

    v_after_json := jsonb_build_object(
      'agentMinutes', case when v_after.live_seconds_limit is null then null else v_after.live_seconds_limit / 60 end,
      'ttsRequests', v_after.tts_limit,
      'sttRequests', v_after.stt_limit,
      'llmRequests', v_after.llm_limit
    );
  end if;

  if v_before.user_id is not null then
    v_before_json := jsonb_build_object(
      'agentMinutes', case when v_before.live_seconds_limit is null then null else v_before.live_seconds_limit / 60 end,
      'ttsRequests', v_before.tts_limit,
      'sttRequests', v_before.stt_limit,
      'llmRequests', v_before.llm_limit
    );
  end if;

  v_effective := public.plan_for_user(p_user_id);

  perform private.write_admin_audit(
    v_context, 'user_limits.updated', 'user', p_user_id::text,
    v_before_json, v_after_json, p_request_id, p_ip
  );

  return jsonb_build_object(
    'userId', p_user_id,
    'overrides', v_after_json,
    'effectiveLimits', jsonb_build_object(
      'agentMinutes', v_effective.live_seconds_limit / 60,
      'ttsRequests', v_effective.tts_limit,
      'sttRequests', v_effective.stt_limit,
      'llmRequests', v_effective.llm_limit
    )
  );
end;
$$;

create or replace function public.admin_update_booking_status(
  p_token_hash text,
  p_csrf_hash text,
  p_booking_id uuid,
  p_status text,
  p_request_id uuid,
  p_ip inet
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_context jsonb;
  v_before public.demo_bookings;
  v_after public.demo_bookings;
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  if p_status not in ('new', 'contacted', 'scheduled', 'done', 'canceled') then
    raise exception 'invalid booking status' using errcode = '22023';
  end if;

  select * into v_before
  from public.demo_bookings
  where id = p_booking_id
  for update;
  if not found then
    raise exception 'booking not found' using errcode = 'P0002';
  end if;

  update public.demo_bookings
  set status = p_status
  where id = p_booking_id
  returning * into v_after;

  perform private.write_admin_audit(
    v_context, 'booking_status.updated', 'demo_booking', p_booking_id::text,
    jsonb_build_object('status', v_before.status),
    jsonb_build_object('status', v_after.status),
    p_request_id, p_ip
  );

  return jsonb_build_object(
    'id', v_after.id,
    'userId', v_after.user_id,
    'name', v_after.name,
    'email', v_after.email,
    'phone', v_after.phone,
    'company', v_after.company,
    'jobTitle', v_after.metadata ->> 'job_title',
    'preferredDate', v_after.preferred_date,
    'preferredTime', v_after.preferred_time,
    'message', v_after.message,
    'source', v_after.source,
    'status', v_after.status,
    'createdAt', v_after.created_at,
    'updatedAt', v_after.updated_at
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- Read RPCs. JSON keys intentionally match the dashboard's TypeScript models;
-- timestamps are emitted by jsonb as ISO-8601 strings through PostgREST.
-- ---------------------------------------------------------------------------

create or replace function private.percent_change(p_current numeric, p_previous numeric)
returns numeric
language sql
immutable
set search_path = ''
as $$
  select case
    when coalesce(p_previous, 0) = 0 then
      case when coalesce(p_current, 0) = 0 then 0 else 100 end
    else round(((p_current - p_previous) * 100.0) / p_previous, 1)
  end;
$$;

revoke all on function private.percent_change(numeric, numeric)
  from public, anon, authenticated, service_role;

create or replace function public.admin_list_plans(p_token_hash text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
begin
  perform private.require_admin(p_token_hash);

  select jsonb_build_object(
    'items', coalesce(jsonb_agg(
      jsonb_build_object(
        'id', p.id,
        'name', p.id,
        'displayName', p.name,
        'agentMinutes', p.live_seconds_limit / 60,
        'ttsRequests', p.tts_limit,
        'sttRequests', p.stt_limit,
        'llmRequests', p.llm_limit,
        'isActive', p.is_active
      ) order by p.sort_order, p.id
    ), '[]'::jsonb),
    'total', count(*)
  ) into v_result
  from public.plans p
  where p.id in ('free', 'pro');

  return v_result;
end;
$$;

create or replace function public.admin_list_users(
  p_token_hash text,
  p_search text default null,
  p_plan text default null,
  p_status text default null,
  p_sort text default 'created_at',
  p_direction text default 'desc',
  p_page integer default 1,
  p_page_size integer default 25
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
  v_search text := lower(btrim(coalesce(p_search, '')));
  v_offset integer;
begin
  perform private.require_admin(p_token_hash);

  if p_page is null or p_page < 1 or p_page_size is null or p_page_size < 1 or p_page_size > 100 then
    raise exception 'invalid pagination' using errcode = '22023';
  end if;
  if p_direction not in ('asc', 'desc') then
    raise exception 'invalid sort direction' using errcode = '22023';
  end if;
  if p_sort not in ('created_at', 'last_sign_in_at', 'last_active_at', 'email', 'name', 'plan', 'usage') then
    raise exception 'invalid user sort' using errcode = '22023';
  end if;
  if p_status is not null and p_status not in ('confirmed', 'unconfirmed', 'banned', 'deleted') then
    raise exception 'invalid user status filter' using errcode = '22023';
  end if;

  v_offset := (p_page - 1) * p_page_size;

  with base as materialized (
    select
      u.id,
      coalesce(u.email, '') as email,
      coalesce(pr.full_name, u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name') as name,
      coalesce(pr.avatar_url, u.raw_user_meta_data ->> 'avatar_url') as avatar_url,
      coalesce(
        u.raw_app_meta_data ->> 'provider',
        (select min(i.provider) from auth.identities i where i.user_id = u.id),
        'email'
      ) as provider,
      (u.confirmed_at is not null) as confirmed,
      u.created_at,
      u.last_sign_in_at,
      activity.last_active_at,
      pl.id as plan,
      coalesce(c.live_seconds_used, 0) as live_seconds_used,
      pl.live_seconds_limit,
      coalesce(c.tts_used, 0) as tts_used,
      pl.tts_limit,
      coalesce(c.stt_used, 0) as stt_used,
      pl.stt_limit,
      coalesce(c.llm_used, 0) as llm_used,
      pl.llm_limit,
      coalesce(c.live_seconds_used, 0) + coalesce(c.tts_used, 0)
        + coalesce(c.stt_used, 0) + coalesce(c.llm_used, 0) as usage_score,
      coalesce(cb.balance, 0) as credits,
      u.banned_until,
      u.deleted_at
    from auth.users u
    left join public.profiles pr on pr.id = u.id
    cross join lateral public.plan_for_user(u.id) pl
    left join public.usage_counters c
      on c.user_id = u.id
     and c.period_start = public.period_start_for(pl.reset_period)
    left join public.credit_balances cb on cb.user_id = u.id
    left join lateral (
      select max(e.created_at) as last_active_at
      from public.usage_events e
      where e.user_id = u.id
    ) activity on true
    where (
      v_search = ''
      or strpos(lower(coalesce(u.email, '')), v_search) > 0
      or strpos(lower(coalesce(pr.full_name, '')), v_search) > 0
      or strpos(lower(coalesce(pr.company, '')), v_search) > 0
      or strpos(lower(coalesce(pr.phone, u.phone, '')), v_search) > 0
    )
    and (p_plan is null or pl.id = p_plan)
    and (
      p_status is null
      or (p_status = 'confirmed' and u.confirmed_at is not null and u.deleted_at is null)
      or (p_status = 'unconfirmed' and u.confirmed_at is null and u.deleted_at is null)
      or (p_status = 'banned' and u.banned_until > now() and u.deleted_at is null)
      or (p_status = 'deleted' and u.deleted_at is not null)
    )
  ), page_rows as (
    select *
    from base
    order by
      case when p_sort = 'created_at' and p_direction = 'asc' then created_at end asc nulls last,
      case when p_sort = 'created_at' and p_direction = 'desc' then created_at end desc nulls last,
      case when p_sort = 'last_sign_in_at' and p_direction = 'asc' then last_sign_in_at end asc nulls last,
      case when p_sort = 'last_sign_in_at' and p_direction = 'desc' then last_sign_in_at end desc nulls last,
      case when p_sort = 'last_active_at' and p_direction = 'asc' then last_active_at end asc nulls last,
      case when p_sort = 'last_active_at' and p_direction = 'desc' then last_active_at end desc nulls last,
      case when p_sort = 'email' and p_direction = 'asc' then lower(email) end asc nulls last,
      case when p_sort = 'email' and p_direction = 'desc' then lower(email) end desc nulls last,
      case when p_sort = 'name' and p_direction = 'asc' then lower(name) end asc nulls last,
      case when p_sort = 'name' and p_direction = 'desc' then lower(name) end desc nulls last,
      case when p_sort = 'plan' and p_direction = 'asc' then plan end asc nulls last,
      case when p_sort = 'plan' and p_direction = 'desc' then plan end desc nulls last,
      case when p_sort = 'usage' and p_direction = 'asc' then usage_score end asc nulls last,
      case when p_sort = 'usage' and p_direction = 'desc' then usage_score end desc nulls last,
      id asc
    offset v_offset limit p_page_size
  )
  select jsonb_build_object(
    'items', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', r.id,
        'email', r.email,
        'name', r.name,
        'avatarUrl', r.avatar_url,
        'provider', r.provider,
        'confirmed', r.confirmed,
        'createdAt', r.created_at,
        'lastSignInAt', r.last_sign_in_at,
        'lastActiveAt', r.last_active_at,
        'plan', r.plan,
        'agentUsedMinutes', round(r.live_seconds_used::numeric / 60.0, 2),
        'agentLimitMinutes', r.live_seconds_limit / 60,
        'ttsUsed', r.tts_used,
        'ttsLimit', r.tts_limit,
        'sttUsed', r.stt_used,
        'sttLimit', r.stt_limit,
        'llmUsed', r.llm_used,
        'llmLimit', r.llm_limit,
        'credits', r.credits
      ) order by
        case when p_sort = 'created_at' and p_direction = 'asc' then r.created_at end asc nulls last,
        case when p_sort = 'created_at' and p_direction = 'desc' then r.created_at end desc nulls last,
        case when p_sort = 'last_sign_in_at' and p_direction = 'asc' then r.last_sign_in_at end asc nulls last,
        case when p_sort = 'last_sign_in_at' and p_direction = 'desc' then r.last_sign_in_at end desc nulls last,
        case when p_sort = 'last_active_at' and p_direction = 'asc' then r.last_active_at end asc nulls last,
        case when p_sort = 'last_active_at' and p_direction = 'desc' then r.last_active_at end desc nulls last,
        case when p_sort = 'email' and p_direction = 'asc' then lower(r.email) end asc nulls last,
        case when p_sort = 'email' and p_direction = 'desc' then lower(r.email) end desc nulls last,
        case when p_sort = 'name' and p_direction = 'asc' then lower(r.name) end asc nulls last,
        case when p_sort = 'name' and p_direction = 'desc' then lower(r.name) end desc nulls last,
        case when p_sort = 'plan' and p_direction = 'asc' then r.plan end asc nulls last,
        case when p_sort = 'plan' and p_direction = 'desc' then r.plan end desc nulls last,
        case when p_sort = 'usage' and p_direction = 'asc' then r.usage_score end asc nulls last,
        case when p_sort = 'usage' and p_direction = 'desc' then r.usage_score end desc nulls last,
        r.id asc
      ) from page_rows r
    ), '[]'::jsonb),
    'total', (select count(*) from base),
    'page', p_page,
    'pageSize', p_page_size
  ) into v_result;

  return v_result;
end;
$$;

create or replace function public.admin_get_user(
  p_token_hash text,
  p_user_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
begin
  perform private.require_admin(p_token_hash);

  with target as materialized (
    select
      u.id,
      coalesce(u.email, '') as email,
      coalesce(pr.full_name, u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name') as name,
      coalesce(pr.avatar_url, u.raw_user_meta_data ->> 'avatar_url') as avatar_url,
      coalesce(
        u.raw_app_meta_data ->> 'provider',
        (select min(i.provider) from auth.identities i where i.user_id = u.id),
        'email'
      ) as provider,
      (u.confirmed_at is not null) as confirmed,
      u.created_at,
      u.last_sign_in_at,
      activity.last_active_at,
      pl.id as plan,
      pl.reset_period,
      public.period_start_for(pl.reset_period) as period_start,
      coalesce(c.live_seconds_used, 0) as live_seconds_used,
      pl.live_seconds_limit,
      coalesce(c.tts_used, 0) as tts_used,
      pl.tts_limit,
      coalesce(c.stt_used, 0) as stt_used,
      pl.stt_limit,
      coalesce(c.llm_used, 0) as llm_used,
      pl.llm_limit,
      coalesce(cb.balance, 0) as credits,
      o.live_seconds_limit as override_live,
      o.tts_limit as override_tts,
      o.stt_limit as override_stt,
      o.llm_limit as override_llm
    from auth.users u
    left join public.profiles pr on pr.id = u.id
    cross join lateral public.plan_for_user(u.id) pl
    left join public.usage_counters c
      on c.user_id = u.id
     and c.period_start = public.period_start_for(pl.reset_period)
    left join public.credit_balances cb on cb.user_id = u.id
    left join private.user_quota_overrides o on o.user_id = u.id
    left join lateral (
      select max(e.created_at) as last_active_at
      from public.usage_events e where e.user_id = u.id
    ) activity on true
    where u.id = p_user_id
  ), recent as (
    select
      gs.bucket::date as bucket,
      coalesce(sum(e.amount) filter (where e.kind = 'live'), 0) as live_seconds,
      coalesce(sum(e.amount) filter (where e.kind = 'tts'), 0) as tts,
      coalesce(sum(e.amount) filter (where e.kind = 'stt'), 0) as stt,
      coalesce(sum(e.amount) filter (where e.kind = 'llm'), 0) as llm
    from generate_series(
      date_trunc('day', now() at time zone 'Africa/Cairo') - interval '29 days',
      date_trunc('day', now() at time zone 'Africa/Cairo'),
      interval '1 day'
    ) gs(bucket)
    left join public.usage_events e
      on e.user_id = p_user_id
     and date_trunc('day', e.created_at at time zone 'Africa/Cairo') = gs.bucket
    group by gs.bucket
    order by gs.bucket
  )
  select jsonb_build_object(
    'user', jsonb_build_object(
      'id', t.id,
      'email', t.email,
      'name', t.name,
      'avatarUrl', t.avatar_url,
      'provider', t.provider,
      'confirmed', t.confirmed,
      'createdAt', t.created_at,
      'lastSignInAt', t.last_sign_in_at,
      'lastActiveAt', t.last_active_at,
      'plan', t.plan,
      'agentUsedMinutes', round(t.live_seconds_used::numeric / 60.0, 2),
      'agentLimitMinutes', t.live_seconds_limit / 60,
      'ttsUsed', t.tts_used,
      'ttsLimit', t.tts_limit,
      'sttUsed', t.stt_used,
      'sttLimit', t.stt_limit,
      'llmUsed', t.llm_used,
      'llmLimit', t.llm_limit,
      'credits', t.credits
    ),
    'quota', jsonb_build_object(
      'agentUsedMinutes', round(t.live_seconds_used::numeric / 60.0, 2),
      'agentLimitMinutes', t.live_seconds_limit / 60,
      'ttsUsed', t.tts_used,
      'ttsLimit', t.tts_limit,
      'sttUsed', t.stt_used,
      'sttLimit', t.stt_limit,
      'llmUsed', t.llm_used,
      'llmLimit', t.llm_limit,
      'periodStart', t.period_start,
      'periodEnd', case t.reset_period
        when 'day' then t.period_start + 1
        when 'none' then null
        else (t.period_start + interval '1 month')::date
      end
    ),
    'override', jsonb_build_object(
      'agentMinutes', case when t.override_live is null then null else t.override_live / 60 end,
      'ttsRequests', t.override_tts,
      'sttRequests', t.override_stt,
      'llmRequests', t.override_llm
    ),
    'credits', t.credits,
    'recentUsage', coalesce((select jsonb_agg(jsonb_build_object(
      'bucket', r.bucket,
      'agentMinutes', round(r.live_seconds::numeric / 60.0, 2),
      'tts', r.tts,
      'stt', r.stt,
      'llm', r.llm
    ) order by r.bucket) from recent r), '[]'::jsonb)
  ) into v_result
  from target t;

  if v_result is null then
    raise exception 'user not found' using errcode = 'P0002';
  end if;
  return v_result;
end;
$$;

create or replace function public.admin_list_bookings(
  p_token_hash text,
  p_search text default null,
  p_status text default null,
  p_source text default null,
  p_sort text default 'created_at',
  p_direction text default 'desc',
  p_page integer default 1,
  p_page_size integer default 25
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
  v_search text := lower(btrim(coalesce(p_search, '')));
  v_offset integer;
begin
  perform private.require_admin(p_token_hash);

  if p_page is null or p_page < 1 or p_page_size is null or p_page_size < 1 or p_page_size > 100 then
    raise exception 'invalid pagination' using errcode = '22023';
  end if;
  if p_direction not in ('asc', 'desc') or p_sort not in ('created_at', 'updated_at', 'name', 'email', 'status') then
    raise exception 'invalid booking sort' using errcode = '22023';
  end if;
  if p_status is not null and p_status not in ('new', 'contacted', 'scheduled', 'done', 'canceled') then
    raise exception 'invalid booking status' using errcode = '22023';
  end if;
  if p_source is not null and p_source not in ('landing', 'dashboard') then
    raise exception 'invalid booking source' using errcode = '22023';
  end if;

  v_offset := (p_page - 1) * p_page_size;

  with base as materialized (
    select b.*
    from public.demo_bookings b
    where (
      v_search = ''
      or strpos(lower(b.name), v_search) > 0
      or strpos(lower(b.email), v_search) > 0
      or strpos(lower(b.phone), v_search) > 0
      or strpos(lower(coalesce(b.company, '')), v_search) > 0
    )
      and (p_status is null or b.status = p_status)
      and (p_source is null or b.source = p_source)
  ), page_rows as (
    select * from base
    order by
      case when p_sort = 'created_at' and p_direction = 'asc' then created_at end asc nulls last,
      case when p_sort = 'created_at' and p_direction = 'desc' then created_at end desc nulls last,
      case when p_sort = 'updated_at' and p_direction = 'asc' then updated_at end asc nulls last,
      case when p_sort = 'updated_at' and p_direction = 'desc' then updated_at end desc nulls last,
      case when p_sort = 'name' and p_direction = 'asc' then lower(name) end asc nulls last,
      case when p_sort = 'name' and p_direction = 'desc' then lower(name) end desc nulls last,
      case when p_sort = 'email' and p_direction = 'asc' then lower(email) end asc nulls last,
      case when p_sort = 'email' and p_direction = 'desc' then lower(email) end desc nulls last,
      case when p_sort = 'status' and p_direction = 'asc' then status end asc nulls last,
      case when p_sort = 'status' and p_direction = 'desc' then status end desc nulls last,
      id asc
    offset v_offset limit p_page_size
  )
  select jsonb_build_object(
    'items', coalesce((select jsonb_agg(jsonb_build_object(
      'id', r.id,
      'userId', r.user_id,
      'name', r.name,
      'email', r.email,
      'phone', r.phone,
      'company', r.company,
      'jobTitle', r.metadata ->> 'job_title',
      'preferredDate', r.preferred_date,
      'preferredTime', r.preferred_time,
      'message', r.message,
      'source', r.source,
      'status', r.status,
      'createdAt', r.created_at,
      'updatedAt', r.updated_at
    ) order by
      case when p_sort = 'created_at' and p_direction = 'asc' then r.created_at end asc nulls last,
      case when p_sort = 'created_at' and p_direction = 'desc' then r.created_at end desc nulls last,
      case when p_sort = 'updated_at' and p_direction = 'asc' then r.updated_at end asc nulls last,
      case when p_sort = 'updated_at' and p_direction = 'desc' then r.updated_at end desc nulls last,
      case when p_sort = 'name' and p_direction = 'asc' then lower(r.name) end asc nulls last,
      case when p_sort = 'name' and p_direction = 'desc' then lower(r.name) end desc nulls last,
      case when p_sort = 'email' and p_direction = 'asc' then lower(r.email) end asc nulls last,
      case when p_sort = 'email' and p_direction = 'desc' then lower(r.email) end desc nulls last,
      case when p_sort = 'status' and p_direction = 'asc' then r.status end asc nulls last,
      case when p_sort = 'status' and p_direction = 'desc' then r.status end desc nulls last,
      r.id asc
    ) from page_rows r), '[]'::jsonb),
    'total', (select count(*) from base),
    'page', p_page,
    'pageSize', p_page_size
  ) into v_result;

  return v_result;
end;
$$;

create or replace function public.admin_list_audit(
  p_token_hash text,
  p_search text default null,
  p_action text default null,
  p_actor text default null,
  p_page integer default 1,
  p_page_size integer default 25
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
  v_search text := lower(btrim(coalesce(p_search, '')));
  v_actor text := lower(btrim(coalesce(p_actor, '')));
  v_offset integer;
begin
  perform private.require_admin(p_token_hash);

  if p_page is null or p_page < 1 or p_page_size is null or p_page_size < 1 or p_page_size > 100 then
    raise exception 'invalid pagination' using errcode = '22023';
  end if;
  v_offset := (p_page - 1) * p_page_size;

  with base as materialized (
    select a.*
    from private.admin_audit_log a
    where (p_action is null or a.action = p_action)
      and (
        v_search = ''
        or strpos(lower(coalesce(a.actor_email, '')), v_search) > 0
        or strpos(lower(a.action), v_search) > 0
        or strpos(lower(a.target_type), v_search) > 0
        or strpos(lower(a.target_id), v_search) > 0
      )
      and (
        v_actor = ''
        or a.actor_user_id::text = v_actor
        or strpos(lower(coalesce(a.actor_email, '')), v_actor) > 0
      )
  ), page_rows as (
    select * from base order by created_at desc, id desc
    offset v_offset limit p_page_size
  )
  select jsonb_build_object(
    'items', coalesce((select jsonb_agg(jsonb_build_object(
      'id', r.id::text,
      'actorEmail', coalesce(r.actor_email, ''),
      'actorRole', r.actor_role,
      'action', r.action,
      'targetType', r.target_type,
      'targetId', r.target_id,
      'before', r.before_state,
      'after', r.after_state,
      'ipAddress', r.ip_address::text,
      'createdAt', r.created_at
    ) order by r.created_at desc, r.id desc) from page_rows r), '[]'::jsonb),
    'total', (select count(*) from base),
    'page', p_page,
    'pageSize', p_page_size
  ) into v_result;

  return v_result;
end;
$$;

create or replace function public.admin_overview(
  p_token_hash text,
  p_range text default 'month'
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_now timestamptz := now();
  v_start timestamptz;
  v_previous_start timestamptz;
  v_bucket text;
  v_step interval;
  v_total_users bigint;
  v_previous_total_users bigint;
  v_new_users bigint;
  v_previous_new_users bigint;
  v_active_users bigint;
  v_previous_active_users bigint;
  v_pro_users bigint;
  v_revenue numeric;
  v_previous_revenue numeric;
  v_bookings bigint;
  v_previous_bookings bigint;
  v_growth jsonb;
  v_agent_usage jsonb;
  v_request_usage jsonb;
  v_plan_distribution jsonb;
  v_quota_health jsonb;
  v_top_users jsonb;
  v_booking_status jsonb;
  v_booking_sources jsonb;
  v_booking_trend jsonb;
  v_payment_status jsonb;
begin
  perform private.require_admin(p_token_hash);

  case p_range
    when 'week' then
      v_start := v_now - interval '7 days';
      v_previous_start := v_now - interval '14 days';
      v_bucket := 'day';
      v_step := interval '1 day';
    when 'month' then
      v_start := v_now - interval '30 days';
      v_previous_start := v_now - interval '60 days';
      v_bucket := 'day';
      v_step := interval '1 day';
    when 'year' then
      v_start := v_now - interval '12 months';
      v_previous_start := v_now - interval '24 months';
      v_bucket := 'month';
      v_step := interval '1 month';
    else
      raise exception 'invalid overview range' using errcode = '22023';
  end case;

  select count(*), count(*) filter (where created_at < v_start),
         count(*) filter (where created_at >= v_start and created_at < v_now),
         count(*) filter (where created_at >= v_previous_start and created_at < v_start)
  into v_total_users, v_previous_total_users, v_new_users, v_previous_new_users
  from auth.users;

  select count(distinct user_id) filter (where created_at >= v_start and created_at < v_now),
         count(distinct user_id) filter (where created_at >= v_previous_start and created_at < v_start)
  into v_active_users, v_previous_active_users
  from public.usage_events;

  select count(*) into v_pro_users
  from auth.users u
  cross join lateral public.plan_for_user(u.id) pl
  where pl.id = 'pro';

  select
    coalesce(sum(amount) filter (
      where status = 'paid' and fulfilled_at >= v_start and fulfilled_at < v_now
    ), 0)::numeric / 100.0,
    coalesce(sum(amount) filter (
      where status = 'paid' and fulfilled_at >= v_previous_start and fulfilled_at < v_start
    ), 0)::numeric / 100.0
  into v_revenue, v_previous_revenue
  from public.payments
  where fulfilled_at is not null;

  select count(*) filter (where created_at >= v_start and created_at < v_now),
         count(*) filter (where created_at >= v_previous_start and created_at < v_start)
  into v_bookings, v_previous_bookings
  from public.demo_bookings;

  with buckets as (
    select generate_series(
      date_trunc(v_bucket, v_start at time zone 'Africa/Cairo'),
      date_trunc(v_bucket, (v_now - interval '1 microsecond') at time zone 'Africa/Cairo'),
      v_step
    ) as bucket
  ), values_by_bucket as (
    select b.bucket, count(u.id) as users
    from buckets b
    left join auth.users u
      on u.created_at >= v_start and u.created_at < v_now
     and date_trunc(v_bucket, u.created_at at time zone 'Africa/Cairo') = b.bucket
    group by b.bucket
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'bucket', case when v_bucket = 'month' then to_char(bucket, 'YYYY-MM') else bucket::date::text end,
    'label', case when v_bucket = 'month' then to_char(bucket, 'Mon YYYY') else to_char(bucket, 'Mon DD') end,
    'users', users
  ) order by bucket), '[]'::jsonb) into v_growth
  from values_by_bucket;

  with buckets as (
    select generate_series(
      date_trunc(v_bucket, v_start at time zone 'Africa/Cairo'),
      date_trunc(v_bucket, (v_now - interval '1 microsecond') at time zone 'Africa/Cairo'),
      v_step
    ) as bucket
  ), values_by_bucket as (
    select b.bucket,
      coalesce(sum(e.amount) filter (where e.kind = 'live'), 0) as live_seconds,
      coalesce(sum(e.amount) filter (where e.kind = 'tts'), 0) as tts,
      coalesce(sum(e.amount) filter (where e.kind = 'stt'), 0) as stt,
      coalesce(sum(e.amount) filter (where e.kind = 'llm'), 0) as llm
    from buckets b
    left join public.usage_events e
      on e.created_at >= v_start and e.created_at < v_now
     and date_trunc(v_bucket, e.created_at at time zone 'Africa/Cairo') = b.bucket
    group by b.bucket
  )
  select
    coalesce(jsonb_agg(jsonb_build_object(
      'bucket', case when v_bucket = 'month' then to_char(bucket, 'YYYY-MM') else bucket::date::text end,
      'label', case when v_bucket = 'month' then to_char(bucket, 'Mon YYYY') else to_char(bucket, 'Mon DD') end,
      'minutes', round(live_seconds::numeric / 60.0, 2)
    ) order by bucket), '[]'::jsonb),
    coalesce(jsonb_agg(jsonb_build_object(
      'bucket', case when v_bucket = 'month' then to_char(bucket, 'YYYY-MM') else bucket::date::text end,
      'label', case when v_bucket = 'month' then to_char(bucket, 'Mon YYYY') else to_char(bucket, 'Mon DD') end,
      'tts', tts, 'stt', stt, 'llm', llm
    ) order by bucket), '[]'::jsonb)
  into v_agent_usage, v_request_usage
  from values_by_bucket;

  select coalesce(jsonb_agg(jsonb_build_object('name', plan, 'value', users) order by plan), '[]'::jsonb)
  into v_plan_distribution
  from (
    select pl.id as plan, count(*) as users
    from auth.users u
    cross join lateral public.plan_for_user(u.id) pl
    group by pl.id
  ) distribution;

  with quota as (
    select greatest(
      case when pl.live_seconds_limit = 0 then 1.0 else coalesce(c.live_seconds_used, 0)::numeric / pl.live_seconds_limit end,
      case when pl.tts_limit = 0 then 1.0 else coalesce(c.tts_used, 0)::numeric / pl.tts_limit end,
      case when pl.stt_limit = 0 then 1.0 else coalesce(c.stt_used, 0)::numeric / pl.stt_limit end,
      case when pl.llm_limit = 0 then 1.0 else coalesce(c.llm_used, 0)::numeric / pl.llm_limit end
    ) as utilization
    from auth.users u
    cross join lateral public.plan_for_user(u.id) pl
    left join public.usage_counters c
      on c.user_id = u.id and c.period_start = public.period_start_for(pl.reset_period)
  )
  select jsonb_build_object(
    'healthy', count(*) filter (where utilization < 0.8),
    'warning', count(*) filter (where utilization >= 0.8 and utilization < 1),
    'exhausted', count(*) filter (where utilization >= 1)
  ) into v_quota_health
  from quota;

  with usage_totals as (
    select e.user_id,
      coalesce(sum(e.amount) filter (where e.kind = 'live'), 0) as live_seconds,
      coalesce(sum(e.amount) filter (where e.kind in ('tts', 'stt', 'llm')), 0) as requests
    from public.usage_events e
    where e.created_at >= v_start and e.created_at < v_now
    group by e.user_id
  ), ranked as (
    select u.id, coalesce(u.email, '') as email,
      coalesce(pr.full_name, u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name') as name,
      pl.id as plan, greatest(t.live_seconds, 0) as live_seconds,
      greatest(t.requests, 0) as requests
    from usage_totals t
    join auth.users u on u.id = t.user_id
    left join public.profiles pr on pr.id = u.id
    cross join lateral public.plan_for_user(u.id) pl
    order by (greatest(t.live_seconds, 0) + greatest(t.requests, 0)) desc, u.id
    limit 10
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', id, 'email', email, 'name', name, 'plan', plan,
    'agentMinutes', round(live_seconds::numeric / 60.0, 2),
    'requests', requests
  ) order by (live_seconds + requests) desc, id), '[]'::jsonb)
  into v_top_users from ranked;

  select coalesce(jsonb_agg(jsonb_build_object('name', status, 'value', total) order by status), '[]'::jsonb)
  into v_booking_status
  from (
    select status, count(*) as total
    from public.demo_bookings
    where created_at >= v_start and created_at < v_now
    group by status
  ) x;

  select coalesce(jsonb_agg(jsonb_build_object('name', source, 'value', total) order by source), '[]'::jsonb)
  into v_booking_sources
  from (
    select source, count(*) as total
    from public.demo_bookings
    where created_at >= v_start and created_at < v_now
    group by source
  ) x;

  with buckets as (
    select generate_series(
      date_trunc(v_bucket, v_start at time zone 'Africa/Cairo'),
      date_trunc(v_bucket, (v_now - interval '1 microsecond') at time zone 'Africa/Cairo'),
      v_step
    ) as bucket
  ), values_by_bucket as (
    select b.bucket, count(d.id) as bookings
    from buckets b
    left join public.demo_bookings d
      on d.created_at >= v_start and d.created_at < v_now
     and date_trunc(v_bucket, d.created_at at time zone 'Africa/Cairo') = b.bucket
    group by b.bucket
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'bucket', case when v_bucket = 'month' then to_char(bucket, 'YYYY-MM') else bucket::date::text end,
    'label', case when v_bucket = 'month' then to_char(bucket, 'Mon YYYY') else to_char(bucket, 'Mon DD') end,
    'bookings', bookings
  ) order by bucket), '[]'::jsonb)
  into v_booking_trend
  from values_by_bucket;

  select coalesce(jsonb_agg(jsonb_build_object('name', status, 'value', total) order by status), '[]'::jsonb)
  into v_payment_status
  from (
    select status, count(*) as total
    from public.payments
    where created_at >= v_start and created_at < v_now
    group by status
  ) x;

  return jsonb_build_object(
    'summary', jsonb_build_object(
      'totalUsers', v_total_users,
      'newUsers', v_new_users,
      'activeUsers', v_active_users,
      'proUsers', v_pro_users,
      'proAdoptionRate', case when v_total_users = 0 then 0
        else round(v_pro_users::numeric * 100.0 / v_total_users, 1) end,
      'revenue', v_revenue,
      'bookings', v_bookings,
      'comparisons', jsonb_build_object(
        'totalUsers', private.percent_change(v_total_users, v_previous_total_users),
        'newUsers', private.percent_change(v_new_users, v_previous_new_users),
        'activeUsers', private.percent_change(v_active_users, v_previous_active_users),
        'revenue', private.percent_change(v_revenue, v_previous_revenue),
        'bookings', private.percent_change(v_bookings, v_previous_bookings)
      )
    ),
    'growth', v_growth,
    'agentUsage', v_agent_usage,
    'requestUsage', v_request_usage,
    'planDistribution', v_plan_distribution,
    'quotaHealth', v_quota_health,
    'topUsers', v_top_users,
    'bookingStatus', v_booking_status,
    'bookingSources', v_booking_sources,
    'bookingTrend', v_booking_trend,
    'paymentStatus', v_payment_status
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- Final privilege boundary. Keep this exhaustive: CREATE FUNCTION grants
-- EXECUTE to PUBLIC unless explicitly revoked.
-- ---------------------------------------------------------------------------

revoke all on function public.admin_check_login_rate_limit(text, inet)
  from public, anon, authenticated;
revoke all on function public.admin_record_login_attempt(text, inet, boolean)
  from public, anon, authenticated;
revoke all on function public.admin_bootstrap_owner(uuid)
  from public, anon, authenticated;
revoke all on function public.admin_create_session(uuid, text, text, inet, text)
  from public, anon, authenticated;
revoke all on function public.admin_validate_session(text, boolean)
  from public, anon, authenticated;
revoke all on function public.admin_revoke_session(text, text)
  from public, anon, authenticated;
revoke all on function public.admin_invalidate_credentials(uuid)
  from public, anon, authenticated;
revoke all on function public.admin_update_plan_limits(text, text, text, integer, integer, integer, integer, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.admin_update_user_limits(text, text, uuid, integer, integer, integer, integer, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.admin_update_booking_status(text, text, uuid, text, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.admin_overview(text, text)
  from public, anon, authenticated;
revoke all on function public.admin_list_users(text, text, text, text, text, text, integer, integer)
  from public, anon, authenticated;
revoke all on function public.admin_get_user(text, uuid)
  from public, anon, authenticated;
revoke all on function public.admin_list_plans(text)
  from public, anon, authenticated;
revoke all on function public.admin_list_bookings(text, text, text, text, text, text, integer, integer)
  from public, anon, authenticated;
revoke all on function public.admin_list_audit(text, text, text, text, integer, integer)
  from public, anon, authenticated;

grant execute on function public.admin_check_login_rate_limit(text, inet) to service_role;
grant execute on function public.admin_record_login_attempt(text, inet, boolean) to service_role;
grant execute on function public.admin_bootstrap_owner(uuid) to service_role;
grant execute on function public.admin_create_session(uuid, text, text, inet, text) to service_role;
grant execute on function public.admin_validate_session(text, boolean) to service_role;
grant execute on function public.admin_revoke_session(text, text) to service_role;
grant execute on function public.admin_invalidate_credentials(uuid) to service_role;
grant execute on function public.admin_update_plan_limits(text, text, text, integer, integer, integer, integer, uuid, inet) to service_role;
grant execute on function public.admin_update_user_limits(text, text, uuid, integer, integer, integer, integer, uuid, inet) to service_role;
grant execute on function public.admin_update_booking_status(text, text, uuid, text, uuid, inet) to service_role;
grant execute on function public.admin_overview(text, text) to service_role;
grant execute on function public.admin_list_users(text, text, text, text, text, text, integer, integer) to service_role;
grant execute on function public.admin_get_user(text, uuid) to service_role;
grant execute on function public.admin_list_plans(text) to service_role;
grant execute on function public.admin_list_bookings(text, text, text, text, text, text, integer, integer) to service_role;
grant execute on function public.admin_list_audit(text, text, text, text, integer, integer) to service_role;

commit;
