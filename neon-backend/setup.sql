-- Recruit Desk: the rules that used to live in the sign-in server, kept in the database itself.
-- Run once per branch (and again after any change here): it is idempotent. Needs Neon Auth and the Data API enabled on the branch
-- (see README.md). Run it as the database owner with `python operator.py setup`; never from the app.
--
-- Model: sign-up is open, but a person sees nothing until they are a member of an organization (a "tenant", one company).
-- Organizations are created only by the operator (operator.py). Admins invite people with the Neon Auth organization API;
-- an invitation code is the invitation id, and only the invited email address can accept it.

create extension if not exists unaccent;

-- ---------- tenant names: unique ignoring case, spacing, punctuation and accents ----------

create or replace function public.immutable_unaccent(t text) returns text language sql immutable parallel safe
  as $$ select public.unaccent('public.unaccent'::regdictionary, t) $$;

create or replace function public.fold_name(t text) returns text language sql immutable parallel safe
  as $$ select regexp_replace(lower(public.immutable_unaccent(t)), '[^a-z0-9]+', '', 'g') $$;

create unique index if not exists org_name_folded on neon_auth.organization (public.fold_name(name));

-- ---------- only the operator creates organizations ----------
-- The operator script sets app.operator for its own session; nothing a signed-in person can send sets it.

create or replace function public.only_operator_creates_orgs() returns trigger language plpgsql as $$
begin
  if coalesce(current_setting('app.operator', true), '') <> 'yes' then
    raise exception 'Companies are created by the operator only' using errcode = '42501';
  end if;
  if char_length(btrim(new.name)) < 2 or char_length(public.fold_name(new.name)) < 2 then
    raise exception 'A company name needs at least 2 letters or digits' using errcode = '23514';
  end if;
  return new;
end $$;

drop trigger if exists org_lock on neon_auth.organization;
create trigger org_lock before insert on neon_auth.organization for each row execute function public.only_operator_creates_orgs();

-- ---------- one person, one company ----------

create or replace function public.one_company_per_person() returns trigger language plpgsql as $$
begin
  if exists (select 1 from neon_auth.member m where m."userId" = new."userId") then
    raise exception 'This person already belongs to a company' using errcode = '23505';
  end if;
  return new;
end $$;

drop trigger if exists member_one_company on neon_auth.member;
create trigger member_one_company before insert on neon_auth.member for each row execute function public.one_company_per_person();

-- An address that already has a member account elsewhere cannot be invited (an old invitation to it stays harmless: accepting fails above).
create or replace function public.no_invite_for_member() returns trigger language plpgsql as $$
begin
  if exists (select 1 from neon_auth.member m join neon_auth."user" u on u.id = m."userId" where lower(u.email) = lower(new.email)) then
    raise exception 'That address already belongs to a company' using errcode = '23505';
  end if;
  return new;
end $$;

drop trigger if exists invitation_not_for_member on neon_auth.invitation;
create trigger invitation_not_for_member before insert on neon_auth.invitation for each row execute function public.no_invite_for_member();

-- ---------- leaving a company cleanly ----------
-- Neon Auth points a session at the company the person last joined. When that membership is removed the pointer would dangle, and
-- Neon Auth then answers the removed person's session lookups with a server error. Clear the pointer with the membership.

create or replace function public.forget_active_company() returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update neon_auth.session set "activeOrganizationId" = null
    where "userId" = old."userId" and "activeOrganizationId" = old."organizationId"::text;
  return old;
end $$;

drop trigger if exists member_left on neon_auth.member;
create trigger member_left after delete on neon_auth.member for each row execute function public.forget_active_company();

-- ---------- the operator's own tools ----------
-- A system account owns invitations the operator makes. It has no password, so nobody can sign in as it.

insert into neon_auth."user" (id, name, email, "emailVerified", "createdAt", "updatedAt")
select '00000000-0000-0000-0000-0000000000aa', 'Recruit Desk operator', 'operator@recruit-desk.invalid', false, now(), now()
where not exists (select 1 from neon_auth."user" where id = '00000000-0000-0000-0000-0000000000aa');

