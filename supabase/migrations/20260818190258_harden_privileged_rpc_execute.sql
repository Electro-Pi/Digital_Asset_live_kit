-- Close the pre-existing privilege escalation surface before adding the admin
-- dashboard. These functions operate with their owner's privileges and must
-- only be callable by trusted server code using the service role.
revoke execute on function public.activate_plan(uuid, text, integer, text)
  from public, anon, authenticated;
revoke execute on function public.grant_credits(uuid, bigint, text, text, jsonb)
  from public, anon, authenticated;
revoke execute on function public.refund_credits(uuid, bigint, text, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.spend_credits(uuid, bigint, text, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.credit_balance(uuid)
  from public, anon, authenticated;
revoke execute on function public.resolve_api_key_full(text)
  from public, anon, authenticated;

grant execute on function public.activate_plan(uuid, text, integer, text)
  to service_role;
grant execute on function public.grant_credits(uuid, bigint, text, text, jsonb)
  to service_role;
grant execute on function public.refund_credits(uuid, bigint, text, uuid, jsonb)
  to service_role;
grant execute on function public.spend_credits(uuid, bigint, text, uuid, jsonb)
  to service_role;
grant execute on function public.credit_balance(uuid)
  to service_role;
grant execute on function public.resolve_api_key_full(text)
  to service_role;
