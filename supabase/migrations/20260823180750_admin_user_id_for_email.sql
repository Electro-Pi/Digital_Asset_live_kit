-- Resolve an email address to its auth.users id, for the admin dashboard.
--
-- Issuing a key to someone who has never signed up means creating the auth user
-- first. Creation itself goes through the Supabase Auth API (which owns the
-- encrypted_password/confirmation columns and fires on_auth_user_created, so the
-- profile and free subscription appear on their own). But the API offers no
-- lookup by email, and PostgREST does not expose the auth schema, so the
-- "already exists" branch had no way to recover the id.
--
-- Read-only, and gated by the same session check as every other admin RPC.
-- Roles include viewer only because it discloses nothing a viewer cannot
-- already read from the users list.

create or replace function public.admin_user_id_for_email(
  p_token_hash text,
  p_email      text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_context jsonb;
  v_id      uuid;
begin
  v_context := private.require_admin(
    p_token_hash, null, array['owner', 'operator', 'viewer']::text[]
  );

  if p_email is null or btrim(p_email) = '' then
    raise exception 'email is required' using errcode = '22023';
  end if;

  -- auth.users stores the address as entered; compare case-insensitively so a
  -- capitalised address cannot mint a second account for the same person.
  select id into v_id
  from auth.users
  where lower(email) = lower(btrim(p_email))
    and deleted_at is null
  order by created_at
  limit 1;

  return v_id;
end;
$function$;

revoke all on function public.admin_user_id_for_email(text, text)
  from public, anon, authenticated;
grant execute on function public.admin_user_id_for_email(text, text) to service_role;