-- Creates a company and an owner invitation for its first admin; returns the invitation code to hand to that person.
create or replace function public.operator_create_company(company text, first_admin_email text, valid_days int default 14)
returns uuid language plpgsql as $$
declare org uuid := gen_random_uuid(); code uuid := gen_random_uuid(); shown text := regexp_replace(btrim(company), '\s+', ' ', 'g');
begin
  perform set_config('app.operator', 'yes', true);
  insert into neon_auth.organization (id, name, slug, "createdAt")
    values (org, shown, left(trim(both '-' from regexp_replace(lower(public.immutable_unaccent(shown)), '[^a-z0-9]+', '-', 'g')), 40) || '-' || substr(org::text, 1, 6), now());
  insert into neon_auth.invitation (id, "organizationId", email, role, status, "expiresAt", "inviterId")
    values (code, org, lower(btrim(first_admin_email)), 'owner', 'pending', now() + make_interval(days => valid_days), '00000000-0000-0000-0000-0000000000aa');
  return code;
end $$;

-- ---------- membership helpers for the row-level security below ----------
-- The sign-in token carries no company, so policies look up live membership: removing someone cuts their access at once.

create or replace function public.is_member(org uuid) returns boolean language sql stable security definer set search_path = ''
  as $$ select exists (select 1 from neon_auth.member m where m."organizationId" = org and m."userId"::text = auth.user_id()) $$;

-- ---------- the last record each assistant created ----------

create or replace function public.record_problem(agent text, r jsonb) returns text language plpgsql immutable as $$
declare allowed text[]; k text; v jsonb; s text; m text[]; cap int;
begin
  allowed := case agent
    when 'candidate' then array['name','title','company','location','match_score','profile_url','jd','date_found']
    when 'company'   then array['job_title','company','location','status','source_type','posted_date','listing_url','date_discovered']
    when 'account'   then array['company','domain','product_type','hq','funding','confidence','last_checked']
    else array[]::text[] end;
  if jsonb_typeof(r) <> 'object' then return 'The record must be an object'; end if;
  if char_length(r::text) > 3500 then return 'The record is too large'; end if;
  for k, v in select * from jsonb_each(r) loop
    if not (k = any (allowed)) then return 'Field not accepted: ' || k; end if;
    if jsonb_typeof(v) not in ('string', 'number', 'null') then return k || ' must be text or a number'; end if;
    if jsonb_typeof(v) = 'string' then
      s := v #>> '{}';
      cap := (case when k like '%\_url' then 600 else 300 end);
      if char_length(s) > cap then return k || ' is too long'; end if;
      if s ~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' then return 'Contact details are not accepted'; end if;
      if k not like '%\_url' then
        for m in select regexp_matches(s, '\+?\d[\d .()-]{7,}\d', 'g') loop
          if char_length(regexp_replace(m[1], '\D', '', 'g')) >= 9 then return 'Contact details are not accepted'; end if;
        end loop;
      end if;
    end if;
  end loop;
  return null;
end $$;

-- security definer: the signed-in role may not use the auth schema, the trigger reads who is calling from it
create or replace function public.record_check() returns trigger language plpgsql security definer set search_path = '' as $$
declare problem text := public.record_problem(new.agent, new.record);
begin
  if problem is not null then raise exception '%', problem using errcode = 'PT422'; end if;
  if tg_op = 'UPDATE' then
    if new.created_at < old.created_at then raise exception 'A newer record is already stored' using errcode = 'PT409'; end if;
    if new.created_at = old.created_at and new.subject_hash = old.subject_hash then return old; end if;   -- the same record again: no change
  end if;
  new.stored_at := now();
  new.stored_by := auth.user_id();
  new.stored_by_email := auth.jwt() ->> 'email';
  return new;
end $$;

create table if not exists public.last_records (
  tenant_id       uuid not null references neon_auth.organization(id) on delete cascade,
  agent           text not null check (agent in ('candidate', 'company', 'account')),
  record          jsonb not null,
  subject_hash    text not null check (subject_hash ~ '^[0-9a-f]{64}$'),
  created_at      timestamptz not null,
  stored_at       timestamptz not null default now(),
  stored_by       text,
  stored_by_email text,
  primary key (tenant_id, agent)
);

drop trigger if exists last_records_check on public.last_records;
create trigger last_records_check before insert or update on public.last_records for each row execute function public.record_check();

