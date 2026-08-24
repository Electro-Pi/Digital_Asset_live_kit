-- admin_list_api_keys: make the usage window's upper bound inclusive.
--
-- The window was [p_from, p_to) with p_to defaulting to now(). A usage_events
-- row written at now() therefore failed `created_at < p_to` and was left out,
-- so the most recent usage — the reason someone opens this page — was missing.
-- It showed up as a key that had demonstrably just billed 150 seconds
-- reporting 0.0 minutes.
--
-- A half-open interval is the right default for ranges that tile, but this one
-- does not tile: it is a single "up to now" rollup for display. Two adjacent
-- explicit ranges could now double-count a row landing exactly on the shared
-- boundary, which for a usage summary is a far smaller error than silently
-- hiding the newest data.
--
-- Only the two `< v_to` comparisons change; the rest is the function as applied
-- in 20260823174425.

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
        and e.created_at >= v_from and e.created_at <= v_to
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

revoke all on function public.admin_list_api_keys(text, text, text, text, timestamptz, timestamptz, integer, integer)
  from public, anon, authenticated;
grant execute on function public.admin_list_api_keys(text, text, text, text, timestamptz, timestamptz, integer, integer)
  to service_role;
