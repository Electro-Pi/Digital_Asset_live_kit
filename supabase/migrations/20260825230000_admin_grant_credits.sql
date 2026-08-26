-- Admin credit granting.
--
-- The admin dashboard could already issue API keys but not fund them, so every
-- key it created answered 402 at the gateway until someone topped the account
-- up by hand. This closes that: the same operators who mint a key can give the
-- account behind it credits, through the same token+CSRF check and into the
-- same audit trail as every other privileged action.
--
-- `grant_credits` itself already exists and is already correct — it updates the
-- balance and writes the ledger row in one transaction, and is idempotent on
-- `ref` via credit_ledger_ref_uniq. This is a thin authorization wrapper around
-- it, not a reimplementation: duplicating the balance arithmetic here would be
-- a second place for it to drift.

create or replace function public.admin_grant_credits(
  p_token_hash text,
  p_csrf_hash  text,
  p_user_id    uuid,
  p_amount     bigint,
  p_reason     text default 'admin_grant',
  -- Supplied by the caller so a retried request is recognised as the same
  -- grant. credit_ledger_ref_uniq turns a replay into a no-op that reports
  -- duplicate = true, rather than crediting the account twice.
  p_ref        text default null,
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
  v_email   text;
  v_before  bigint;
  v_result  jsonb;
begin
  -- Granting credits is spending money, so viewers are excluded exactly as
  -- they are for key issuance.
  v_context := private.require_admin(
    p_token_hash, p_csrf_hash, array['owner', 'operator']::text[]
  );

  if p_user_id is null then
    raise exception 'user id is required' using errcode = '22023';
  end if;
  select email into v_email from auth.users where id = p_user_id;
  if not found then
    raise exception 'user not found' using errcode = 'P0002';
  end if;

  -- grant_credits rejects a non-positive amount too, but its message names no
  -- ceiling. A cap belongs here because this is the human-facing path, and a
  -- mistyped grant is far likelier than a malicious one.
  if p_amount is null or p_amount <= 0 then
    raise exception 'amount must be greater than zero' using errcode = '22023';
  end if;
  if p_amount > 100000000 then
    raise exception 'amount exceeds the 100,000,000 credit ceiling for a single grant'
      using errcode = '22023';
  end if;
  if p_reason is null or btrim(p_reason) = '' or length(p_reason) > 120 then
    raise exception 'reason must be 1-120 characters' using errcode = '22023';
  end if;

  select balance into v_before from public.credit_balances where user_id = p_user_id;

  v_result := public.grant_credits(
    p_user_id,
    p_amount,
    btrim(p_reason),
    p_ref,
    jsonb_build_object(
      'admin_user_id', v_context ->> 'user_id',
      'request_id',    p_request_id
    )
  );

  -- A duplicate is audited as well as a real grant. Silence there would make a
  -- retried request indistinguishable from one that never arrived.
  perform private.write_admin_audit(
    v_context,
    case when (v_result ->> 'duplicate')::boolean
      then 'credits.granted.duplicate' else 'credits.granted' end,
    'user', p_user_id::text,
    jsonb_build_object('balance', coalesce(v_before, 0)),
    jsonb_build_object(
      'balance', v_result -> 'balance',
      'amount',  p_amount,
      'reason',  btrim(p_reason),
      'ref',     p_ref
    ),
    p_request_id, p_ip
  );

  return jsonb_build_object(
    'userId',    p_user_id,
    'email',     v_email,
    'amount',    p_amount,
    'granted',   v_result -> 'granted',
    'duplicate', v_result -> 'duplicate',
    'balance',   v_result -> 'balance'
  );
end;
$function$;

-- Reachable only by the dashboard's service role, like every other admin_* RPC.
-- Without this an anon caller who guessed the name could grant themselves
-- credits; the token check inside would stop them, but the surface should not
-- exist at all.
revoke all on function public.admin_grant_credits(text, text, uuid, bigint, text, text, uuid, inet)
  from public, anon, authenticated;
grant execute on function public.admin_grant_credits(text, text, uuid, bigint, text, text, uuid, inet)
  to service_role;