alter table public.last_records enable row level security;
drop policy if exists lr_select on public.last_records;
drop policy if exists lr_insert on public.last_records;
drop policy if exists lr_update on public.last_records;
drop policy if exists lr_delete on public.last_records;
create policy lr_select on public.last_records for select to authenticated using (public.is_member(tenant_id));
create policy lr_insert on public.last_records for insert to authenticated with check (public.is_member(tenant_id));
create policy lr_update on public.last_records for update to authenticated using (public.is_member(tenant_id)) with check (public.is_member(tenant_id));
create policy lr_delete on public.last_records for delete to authenticated using (public.is_member(tenant_id));

revoke all on public.last_records from anonymous, public;
grant select, insert, update, delete on public.last_records to authenticated;

-- ---------- search history: every person's own searches and results; admins see everyone's in their company ----------
-- A person's computer records each search they start and every NEW result row (a fixed list of fields, no contact details) and the
-- app stores them here. A person sees only their own; the company's owner and admins see everyone's. Whoever stored a row is set by
-- the database from the sign-in, never by the app.

create or replace function public.is_org_admin(org uuid) returns boolean language sql stable security definer set search_path = ''
  as $$ select exists (select 1 from neon_auth.member m where m."organizationId" = org and m."userId"::text = auth.user_id() and m.role in ('owner', 'admin')) $$;

create or replace function public.search_problem(agent text, i jsonb) returns text language plpgsql immutable as $$
declare allowed text[]; k text; v jsonb;
begin
  allowed := case agent
    when 'candidate' then array['jd']
    when 'company'   then array['company', 'domain', 'publicPosts']
    when 'account'   then array['company', 'domain', 'csv', 'contacts']
    else array[]::text[] end;
  if jsonb_typeof(i) <> 'object' then return 'The search must be an object'; end if;
  if char_length(i::text) > 40000 then return 'The search is too large'; end if;
  for k, v in select * from jsonb_each(i) loop
    if not (k = any (allowed)) then return 'Field not accepted: ' || k; end if;
    if jsonb_typeof(v) not in ('string', 'boolean', 'null') then return k || ' must be text or yes/no'; end if;
  end loop;
  return null;
end $$;

create table if not exists public.searches (
  id          uuid primary key,
  tenant_id   uuid not null references neon_auth.organization(id) on delete cascade,
  user_id     text not null,
  user_email  text,
  agent       text not null check (agent in ('candidate', 'company', 'account')),
  inputs      jsonb not null default '{}'::jsonb,
  started_at  timestamptz not null,
  finished_at timestamptz,
  status      text not null default 'running' check (status in ('running', 'done', 'failed', 'stopped')),
  created_at  timestamptz not null default now()
);
create index if not exists searches_tenant_started on public.searches (tenant_id, started_at desc);

create table if not exists public.shared_rows (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references neon_auth.organization(id) on delete cascade,
  user_id      text not null,
  user_email   text,
  search_id    uuid references public.searches(id) on delete cascade,
  agent        text not null check (agent in ('candidate', 'company', 'account')),
  subject_hash text not null check (subject_hash ~ '^[0-9a-f]{64}$'),
  record       jsonb not null,
  created_at   timestamptz not null,
  stored_at    timestamptz not null default now(),
  unique (tenant_id, user_id, agent, subject_hash)
);
create index if not exists shared_rows_search on public.shared_rows (search_id);
create index if not exists shared_rows_subject on public.shared_rows (tenant_id, agent, subject_hash);

create or replace function public.search_check() returns trigger language plpgsql security definer set search_path = '' as $$
declare problem text := public.search_problem(new.agent, new.inputs);
begin
  if problem is not null then raise exception '%', problem using errcode = 'PT422'; end if;
  if tg_op = 'INSERT' then
    new.user_id := auth.user_id();
    new.user_email := auth.jwt() ->> 'email';
    new.created_at := now();
  else   -- who a search belongs to never changes
    new.id := old.id; new.tenant_id := old.tenant_id; new.user_id := old.user_id; new.user_email := old.user_email; new.agent := old.agent; new.created_at := old.created_at;
  end if;
  return new;
end $$;

