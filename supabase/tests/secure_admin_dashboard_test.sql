-- Transactional database assertions for 20260818192000_secure_admin_dashboard.
-- Run against a migrated local Supabase database. The entire test rolls back.

begin;

do $$
begin
  if not exists (select 1 from pg_namespace where nspname = 'private') then
    raise exception 'private schema was not created';
  end if;
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'admin_overview'
  ) then
    raise exception 'admin_overview RPC was not created';
  end if;
  if (select provolatile from pg_proc where oid = 'public.period_start_for(text)'::regprocedure) <> 's' then
    raise exception 'period_start_for must be STABLE';
  end if;
end;
$$;

insert into auth.users (
  id, email, encrypted_password, email_confirmed_at, confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  ('10000000-0000-4000-8000-000000000001', 'admin-test@example.invalid', 'test-password-hash', now(), now(),
   '{"provider":"email"}', '{"full_name":"Test Owner"}', now() - interval '90 days', now()),
  ('10000000-0000-4000-8000-000000000002', 'user-test@example.invalid', 'test-password-hash', now(), now(),
   '{"provider":"email"}', '{"full_name":"Test User"}', now() - interval '2 days', now());

insert into auth.identities (user_id, provider) values
  ('10000000-0000-4000-8000-000000000001', 'email'),
  ('10000000-0000-4000-8000-000000000002', 'email');
insert into public.profiles (id, full_name, company) values
  ('10000000-0000-4000-8000-000000000001', 'Test Owner', 'Coda'),
  ('10000000-0000-4000-8000-000000000002', 'Test User', 'Customer');
insert into public.subscriptions (user_id, plan_id, status, is_pro) values
  ('10000000-0000-4000-8000-000000000001', 'free', 'active', false),
  ('10000000-0000-4000-8000-000000000002', 'pro', 'active', true);

do $$
declare v jsonb;
begin
  v := public.admin_bootstrap_owner('10000000-0000-4000-8000-000000000001');
  if v ->> 'role' <> 'owner' then raise exception 'owner bootstrap failed: %', v; end if;

  v := public.admin_create_session(
    '10000000-0000-4000-8000-000000000001', repeat('a', 64), repeat('b', 64),
    '127.0.0.1', 'database-test'
  );
  if v ->> 'valid' <> 'true'
     or v ->> 'admin_id' <> '10000000-0000-4000-8000-000000000001'
     or v ->> 'auth_user_id' <> '10000000-0000-4000-8000-000000000001' then
    raise exception 'session creation shape is invalid: %', v;
  end if;

  v := public.admin_validate_session(repeat('a', 64), true);
  if v ->> 'valid' <> 'true' or v ->> 'csrf_hash' <> repeat('b', 64) then
    raise exception 'session validation failed: %', v;
  end if;
end;
$$;

do $$
declare i integer; v jsonb;
begin
  for i in 1..5 loop
    v := public.admin_check_login_rate_limit('guess@example.invalid', '192.0.2.10');
    if v ->> 'allowed' <> 'true' then raise exception 'attempt % should be allowed: %', i, v; end if;
    perform public.admin_record_login_attempt('guess@example.invalid', '192.0.2.10', false);
  end loop;
  v := public.admin_check_login_rate_limit('guess@example.invalid', '192.0.2.10');
  if v ->> 'allowed' <> 'false' then raise exception 'sixth attempt must be blocked: %', v; end if;
end;
$$;

do $$
declare v jsonb; v_plan public.plans; v_count integer;
begin
  v := public.admin_update_plan_limits(
    repeat('a', 64), repeat('b', 64), 'free', 12, 20, 30, 40,
    '20000000-0000-4000-8000-000000000001', '127.0.0.1'
  );
  if v ->> 'agentMinutes' <> '12' or v ->> 'displayName' <> 'Free' then
    raise exception 'flat plan mutation result is invalid: %', v;
  end if;
  select * into v_plan from public.plans where id = 'free';
  if v_plan.live_seconds_limit <> 720 or v_plan.tts_limit <> 20 then
    raise exception 'plan limits were not converted/stored correctly';
  end if;
  select count(*) into v_count from private.admin_audit_log where action = 'plan_limits.updated';
  if v_count <> 1 then raise exception 'plan update was not audited exactly once'; end if;
end;
$$;

do $$
declare v jsonb; v_plan public.plans;
begin
  v := public.admin_update_user_limits(
    repeat('a', 64), repeat('b', 64), '10000000-0000-4000-8000-000000000002',
    7, null, 44, null, '20000000-0000-4000-8000-000000000002', '127.0.0.1'
  );
  v_plan := public.plan_for_user('10000000-0000-4000-8000-000000000002');
  if v_plan.live_seconds_limit <> 420 or v_plan.tts_limit <> 5000
     or v_plan.stt_limit <> 44 or v_plan.llm_limit <> 100000 then
    raise exception 'partial persistent override is incorrect: %', row_to_json(v_plan);
  end if;
  if v #>> '{overrides,agentMinutes}' <> '7' or v #>> '{overrides,ttsRequests}' is not null then
    raise exception 'override response is incorrect: %', v;
  end if;
end;
$$;

insert into public.usage_counters (
  user_id, period_start, live_seconds_used, tts_used, stt_used, llm_used
) values (
  '10000000-0000-4000-8000-000000000002', public.period_start_for('month'), 180, 3, 4, 5
);
insert into public.usage_events (user_id, kind, amount, created_at) values
  ('10000000-0000-4000-8000-000000000002', 'live', 180, now() - interval '1 minute'),
  ('10000000-0000-4000-8000-000000000002', 'tts', 3, now() - interval '1 minute');

select public.refund_quota('10000000-0000-4000-8000-000000000002', 'live', 60);

do $$
declare v_used integer; v_event integer;
begin
  select live_seconds_used into v_used from public.usage_counters
  where user_id = '10000000-0000-4000-8000-000000000002'
    and period_start = public.period_start_for('month');
  select amount into v_event from public.usage_events
  where user_id = '10000000-0000-4000-8000-000000000002'
    and kind = 'live' and metadata ->> 'event' = 'refund'
  order by id desc limit 1;
  if v_used <> 120 or v_event <> -60 then
    raise exception 'quota refund/counter-event reconciliation failed: used %, event %', v_used, v_event;
  end if;
end;
$$;

insert into public.credit_balances (user_id, balance) values
  ('10000000-0000-4000-8000-000000000002', 99);
insert into public.payments (
  user_id, kind, plan_id, amount, currency, status, fulfilled_at, created_at
) values (
  '10000000-0000-4000-8000-000000000002', 'plan', 'pro', 12345, 'SAR', 'paid',
  now() - interval '1 minute', now() - interval '1 minute'
);
insert into public.demo_bookings (
  id, user_id, name, company, email, phone, preferred_date, preferred_time,
  message, status, source, metadata, created_at, updated_at
) values (
  '30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000002',
  'Demo Lead', 'Customer', 'lead@example.invalid', '+966500000000', current_date + 1,
  time '14:30', 'Call me', 'new', 'landing', '{"job_title":"CTO"}',
  now() - interval '1 minute', now() - interval '1 day'
);

do $$
declare v jsonb; v_old_counter integer;
begin
  select live_seconds_used into v_old_counter from public.usage_counters
  where user_id = '10000000-0000-4000-8000-000000000002'
    and period_start = public.period_start_for('month');

  v := public.admin_update_booking_status(
    repeat('a', 64), repeat('b', 64), '30000000-0000-4000-8000-000000000001',
    'scheduled', '20000000-0000-4000-8000-000000000003', '127.0.0.1'
  );
  if v ->> 'status' <> 'scheduled' or v ->> 'preferredTime' is null
     or v ->> 'userId' <> '10000000-0000-4000-8000-000000000002' then
    raise exception 'full booking mutation result is invalid: %', v;
  end if;
  if (select updated_at <= now() - interval '1 hour' from public.demo_bookings
      where id = '30000000-0000-4000-8000-000000000001') then
    raise exception 'booking updated_at trigger did not run';
  end if;

  -- Limit changes must not reset existing usage.
  if v_old_counter <> (select live_seconds_used from public.usage_counters
      where user_id = '10000000-0000-4000-8000-000000000002'
        and period_start = public.period_start_for('month')) then
    raise exception 'admin mutation reset a usage counter';
  end if;
end;
$$;

do $$
declare v jsonb;
begin
  v := public.admin_list_users(repeat('a', 64), 'user-test', 'pro', 'confirmed', 'usage', 'desc', 1, 25);
  if (v ->> 'total')::integer <> 1 or jsonb_array_length(v -> 'items') <> 1 then
    raise exception 'user list filters/pagination failed: %', v;
  end if;

  v := public.admin_get_user(repeat('a', 64), '10000000-0000-4000-8000-000000000002');
  if v #>> '{user,email}' <> 'user-test@example.invalid'
     or jsonb_array_length(v -> 'recentUsage') <> 30 then
    raise exception 'user detail shape failed: %', v;
  end if;

  v := public.admin_list_bookings(repeat('a', 64), 'lead@', 'scheduled', 'landing', 'email', 'asc', 1, 25);
  if v #>> '{items,0,preferredTime}' is null then
    raise exception 'booking list omitted full details: %', v;
  end if;

  v := public.admin_overview(repeat('a', 64), 'month');
  if (v #>> '{summary,revenue}')::numeric <> 123.45
     or not (v ? 'bookingTrend')
     or not (v #> '{summary}' ? 'proAdoptionRate') then
    raise exception 'overview metrics/units are invalid: %', v;
  end if;
end;
$$;

do $$
begin
  if has_function_privilege('anon', 'public.admin_overview(text,text)', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.admin_overview(text,text)', 'EXECUTE') then
    raise exception 'admin_overview is publicly executable';
  end if;
  if not has_function_privilege('service_role', 'public.admin_overview(text,text)', 'EXECUTE') then
    raise exception 'service_role cannot execute admin_overview';
  end if;
  if has_function_privilege('anon', 'public.grant_credits(uuid,bigint,text,text,jsonb)', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.resolve_api_key_full(text)', 'EXECUTE') then
    raise exception 'privileged billing/key RPC grants were not revoked';
  end if;
end;
$$;

do $$
begin
  begin
    perform public.admin_update_plan_limits(
      repeat('a', 64), repeat('c', 64), 'free', 1, 1, 1, 1,
      '20000000-0000-4000-8000-000000000004', '127.0.0.1'
    );
    raise exception 'forged CSRF hash was accepted';
  exception when insufficient_privilege then
    null;
  end;

  begin
    update private.admin_audit_log set action = 'tampered' where true;
    raise exception 'audit log mutation was accepted';
  exception when object_not_in_prerequisite_state then
    null;
  end;
end;
$$;

select public.admin_update_user_limits(
  repeat('a', 64), repeat('b', 64), '10000000-0000-4000-8000-000000000002',
  null, null, null, null, '20000000-0000-4000-8000-000000000005', '127.0.0.1'
);

do $$
begin
  if exists (
    select 1 from private.user_quota_overrides
    where user_id = '10000000-0000-4000-8000-000000000002'
  ) then
    raise exception 'all-null override did not clear the row';
  end if;
end;
$$;

rollback;
