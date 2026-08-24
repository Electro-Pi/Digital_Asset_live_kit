-- API keys gain a scope, a concurrency ceiling and an expiry; usage gains
-- per-key attribution; the admin dashboard gains issue/revoke/list RPCs.
--
-- Two problems this fixes, both established against the live database:
--
--   1. public.api_keys had no notion of what a key may open. 38 keys were
--      already issued for text-to-speech. Letting the voice agent accept
--      `coda_sk_live_…` without a scope column would have handed all 38 of them
--      metered live-voice access on the day the gateway shipped. `scopes`
--      defaults to '{tts}' so every existing key keeps exactly what it had and
--      gains nothing; agent access is opt-in, per key.
--
--   2. Nothing attributed usage to a key. usage_events.api_key_id existed and
--      was nullable everywhere, but count(api_key_id) was 0 across all 9 367
--      rows, because consume_quota had no key parameter to pass one through.
--      spend_credits (the TTS credit path) already took p_api_key; the quota
--      path did not.
--
-- Style follows 20260818192000_secure_admin_dashboard.sql: security definer,
-- empty search_path, fully qualified names, private.require_admin first,
-- private.write_admin_audit last, and execute granted to service_role only.

-- ── 1. Key capabilities ─────────────────────────────────────────────────────

alter table public.api_keys
  add column if not exists scopes                  text[]      not null default '{tts}',
  add column if not exists max_concurrent_sessions smallint    not null default 1,
  add column if not exists expires_at              timestamptz,
  add column if not exists created_by              uuid references auth.users(id);

comment on column public.api_keys.scopes is
  'Which products this key may reach: tts, agent, stt, llm. Defaults to {tts} so
   keys minted before scoping existed keep their original reach and nothing more.';
comment on column public.api_keys.max_concurrent_sessions is
  'Ceiling on simultaneous live agent sessions. A live session is billed per
   second while it is open, so a leaked key without this cap can hold the box
   hostage — nginx limit_req cannot see it, a WebSocket being one long request.';
comment on column public.api_keys.created_by is
  'Admin who issued the key, when issued from the dashboard. Null for
   self-serve keys the owner minted themselves.';

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'api_keys_scopes_valid'
  ) then
    alter table public.api_keys
      add constraint api_keys_scopes_valid check (
        scopes <@ array['tts','agent','stt','llm']::text[]
        and cardinality(scopes) > 0
      );
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'api_keys_max_concurrent_sane'
  ) then
    alter table public.api_keys
      add constraint api_keys_max_concurrent_sane check (
        max_concurrent_sessions between 1 and 100
      );
  end if;
end $$;

-- Per-key usage rollups scan by key and date; the existing indexes are keyed on
-- (kind, created_at, user_id), which does not serve this.
create index if not exists usage_events_key_created_idx
  on public.usage_events (api_key_id, created_at desc)
  where api_key_id is not null;