create or replace function public.shared_row_check() returns trigger language plpgsql security definer set search_path = '' as $$
declare problem text := public.record_problem(new.agent, new.record);
begin
  if problem is not null then raise exception '%', problem using errcode = 'PT422'; end if;
  new.user_id := auth.user_id();
  new.user_email := auth.jwt() ->> 'email';
  new.stored_at := now();
  -- a row can only be linked to the sender's own search in the same company
  if new.search_id is not null and not exists (select 1 from public.searches s where s.id = new.search_id and s.tenant_id = new.tenant_id and s.user_id = new.user_id) then
    new.search_id := null;
  end if;
  return new;
end $$;

drop trigger if exists searches_check on public.searches;
create trigger searches_check before insert or update on public.searches for each row execute function public.search_check();
drop trigger if exists shared_rows_check on public.shared_rows;
create trigger shared_rows_check before insert on public.shared_rows for each row execute function public.shared_row_check();

alter table public.searches enable row level security;
alter table public.shared_rows enable row level security;
drop policy if exists s_select on public.searches;  drop policy if exists s_insert on public.searches;
drop policy if exists s_update on public.searches;  drop policy if exists s_delete on public.searches;
drop policy if exists r_select on public.shared_rows; drop policy if exists r_insert on public.shared_rows; drop policy if exists r_delete on public.shared_rows;
create policy s_select on public.searches for select to authenticated using (public.is_member(tenant_id) and (user_id = auth.user_id() or public.is_org_admin(tenant_id)));
create policy s_insert on public.searches for insert to authenticated with check (public.is_member(tenant_id));
create policy s_update on public.searches for update to authenticated using (public.is_member(tenant_id) and user_id = auth.user_id()) with check (public.is_member(tenant_id));
create policy s_delete on public.searches for delete to authenticated using (public.is_member(tenant_id) and (user_id = auth.user_id() or public.is_org_admin(tenant_id)));
create policy r_select on public.shared_rows for select to authenticated using (public.is_member(tenant_id) and (user_id = auth.user_id() or public.is_org_admin(tenant_id)));
create policy r_insert on public.shared_rows for insert to authenticated with check (public.is_member(tenant_id));
create policy r_delete on public.shared_rows for delete to authenticated using (public.is_member(tenant_id) and (user_id = auth.user_id() or public.is_org_admin(tenant_id)));

revoke all on public.searches, public.shared_rows from anonymous, public;
grant select, insert, update, delete on public.searches to authenticated;
grant select, insert, delete on public.shared_rows to authenticated;

-- ---------- the token allowance: each person's own cap and window, counted over all the assistants together ----------
-- Every person has their own row: their own cap, their own window length and their own count of tokens, over the three assistants together.
-- Nothing is company-wide. A new person starts at 3,000,000 tokens per 4 hours; the company's owner and admins set each person's cap and
-- window one by one. A person's window opens with their first tokens counted in it and closes after their `window_minutes`, when their count
-- returns to zero and any lock lifts. Time is the database's clock, so nobody's own clock matters. People never write these tables: they
-- call the functions below, which work out the person and the company from the sign-in.

-- (2026-10-21) Earlier versions kept a pool per assistant for the company, then one company cap for everyone. Both are gone.
drop function if exists public.allowance_set(bigint, int);
drop function if exists public.allowance_set(text, bigint, int);
drop function if exists public.allowance_reset(text);
drop function if exists public.allowance_json(uuid);
drop function if exists public.allowance_roll(uuid);
drop table if exists public.allowance;
drop table if exists public.allowance_policy;

create table if not exists public.allowance_use (
  tenant_id      uuid not null references neon_auth.organization(id) on delete cascade,
  user_id        text not null,
  user_email     text,
  cap            bigint not null default 3000000 check (cap between 1000 and 1000000000),
  window_minutes int not null default 240 check (window_minutes between 15 and 1440),
  counted        bigint not null default 0 check (counted >= 0),
  window_started timestamptz,
  locked_at      timestamptz,
  primary key (tenant_id, user_id)
);
alter table public.allowance_use add column if not exists cap bigint not null default 3000000 check (cap between 1000 and 1000000000);
alter table public.allowance_use add column if not exists window_minutes int not null default 240 check (window_minutes between 15 and 1440);

