-- Coda podcast: seat reservations, alert subscribers, and scheduled episodes.
--
-- Three tables, all service-role only (RLS on, no policies) exactly like
-- dialect_entries: the public page reaches them through server routes that hold
-- the service key, and the dashboard reaches them through the SECURITY DEFINER
-- RPCs below, which re-check the admin session and write the audit log. Nothing
-- here is readable by anon or authenticated.
--
-- Publish times are stored as timestamptz. The dashboard collects a wall-clock
-- date and time in Riyadh and converts with a fixed +03:00 offset before
-- sending — Saudi Arabia has observed no DST since 1990, so that offset is
-- exact rather than an approximation.
begin;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table if not exists public.coda_podcast (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id),
  full_name text not null,
  organisation text,
  mobile text not null,
  day_id text not null check (day_id ~ '^[a-z0-9_-]{1,24}$'),
  slot_time text not null check (slot_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
  angle text not null check (angle in ('sector', 'sovereignty', 'year')),
  lang text not null default 'ar' check (lang in ('ar', 'en')),
  consent boolean not null default true,
  status text not null default 'new' check (status in ('new', 'confirmed', 'done', 'canceled')),
  source text not null default 'podcast_page',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.coda_podcast is
  'Seat reservations submitted by the /podcast booking form. RLS: no policies by design — reachable only through the service-role API route and the admin RPCs.';

-- One live booking per slot. Cancelling frees the slot again, which is why the
-- index is partial rather than a plain unique constraint.
create unique index if not exists coda_podcast_slot_unique
  on public.coda_podcast (day_id, slot_time)
  where status <> 'canceled';

create index if not exists coda_podcast_created_idx
  on public.coda_podcast (created_at desc);

create table if not exists public.coda_podcast_subscribers (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  lang text not null default 'ar' check (lang in ('ar', 'en')),
  status text not null default 'active' check (status in ('active', 'unsubscribed', 'bounced')),
  -- Carried in the footer of every alert so one click stops the mail without a
  -- login. Long and random because possessing it is the whole authorisation.
  unsubscribe_token text not null unique default encode(extensions.gen_random_bytes(24), 'hex'),
  source text not null default 'podcast_page',
  created_at timestamptz not null default now(),
  unsubscribed_at timestamptz,
  last_notified_at timestamptz
);

comment on table public.coda_podcast_subscribers is
  'Addresses that asked to be told when a podcast episode goes live. RLS: no policies by design — service-role routes only.';

create unique index if not exists coda_podcast_subscribers_email_unique
  on public.coda_podcast_subscribers (lower(email));

create table if not exists public.coda_podcast_episodes (
  id uuid primary key default gen_random_uuid(),
  title_ar text not null,
  title_en text not null,
  summary_ar text,
  summary_en text,
  guest_name text,
  -- Playable URL. Either an object in the podcast-video bucket (video_path is
  -- then set too) or an external link the operator pasted.
  video_url text not null,
  video_path text,
  thumbnail_url text,
  duration_seconds integer check (duration_seconds is null or duration_seconds >= 0),
  -- The instant the episode becomes visible on /podcast.
  publish_at timestamptz not null,
  status text not null default 'scheduled' check (status in ('draft', 'scheduled', 'archived')),
  -- Set when the alert mail for this episode has been claimed for sending, so a
  -- second dispatcher run cannot mail the same episode twice.
  notified_at timestamptz,
  created_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.coda_podcast_episodes is
  'Podcast episodes and their scheduled reveal time. Visible on the public page when status = scheduled and publish_at <= now(). RLS: no policies by design — service-role routes only.';

create index if not exists coda_podcast_episodes_live_idx
  on public.coda_podcast_episodes (publish_at desc)
  where status = 'scheduled';

-- Claimed by the dispatcher; the partial index keeps that poll cheap.
create index if not exists coda_podcast_episodes_pending_notify_idx
  on public.coda_podcast_episodes (publish_at)
  where status = 'scheduled' and notified_at is null;

alter table public.coda_podcast enable row level security;
alter table public.coda_podcast_subscribers enable row level security;
alter table public.coda_podcast_episodes enable row level security;

drop trigger if exists coda_podcast_touch_updated_at on public.coda_podcast;
create trigger coda_podcast_touch_updated_at
before update on public.coda_podcast
for each row execute function private.touch_updated_at();

drop trigger if exists coda_podcast_episodes_touch_updated_at on public.coda_podcast_episodes;
create trigger coda_podcast_episodes_touch_updated_at
before update on public.coda_podcast_episodes
for each row execute function private.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Episode RPCs
-- ---------------------------------------------------------------------------

create or replace function private.podcast_episode_json(p_row public.coda_podcast_episodes)
returns jsonb
language sql
stable
set search_path = ''
as $$
  select jsonb_build_object(
    'id', p_row.id,
    'titleAr', p_row.title_ar,
    'titleEn', p_row.title_en,
    'summaryAr', p_row.summary_ar,
    'summaryEn', p_row.summary_en,
    'guestName', p_row.guest_name,
    'videoUrl', p_row.video_url,
    'videoPath', p_row.video_path,
    'thumbnailUrl', p_row.thumbnail_url,
    'durationSeconds', p_row.duration_seconds,
    'publishAt', p_row.publish_at,
    'status', p_row.status,
    -- Derived, never stored: an episode is live once its scheduled instant has
    -- passed. Storing it would need a job to flip the column at the right
    -- second, and would be wrong in between.
    'isLive', (p_row.status = 'scheduled' and p_row.publish_at <= now()),
    'notifiedAt', p_row.notified_at,
    'createdAt', p_row.created_at,
    'updatedAt', p_row.updated_at
  );
$$;

create or replace function public.admin_list_podcast_episodes(
  p_token_hash text,
  p_search text default null,
  p_status text default null,
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
  if p_status is not null and p_status not in ('draft', 'scheduled', 'archived', 'live', 'upcoming') then
    raise exception 'invalid episode status' using errcode = '22023';
  end if;

  v_offset := (p_page - 1) * p_page_size;

  with base as materialized (
    select e.*
    from public.coda_podcast_episodes e
    where (
      v_search = ''
      or strpos(lower(e.title_ar), v_search) > 0
      or strpos(lower(e.title_en), v_search) > 0
      or strpos(lower(coalesce(e.guest_name, '')), v_search) > 0
    )
      and (
        p_status is null
        or (p_status in ('draft', 'scheduled', 'archived') and e.status = p_status)
        or (p_status = 'live' and e.status = 'scheduled' and e.publish_at <= now())
        or (p_status = 'upcoming' and e.status = 'scheduled' and e.publish_at > now())
      )
  ), page_rows as (
    select * from base
    order by publish_at desc, id asc
    offset v_offset limit p_page_size
  )
  select jsonb_build_object(
    'items', coalesce(
      (select jsonb_agg(private.podcast_episode_json(r.*) order by r.publish_at desc, r.id asc)
       from page_rows r),
      '[]'::jsonb),
    'total', (select count(*) from base),
    'page', p_page,
    'pageSize', p_page_size
  ) into v_result;

  return v_result;
end;
$$;

/**
 * Create or update one episode. `p_id` null inserts, otherwise updates in
 * place, so the dashboard's one form serves both and the audit log records
 * which happened along with the previous row.
 */
create or replace function public.admin_save_podcast_episode(
  p_token_hash text,
  p_csrf_hash text,
  p_id uuid,
  p_title_ar text,
  p_title_en text,
  p_summary_ar text,
  p_summary_en text,
  p_guest_name text,
  p_video_url text,
  p_video_path text,
  p_thumbnail_url text,
  p_duration_seconds integer,
  p_publish_at timestamptz,
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
  v_before public.coda_podcast_episodes;
  v_after public.coda_podcast_episodes;
  v_title_ar text := btrim(coalesce(p_title_ar, ''));
  v_title_en text := btrim(coalesce(p_title_en, ''));
  v_video_url text := btrim(coalesce(p_video_url, ''));
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  if v_title_ar = '' or v_title_en = '' then
    raise exception 'episode titles are required' using errcode = '22023';
  end if;
  if v_video_url = '' then
    raise exception 'episode video is required' using errcode = '22023';
  end if;
  if v_video_url !~ '^https://' then
    raise exception 'episode video must be an https url' using errcode = '22023';
  end if;
  if p_thumbnail_url is not null and btrim(p_thumbnail_url) <> '' and p_thumbnail_url !~ '^https://' then
    raise exception 'episode thumbnail must be an https url' using errcode = '22023';
  end if;
  if p_publish_at is null then
    raise exception 'publish time is required' using errcode = '22023';
  end if;
  if p_status not in ('draft', 'scheduled', 'archived') then
    raise exception 'invalid episode status' using errcode = '22023';
  end if;

  if p_id is null then
    insert into public.coda_podcast_episodes (
      title_ar, title_en, summary_ar, summary_en, guest_name,
      video_url, video_path, thumbnail_url, duration_seconds,
      publish_at, status, created_by
    ) values (
      v_title_ar, v_title_en,
      nullif(btrim(coalesce(p_summary_ar, '')), ''),
      nullif(btrim(coalesce(p_summary_en, '')), ''),
      nullif(btrim(coalesce(p_guest_name, '')), ''),
      v_video_url,
      nullif(btrim(coalesce(p_video_path, '')), ''),
      nullif(btrim(coalesce(p_thumbnail_url, '')), ''),
      p_duration_seconds,
      p_publish_at,
      p_status,
      (v_context ->> 'user_id')::uuid
    )
    returning * into v_after;
  else
    select * into v_before
    from public.coda_podcast_episodes
    where id = p_id
    for update;
    if not found then
      raise exception 'episode not found' using errcode = 'P0002';
    end if;

    update public.coda_podcast_episodes
    set title_ar = v_title_ar,
        title_en = v_title_en,
        summary_ar = nullif(btrim(coalesce(p_summary_ar, '')), ''),
        summary_en = nullif(btrim(coalesce(p_summary_en, '')), ''),
        guest_name = nullif(btrim(coalesce(p_guest_name, '')), ''),
        video_url = v_video_url,
        video_path = nullif(btrim(coalesce(p_video_path, '')), ''),
        thumbnail_url = nullif(btrim(coalesce(p_thumbnail_url, '')), ''),
        duration_seconds = p_duration_seconds,
        publish_at = p_publish_at,
        status = p_status,
        -- Re-scheduling an episode into the future re-arms its alert: the mail
        -- has not gone out for the new time, so the claim must be released.
        notified_at = case
          when p_publish_at > now() and p_publish_at is distinct from v_before.publish_at then null
          else notified_at
        end
    where id = p_id
    returning * into v_after;
  end if;

  perform private.write_admin_audit(
    v_context,
    case when p_id is null then 'podcast_episode.created' else 'podcast_episode.updated' end,
    'podcast_episode',
    v_after.id::text,
    case when p_id is null then null else private.podcast_episode_json(v_before.*) end,
    private.podcast_episode_json(v_after.*),
    p_request_id, p_ip
  );

  return private.podcast_episode_json(v_after.*);
end;
$$;

create or replace function public.admin_delete_podcast_episode(
  p_token_hash text,
  p_csrf_hash text,
  p_id uuid,
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
  v_before public.coda_podcast_episodes;
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  delete from public.coda_podcast_episodes
  where id = p_id
  returning * into v_before;
  if not found then
    raise exception 'episode not found' using errcode = 'P0002';
  end if;

  perform private.write_admin_audit(
    v_context, 'podcast_episode.deleted', 'podcast_episode', p_id::text,
    private.podcast_episode_json(v_before.*), null,
    p_request_id, p_ip
  );

  -- The storage object is removed by the route after this commits; returning
  -- the path is what lets it do that without a second read.
  return jsonb_build_object('id', p_id, 'videoPath', v_before.video_path);
end;
$$;

/**
 * Atomically take ownership of every episode whose publish time has arrived and
 * whose alert has not been sent, stamping notified_at in the same statement.
 *
 * The dispatcher runs on a timer and may overlap itself; claiming and reading
 * in one UPDATE ... RETURNING is what stops two runs from mailing the same
 * episode. A run that then fails to send has already burned the claim, which is
 * the deliberate trade: a missed alert is recoverable by clearing notified_at,
 * a duplicate blast to every subscriber is not.
 */
create or replace function public.claim_podcast_episodes_for_notify(p_limit integer default 5)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_items jsonb;
begin
  if p_limit is null or p_limit < 1 or p_limit > 50 then
    raise exception 'invalid limit' using errcode = '22023';
  end if;

  with due as (
    select id
    from public.coda_podcast_episodes
    where status = 'scheduled'
      and notified_at is null
      and publish_at <= now()
    order by publish_at asc
    limit p_limit
    for update skip locked
  ), claimed as (
    update public.coda_podcast_episodes e
    set notified_at = now()
    from due
    where e.id = due.id
    returning e.*
  )
  select coalesce(jsonb_agg(private.podcast_episode_json(c.*) order by c.publish_at asc), '[]'::jsonb)
  into v_items
  from claimed c;

  return v_items;
end;
$$;

-- ---------------------------------------------------------------------------
-- Seat + subscriber RPCs
-- ---------------------------------------------------------------------------

create or replace function public.admin_list_podcast_seats(
  p_token_hash text,
  p_search text default null,
  p_status text default null,
  p_day text default null,
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
  if p_status is not null and p_status not in ('new', 'confirmed', 'done', 'canceled') then
    raise exception 'invalid seat status' using errcode = '22023';
  end if;

  v_offset := (p_page - 1) * p_page_size;

  with base as materialized (
    select s.*
    from public.coda_podcast s
    where (
      v_search = ''
      or strpos(lower(s.full_name), v_search) > 0
      or strpos(lower(s.mobile), v_search) > 0
      or strpos(lower(coalesce(s.organisation, '')), v_search) > 0
    )
      and (p_status is null or s.status = p_status)
      and (p_day is null or s.day_id = p_day)
  ), page_rows as (
    select * from base
    order by created_at desc, id asc
    offset v_offset limit p_page_size
  )
  select jsonb_build_object(
    'items', coalesce((select jsonb_agg(jsonb_build_object(
      'id', r.id,
      'userId', r.user_id,
      'fullName', r.full_name,
      'organisation', r.organisation,
      'mobile', r.mobile,
      'dayId', r.day_id,
      'slotTime', r.slot_time,
      'angle', r.angle,
      'lang', r.lang,
      'status', r.status,
      'source', r.source,
      'createdAt', r.created_at,
      'updatedAt', r.updated_at
    ) order by r.created_at desc, r.id asc) from page_rows r), '[]'::jsonb),
    'total', (select count(*) from base),
    'page', p_page,
    'pageSize', p_page_size
  ) into v_result;

  return v_result;
end;
$$;

create or replace function public.admin_update_podcast_seat_status(
  p_token_hash text,
  p_csrf_hash text,
  p_seat_id uuid,
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
  v_before public.coda_podcast;
  v_after public.coda_podcast;
begin
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  if p_status not in ('new', 'confirmed', 'done', 'canceled') then
    raise exception 'invalid seat status' using errcode = '22023';
  end if;

  select * into v_before
  from public.coda_podcast
  where id = p_seat_id
  for update;
  if not found then
    raise exception 'seat not found' using errcode = 'P0002';
  end if;

  update public.coda_podcast
  set status = p_status
  where id = p_seat_id
  returning * into v_after;

  perform private.write_admin_audit(
    v_context, 'podcast_seat.status_updated', 'podcast_seat', p_seat_id::text,
    jsonb_build_object('status', v_before.status),
    jsonb_build_object('status', v_after.status),
    p_request_id, p_ip
  );

  return jsonb_build_object(
    'id', v_after.id,
    'fullName', v_after.full_name,
    'status', v_after.status,
    'dayId', v_after.day_id,
    'slotTime', v_after.slot_time,
    'updatedAt', v_after.updated_at
  );
end;
$$;

create or replace function public.admin_list_podcast_subscribers(
  p_token_hash text,
  p_search text default null,
  p_status text default null,
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
  if p_status is not null and p_status not in ('active', 'unsubscribed', 'bounced') then
    raise exception 'invalid subscriber status' using errcode = '22023';
  end if;

  v_offset := (p_page - 1) * p_page_size;

  with base as materialized (
    select s.*
    from public.coda_podcast_subscribers s
    where (v_search = '' or strpos(lower(s.email), v_search) > 0)
      and (p_status is null or s.status = p_status)
  ), page_rows as (
    select * from base
    order by created_at desc, id asc
    offset v_offset limit p_page_size
  )
  select jsonb_build_object(
    'items', coalesce((select jsonb_agg(jsonb_build_object(
      'id', r.id,
      'email', r.email,
      'lang', r.lang,
      'status', r.status,
      'source', r.source,
      'createdAt', r.created_at,
      'lastNotifiedAt', r.last_notified_at,
      'unsubscribedAt', r.unsubscribed_at
    ) order by r.created_at desc, r.id asc) from page_rows r), '[]'::jsonb),
    'total', (select count(*) from base),
    'active', (select count(*) from public.coda_podcast_subscribers where status = 'active'),
    'page', p_page,
    'pageSize', p_page_size
  ) into v_result;

  return v_result;
end;
$$;

-- ---------------------------------------------------------------------------
-- Privilege boundary. CREATE FUNCTION grants EXECUTE to PUBLIC unless revoked,
-- so every function above is listed here.
-- ---------------------------------------------------------------------------

revoke all on function private.podcast_episode_json(public.coda_podcast_episodes)
  from public, anon, authenticated, service_role;

revoke all on function public.admin_list_podcast_episodes(text, text, text, integer, integer)
  from public, anon, authenticated;
revoke all on function public.admin_save_podcast_episode(text, text, uuid, text, text, text, text, text, text, text, text, integer, timestamptz, text, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.admin_delete_podcast_episode(text, text, uuid, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.claim_podcast_episodes_for_notify(integer)
  from public, anon, authenticated;
revoke all on function public.admin_list_podcast_seats(text, text, text, text, integer, integer)
  from public, anon, authenticated;
revoke all on function public.admin_update_podcast_seat_status(text, text, uuid, text, uuid, inet)
  from public, anon, authenticated;
revoke all on function public.admin_list_podcast_subscribers(text, text, text, integer, integer)
  from public, anon, authenticated;

grant execute on function public.admin_list_podcast_episodes(text, text, text, integer, integer) to service_role;
grant execute on function public.admin_save_podcast_episode(text, text, uuid, text, text, text, text, text, text, text, text, integer, timestamptz, text, uuid, inet) to service_role;
grant execute on function public.admin_delete_podcast_episode(text, text, uuid, uuid, inet) to service_role;
grant execute on function public.claim_podcast_episodes_for_notify(integer) to service_role;
grant execute on function public.admin_list_podcast_seats(text, text, text, text, integer, integer) to service_role;
grant execute on function public.admin_update_podcast_seat_status(text, text, uuid, text, uuid, inet) to service_role;
grant execute on function public.admin_list_podcast_subscribers(text, text, text, integer, integer) to service_role;

-- ---------------------------------------------------------------------------
-- Storage. Public read so the <video> element on /podcast can play the object
-- straight from its URL; writes stay service-role only, because no insert or
-- update policy is granted to anon or authenticated.
-- ---------------------------------------------------------------------------

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'podcast-video', 'podcast-video', true, 524288000,
  array['video/mp4','video/webm','video/quicktime','video/x-m4v','image/jpeg','image/png','image/webp','image/avif']
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

commit;
