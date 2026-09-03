-- Report the storage object an edit orphaned, so the caller can delete it.
--
-- Replacing the video on an existing episode used to leave the previous upload
-- in the bucket forever, and episodes are hundreds of megabytes each. The RPC
-- is the only place that still knows the old path at that moment, so it hands
-- it back rather than making the route re-read the row it just overwrote.
--
-- The whole body is restated because `create or replace function` has no
-- partial form; only the path variable and the trailing return value differ
-- from 20260830120000_coda_podcast.sql.
begin;

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
  v_new_path text := nullif(btrim(coalesce(p_video_path, '')), '');
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
      v_new_path,
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
        video_path = v_new_path,
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

  return private.podcast_episode_json(v_after.*)
    || jsonb_build_object(
         'replacedVideoPath',
         case
           when p_id is not null
            and v_before.video_path is not null
            and v_before.video_path is distinct from v_new_path
           then v_before.video_path
         end
       );
end;
$$;

revoke all on function public.admin_save_podcast_episode(text, text, uuid, text, text, text, text, text, text, text, text, integer, timestamptz, text, uuid, inet)
  from public, anon, authenticated;
grant execute on function public.admin_save_podcast_episode(text, text, uuid, text, text, text, text, text, text, text, text, integer, timestamptz, text, uuid, inet) to service_role;

commit;