create table if not exists public.allowance_log (
  id         bigint generated always as identity primary key,
  tenant_id  uuid not null references neon_auth.organization(id) on delete cascade,
  user_id    text not null,
  user_email text,
  agent      text not null,
  tokens     bigint not null check (tokens >= 0),
  at         timestamptz not null default now()
);
create index if not exists allowance_log_tenant_at on public.allowance_log (tenant_id, at desc);

alter table public.allowance_use enable row level security;
alter table public.allowance_log enable row level security;
drop policy if exists au_select on public.allowance_use;
drop policy if exists alog_select on public.allowance_log;
create policy au_select on public.allowance_use for select to authenticated using (public.is_member(tenant_id) and (user_id = auth.user_id() or public.is_org_admin(tenant_id)));
create policy alog_select on public.allowance_log for select to authenticated using (public.is_member(tenant_id) and (user_id = auth.user_id() or public.is_org_admin(tenant_id)));
revoke all on public.allowance_use, public.allowance_log from anonymous, public;
grant select on public.allowance_use, public.allowance_log to authenticated;   -- reading only; every change goes through the functions

create or replace function public.my_company() returns uuid language sql stable security definer set search_path = ''
  as $$ select m."organizationId" from neon_auth.member m where m."userId"::text = auth.user_id() limit 1 $$;

-- Make sure the person's row exists, and close their window if it has ended (count to zero, lock lifts).
create or replace function public.allowance_roll(t uuid, u text) returns void language plpgsql security definer set search_path = '' as $$
begin
  insert into public.allowance_use (tenant_id, user_id, user_email) values (t, u, auth.jwt() ->> 'email')
    on conflict (tenant_id, user_id) do update set user_email = coalesce(excluded.user_email, public.allowance_use.user_email);
  update public.allowance_use x set counted = 0, window_started = null, locked_at = null
    where x.tenant_id = t and x.user_id = u and x.window_started is not null and now() >= x.window_started + make_interval(mins => x.window_minutes);
end $$;

-- One person's state: their own cap and window and their own count.
create or replace function public.allowance_json(t uuid, u text) returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'now_ms', (extract(epoch from now()) * 1000)::bigint,
    'cap', x.cap, 'window_minutes', x.window_minutes, 'counted', x.counted,
    'window_started_ms', (extract(epoch from x.window_started) * 1000)::bigint,
    'locked_at_ms', (extract(epoch from x.locked_at) * 1000)::bigint)
  from public.allowance_use x where x.tenant_id = t and x.user_id = u $$;

create or replace function public.allowance_state() returns jsonb language plpgsql security definer set search_path = '' as $$
declare t uuid := public.my_company(); u text := auth.user_id();
begin
  if t is null then raise exception 'You are not part of a company' using errcode = '42501'; end if;
  perform public.allowance_roll(t, u);
  return public.allowance_json(t, u);
end $$;

-- Count tokens for the caller. The assistant is only recorded in the log; the count is over all of them.
create or replace function public.allowance_add(p_agent text, p_tokens bigint) returns jsonb language plpgsql security definer set search_path = '' as $$
declare t uuid := public.my_company(); u text := auth.user_id();
begin
  if t is null then raise exception 'You are not part of a company' using errcode = '42501'; end if;
  if p_agent not in ('candidate', 'company', 'account') then raise exception 'No such assistant' using errcode = 'PT422'; end if;
  if p_tokens is null or p_tokens < 0 or p_tokens > 5000000 then raise exception 'Tokens must be between 0 and 5,000,000 per call' using errcode = 'PT422'; end if;
  perform public.allowance_roll(t, u);
  update public.allowance_use x set
      window_started = case when x.window_started is null and p_tokens > 0 then now() else x.window_started end,
      counted = x.counted + p_tokens,
      locked_at = case when x.locked_at is null and x.counted + p_tokens >= x.cap then now() else x.locked_at end
    where x.tenant_id = t and x.user_id = u;
  if p_tokens > 0 then
    insert into public.allowance_log (tenant_id, user_id, user_email, agent, tokens) values (t, u, auth.jwt() ->> 'email', p_agent, p_tokens);
  end if;
  return public.allowance_json(t, u);