-- ── 2. Attribution ──────────────────────────────────────────────────────────
--
-- consume_quota needs a fourth parameter. CREATE OR REPLACE cannot change a
-- function's argument list, and keeping both arities would leave PostgREST
-- unable to choose between them for a three-key body ("could not choose the
-- best candidate function"). So: drop, recreate with the new argument
-- defaulting to null, and restore the grants — a recreated function does NOT
-- inherit its predecessor's ACL, and Postgres would otherwise hand EXECUTE to
-- PUBLIC. The previous ACL was postgres=X, service_role=X and nothing else.
--
-- The body below is the original, unchanged except for the usage_events insert.

drop function if exists public.consume_quota(uuid, text, integer);

create or replace function public.consume_quota(
  p_user    uuid,
  p_kind    text,
  p_amount  integer,
  p_api_key uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_plan   public.plans;
  v_period date;
  v_limit  integer;
  v_used   integer;
begin
  if p_kind not in ('live','tts','stt','llm') then
    raise exception 'invalid kind %', p_kind;
  end if;
  if p_amount is null or p_amount <= 0 then
    raise exception 'invalid amount %', p_amount;
  end if;

  v_plan   := public.plan_for_user(p_user);
  v_period := public.period_start_for(v_plan.reset_period);
  v_limit  := case p_kind
                when 'live' then v_plan.live_seconds_limit
                when 'tts'  then v_plan.tts_limit
                when 'stt'  then v_plan.stt_limit
                when 'llm'  then v_plan.llm_limit end;

  insert into public.usage_counters (user_id, period_start)
  values (p_user, v_period)
  on conflict (user_id, period_start) do nothing;

  select case p_kind
           when 'live' then live_seconds_used
           when 'tts'  then tts_used
           when 'stt'  then stt_used
           when 'llm'  then llm_used end
    into v_used
  from public.usage_counters
  where user_id = p_user and period_start = v_period
  for update;

  if v_used + p_amount > v_limit then
    return jsonb_build_object(
      'allowed', false, 'limit', v_limit, 'used', v_used,
      'remaining', greatest(v_limit - v_used, 0));
  end if;

  update public.usage_counters set
    live_seconds_used = live_seconds_used + (case when p_kind='live' then p_amount else 0 end),
    tts_used          = tts_used          + (case when p_kind='tts'  then p_amount else 0 end),
    stt_used          = stt_used          + (case when p_kind='stt'  then p_amount else 0 end),
    llm_used          = llm_used          + (case when p_kind='llm'  then p_amount else 0 end),
    updated_at        = now()
  where user_id = p_user and period_start = v_period;

  -- The one changed line: attribute the event to a key when the caller named
  -- one. Website sessions pass nothing and stay attributed to the user only.
  insert into public.usage_events (user_id, kind, amount, api_key_id)
  values (p_user, p_kind, p_amount, p_api_key);

  return jsonb_build_object(
    'allowed', true, 'limit', v_limit, 'used', v_used + p_amount,
    'remaining', v_limit - v_used - p_amount);
end;
$function$;

revoke all on function public.consume_quota(uuid, text, integer, uuid)
  from public, anon, authenticated;
grant execute on function public.consume_quota(uuid, text, integer, uuid)
  to service_role;

-- resolve_api_key_full now reports what the key may do, and refuses an expired
-- one. It already filtered revoked_at and touched last_used_at. Callers that
-- ignore the new fields (the TTS gateway, today) are unaffected.
create or replace function public.resolve_api_key_full(p_hash text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_key  public.api_keys;
  v_plan public.plans;
  v_bal  bigint;
begin
  update public.api_keys set last_used_at = now()
   where key_hash = p_hash
     and revoked_at is null
     and (expires_at is null or expires_at > now())
  returning * into v_key;
  if not found then return null; end if;

  v_plan := public.plan_for_user(v_key.user_id);
  select balance into v_bal from public.credit_balances where user_id = v_key.user_id;

  return jsonb_build_object(
    'user_id',                 v_key.user_id,
    'api_key_id',              v_key.id,
    'label',                   v_key.label,
    'plan',                    v_plan.id,
    'plan_name',               v_plan.name,
    'credits',                 coalesce(v_bal, 0),
    'scopes',                  v_key.scopes,
    'max_concurrent_sessions', v_key.max_concurrent_sessions,
    'expires_at',              v_key.expires_at);
end;
$function$;

revoke all on function public.resolve_api_key_full(text)
  from public, anon, authenticated;
grant execute on function public.resolve_api_key_full(text) to service_role;

-- ── 3. Admin issuance ───────────────────────────────────────────────────────
--
-- The raw key never reaches Postgres. The application generates it, hashes it
-- as sha256(pepper || raw) — the pepper being an application secret shared with
-- coda-next and the TTS gateway — and sends only the digest. A database dump
-- therefore cannot be turned back into working credentials.

create or replace function public.admin_create_api_key(
  p_token_hash   text,
  p_csrf_hash    text,
  p_user_id      uuid,
  p_label        text,
  p_scopes       text[],
  p_key_hash     text,
  p_key_prefix   text,
  p_max_concurrent smallint default 1,
  p_expires_at   timestamptz default null,
  p_request_id   uuid default gen_random_uuid(),
  p_ip           inet default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_context jsonb;
  v_key     public.api_keys;
  v_after   jsonb;
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  if p_user_id is null or not exists (select 1 from auth.users where id = p_user_id) then
    raise exception 'user not found' using errcode = 'P0002';
  end if;
  if p_label is null or btrim(p_label) = '' or length(p_label) > 60 then
    raise exception 'label must be 1-60 characters' using errcode = '22023';
  end if;
  if p_key_hash is null or p_key_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'key hash must be 64 lowercase hex characters' using errcode = '22023';
  end if;
  if p_key_prefix is null or p_key_prefix !~ '^coda_sk_live_[A-Za-z0-9_-]{1,8}$' then
    raise exception 'key prefix is malformed' using errcode = '22023';
  end if;
  if p_scopes is null or cardinality(p_scopes) = 0
     or not (p_scopes <@ array['tts','agent','stt','llm']::text[]) then
    raise exception 'scopes must be a non-empty subset of tts, agent, stt, llm'
      using errcode = '22023';
  end if;
  if p_max_concurrent is null or p_max_concurrent < 1 or p_max_concurrent > 100 then
    raise exception 'max concurrent sessions must be between 1 and 100'
      using errcode = '22023';
  end if;
  if p_expires_at is not null and p_expires_at <= now() then
    raise exception 'expiry must be in the future' using errcode = '22023';
  end if;

  insert into public.api_keys (
    user_id, label, key_hash, key_prefix,
    scopes, max_concurrent_sessions, expires_at, created_by
  ) values (
    p_user_id, btrim(p_label), p_key_hash, p_key_prefix,
    p_scopes, p_max_concurrent, p_expires_at, (v_context ->> 'user_id')::uuid
  )
  returning * into v_key;

  -- Deliberately excludes key_hash: the audit log is read by more people than
  -- the api_keys table is, and a digest plus a known pepper is a working key.
  v_after := jsonb_build_object(
    'label',         v_key.label,
    'prefix',        v_key.key_prefix,
    'scopes',        to_jsonb(v_key.scopes),
    'maxConcurrent', v_key.max_concurrent_sessions,
    'expiresAt',     v_key.expires_at,
    'ownerId',       v_key.user_id
  );

  perform private.write_admin_audit(
    v_context, 'api_key.created', 'api_key', v_key.id::text,
    null, v_after, p_request_id, p_ip
  );

  return jsonb_build_object(
    'id',            v_key.id,
    'label',         v_key.label,
    'prefix',        v_key.key_prefix,
    'scopes',        to_jsonb(v_key.scopes),
    'maxConcurrent', v_key.max_concurrent_sessions,
    'expiresAt',     v_key.expires_at,
    'createdAt',     v_key.created_at,
    'ownerId',       v_key.user_id
  );
end;
$function$;

create or replace function public.admin_revoke_api_key(
  p_token_hash text,
  p_csrf_hash  text,
  p_key_id     uuid,
  p_request_id uuid default gen_random_uuid(),
  p_ip         inet default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_context jsonb;
  v_key     public.api_keys;
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  select * into v_key from public.api_keys where id = p_key_id for update;
  if not found then
    raise exception 'api key not found' using errcode = 'P0002';
  end if;

  -- Revocation is idempotent, but the audit entry is not written twice: a
  -- second call is a no-op, not a second event.
  if v_key.revoked_at is not null then
    return jsonb_build_object(
      'id', v_key.id, 'revokedAt', v_key.revoked_at, 'alreadyRevoked', true);
  end if;

  update public.api_keys set revoked_at = now()
   where id = p_key_id
  returning * into v_key;

  perform private.write_admin_audit(
    v_context, 'api_key.revoked', 'api_key', v_key.id::text,
    jsonb_build_object('label', v_key.label, 'prefix', v_key.key_prefix,
                       'scopes', to_jsonb(v_key.scopes), 'revokedAt', null),
    jsonb_build_object('label', v_key.label, 'prefix', v_key.key_prefix,
                       'scopes', to_jsonb(v_key.scopes), 'revokedAt', v_key.revoked_at),
    p_request_id, p_ip
  );

  return jsonb_build_object(
    'id', v_key.id, 'revokedAt', v_key.revoked_at, 'alreadyRevoked', false);
end;
$function$;

-- Listing is a read, so viewers get it too. Minutes are computed here rather
-- than in the browser, matching admin_update_user_limits, which already speaks
-- minutes on the wire and seconds in the table.
create or replace function public.admin_list_api_keys(
  p_token_hash text,
  p_search     text default null,
  p_scope      text default null,
  p_status     text default null,
  p_from       timestamptz default null,
  p_to         timestamptz default null,
  p_limit      integer default 50,
  p_offset     integer default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_context jsonb;
  v_from    timestamptz := coalesce(p_from, now() - interval '30 days');
  v_to      timestamptz := coalesce(p_to, now());
  v_limit   integer := least(greatest(coalesce(p_limit, 50), 1), 200);
  v_offset  integer := greatest(coalesce(p_offset, 0), 0);
  v_result  jsonb;
begin
  v_context := private.require_admin(
    p_token_hash, null, array['owner', 'operator', 'viewer']::text[]
  );

  if p_status is not null and p_status not in ('active','revoked','expired') then
    raise exception 'status must be active, revoked or expired' using errcode = '22023';
  end if;

  with filtered as (
    select k.id, k.user_id, u.email, k.label, k.key_prefix, k.scopes,
           k.max_concurrent_sessions, k.expires_at, k.revoked_at,
           k.last_used_at, k.created_at
    from public.api_keys k
    join auth.users u on u.id = k.user_id
    where (p_search is null or btrim(p_search) = ''
           or k.label ilike '%' || p_search || '%'
           or u.email ilike '%' || p_search || '%')
      and (p_scope is null or p_scope = any(k.scopes))
      and (p_status is null
           or (p_status = 'active'
               and k.revoked_at is null
               and (k.expires_at is null or k.expires_at > now()))
           or (p_status = 'revoked' and k.revoked_at is not null)
           or (p_status = 'expired'
               and k.revoked_at is null
               and k.expires_at is not null and k.expires_at <= now()))
  ),
  page as (
    select * from filtered order by created_at desc limit v_limit offset v_offset
  ),
  enriched as (
    select p.id,
           p.email                        as "ownerEmail",
           p.user_id                      as "ownerId",
           p.label,
           p.key_prefix || repeat('•', 8) as masked,
           to_jsonb(p.scopes)             as scopes,
           p.max_concurrent_sessions      as "maxConcurrent",
           p.expires_at                   as "expiresAt",
           p.revoked_at                   as "revokedAt",
           p.last_used_at                 as "lastUsedAt",
           p.created_at                   as "createdAt",
           case
             when p.revoked_at is not null then 'revoked'
             when p.expires_at is not null and p.expires_at <= now() then 'expired'
             else 'active'
           end                            as status,
           -- Seconds in the table, minutes on the wire — the convention
           -- admin_update_user_limits already established.
           round(coalesce(r.live_range, 0) / 60.0, 1) as "liveMinutes",
           round(coalesce(l.live_total, 0) / 60.0, 1) as "liveMinutesLifetime",
           coalesce(r.tts_range, 0)                   as "ttsCharacters"
    from page p
    left join lateral (
      select sum(e.amount) filter (where e.kind = 'live') as live_range,
             sum(e.amount) filter (where e.kind = 'tts')  as tts_range
      from public.usage_events e
      where e.api_key_id = p.id
        and e.created_at >= v_from and e.created_at < v_to
    ) r on true
    left join lateral (
      select sum(e.amount) as live_total
      from public.usage_events e
      where e.api_key_id = p.id and e.kind = 'live'
    ) l on true
  )
  select jsonb_build_object(
    'items',  coalesce((select jsonb_agg(to_jsonb(x) order by x."createdAt" desc)
                          from enriched x), '[]'::jsonb),
    'total',  (select count(*)::int from filtered),
    'limit',  v_limit,
    'offset', v_offset,
    'from',   v_from,
    'to',     v_to
  ) into v_result;

  return v_result;
end;
$function$;

revoke all on function public.admin_create_api_key(text, text, uuid, text, text[], text, text, smallint, timestamptz, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.admin_revoke_api_key(text, text, uuid, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.admin_list_api_keys(text, text, text, text, timestamptz, timestamptz, integer, integer)
  from public, anon, authenticated;

grant execute on function public.admin_create_api_key(text, text, uuid, text, text[], text, text, smallint, timestamptz, uuid, inet)
  to service_role;
grant execute on function public.admin_revoke_api_key(text, text, uuid, uuid, inet)
  to service_role;
grant execute on function public.admin_list_api_keys(text, text, text, text, timestamptz, timestamptz, integer, integer)
  to service_role;