end $$;

-- Admins: one person's own cap and window, by email (a person who has not opened the app yet gets their row now).
create or replace function public.allowance_set_user(p_email text, p_cap bigint, p_window_minutes int) returns jsonb language plpgsql security definer set search_path = '' as $$
declare t uuid := public.my_company(); u text := auth.user_id(); target text;
begin
  if t is null then raise exception 'You are not part of a company' using errcode = '42501'; end if;
  if not public.is_org_admin(t) then raise exception 'Only an admin can change the allowance' using errcode = '42501'; end if;
  if p_cap is null or p_cap < 1000 or p_cap > 1000000000 then raise exception 'The cap must be between 1,000 and 1,000,000,000 tokens' using errcode = 'PT422'; end if;
  if p_window_minutes is null or p_window_minutes < 15 or p_window_minutes > 1440 then raise exception 'The window must be between 15 minutes and 24 hours' using errcode = 'PT422'; end if;
  select m."userId"::text into target from neon_auth.member m join neon_auth."user" x on x.id = m."userId"
    where m."organizationId" = t and lower(x.email) = lower(p_email) limit 1;
  if target is null then raise exception 'That person is not in your company' using errcode = 'PT422'; end if;
  perform public.allowance_roll(t, u);
  insert into public.allowance_use (tenant_id, user_id, user_email) values (t, target, lower(p_email)) on conflict (tenant_id, user_id) do nothing;
  update public.allowance_use x set cap = p_cap, window_minutes = p_window_minutes,
      locked_at = case when x.counted >= p_cap then coalesce(x.locked_at, now()) else null end   -- a raised cap lifts the lock, a lowered one may set it
    where x.tenant_id = t and x.user_id = target;
  update public.allowance_use x set counted = 0, window_started = null, locked_at = null   -- a shorter window may already be over
    where x.tenant_id = t and x.user_id = target and x.window_started is not null and now() >= x.window_started + make_interval(mins => p_window_minutes);
  return public.allowance_json(t, u);
end $$;

-- Admins: empty one person's count (by email), or everyone's when no email is given. The window starts again with their next tokens.
create or replace function public.allowance_reset(p_email text default null) returns jsonb language plpgsql security definer set search_path = '' as $$
declare t uuid := public.my_company(); u text := auth.user_id();
begin
  if t is null then raise exception 'You are not part of a company' using errcode = '42501'; end if;
  if not public.is_org_admin(t) then raise exception 'Only an admin can reset the allowance' using errcode = '42501'; end if;
  perform public.allowance_roll(t, u);
  update public.allowance_use set counted = 0, window_started = null, locked_at = null
    where tenant_id = t and (p_email is null or lower(user_email) = lower(p_email));
  return public.allowance_json(t, u);
end $$;

-- Admins: everyone's cap, window and count at a glance (every member, also those who have not used any tokens yet).
create or replace function public.allowance_people() returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare t uuid := public.my_company();
begin
  if t is null then raise exception 'You are not part of a company' using errcode = '42501'; end if;
  if not public.is_org_admin(t) then raise exception 'Only an admin can see everyone''s allowance' using errcode = '42501'; end if;
  return jsonb_build_object(
    'now_ms', (extract(epoch from now()) * 1000)::bigint,
    'people', coalesce((select jsonb_agg(jsonb_build_object(
        'email', lower(u.email),
        'counted', case when x.user_id is null then 0 when x.window_started is not null and now() >= x.window_started + make_interval(mins => x.window_minutes) then 0 else x.counted end,
        'cap', coalesce(x.cap, 3000000), 'window_minutes', coalesce(x.window_minutes, 240),
        'window_started_ms', (extract(epoch from x.window_started) * 1000)::bigint,
        'locked_at_ms', (extract(epoch from x.locked_at) * 1000)::bigint) order by lower(u.email))
      from neon_auth.member m join neon_auth."user" u on u.id = m."userId"
      left join public.allowance_use x on x.tenant_id = m."organizationId" and x.user_id = m."userId"::text
      where m."organizationId" = t), '[]'::jsonb));
end $$;

revoke all on function public.allowance_roll(uuid, text), public.allowance_json(uuid, text), public.my_company() from public, anonymous, authenticated;
revoke all on function public.allowance_state(), public.allowance_add(text, bigint), public.allowance_set_user(text, bigint, int), public.allowance_reset(text), public.allowance_people() from public, anonymous;
grant execute on function public.allowance_state(), public.allowance_add(text, bigint), public.allowance_set_user(text, bigint, int), public.allowance_reset(text), public.allowance_people() to authenticated;

-- ---------- Excel results are not kept in the database (2026-10-21) ----------
-- The three Excel files stay on each person's computer (Documents/RecruitDesk-Data). Earlier versions stored every workbook row here and
-- kept an archive of deleted rows; both are removed, and so is everything that served them. A copy of the app that still tries to store
-- workbooks gets a plain refusal and keeps working with its local file.
drop table if exists public.workbook_rows_archive;
drop table if exists public.workbook_rows;
drop function if exists public.workbook_trim(text, text, int);
drop function if exists public.workbook_drop_sheets(text, text[]);
drop function if exists public.workbook_stamp(text);
drop function if exists public.workbook_row_check();
drop function if exists public.workbook_row_archive();
drop function if exists public.workbook_archive_purge();

-- ---------- application logs (2026-10-21) ----------
-- The app writes short log lines about itself: events and errors, how a run went (progress lines, how it ended, tokens), and sign-ins and
-- sign-outs. They never carry search text, candidates, companies or contact details (the app strips emails and phone numbers and cuts long
-- text). Only the company's owner and admins can read them; nobody can change or delete a line through the API; lines older than 30 days
-- are removed by the next write. The person and the company come from the sign-in, so a line cannot be written in someone else's name.
create table if not exists public.app_logs (
  id         bigint generated always as identity primary key,
  tenant_id  uuid not null references neon_auth.organization(id) on delete cascade,
  user_id    text not null,
  user_email text,
  kind       text not null check (kind in ('event', 'run', 'signin')),
  level      text not null check (level in ('info', 'warn', 'error')),
  agent      text check (agent in ('candidate', 'company', 'account')),
  message    text not null check (char_length(message) between 1 and 600),
  details    jsonb check (details is null or (jsonb_typeof(details) = 'object' and pg_column_size(details) <= 4000)),
  client_at  timestamptz,
  at         timestamptz not null default now()
);
create index if not exists app_logs_tenant_at on public.app_logs (tenant_id, at desc);
create index if not exists app_logs_at on public.app_logs (at);
alter table public.app_logs enable row level security;
drop policy if exists logs_select on public.app_logs;
create policy logs_select on public.app_logs for select to authenticated using (public.is_org_admin(tenant_id));
revoke all on public.app_logs from anonymous, public, authenticated;
grant select on public.app_logs to authenticated;   -- reading only, and only for admins; lines are written through log_write

create or replace function public.log_write(p_items jsonb) returns int language plpgsql security definer set search_path = '' as $$
declare t uuid := public.my_company(); u text := auth.user_id(); it jsonb; n int := 0;
begin
  if t is null then raise exception 'You are not part of a company' using errcode = '42501'; end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then raise exception 'Expected a list of log lines' using errcode = 'PT422'; end if;
  if jsonb_array_length(p_items) > 50 then raise exception 'At most 50 log lines per call' using errcode = 'PT422'; end if;
  for it in select * from jsonb_array_elements(p_items) loop
    insert into public.app_logs (tenant_id, user_id, user_email, kind, level, agent, message, details, client_at)
      values (t, u, auth.jwt() ->> 'email', it ->> 'kind', coalesce(it ->> 'level', 'info'), nullif(it ->> 'agent', ''),
              left(coalesce(it ->> 'message', ''), 600),
              case when jsonb_typeof(it -> 'details') = 'object' and pg_column_size(it -> 'details') <= 4000 then it -> 'details' else null end,
              case when (it ->> 'at_ms') ~ '^[0-9]{10,14}$' then to_timestamp(((it ->> 'at_ms')::bigint) / 1000.0) else null end);
    n := n + 1;
  end loop;
  delete from public.app_logs where at < now() - interval '30 days';
  return n;
end $$;
revoke all on function public.log_write(jsonb) from public, anonymous;
grant execute on function public.log_write(jsonb) to authenticated;
