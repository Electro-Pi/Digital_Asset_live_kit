# CODA Admin Deployment

The admin application is served at `https://www.coda.eracoda.ai/admin`. It is a
separate Next.js application on its own service and its own port
(`coda-admin-frontend.service`, `127.0.0.1:8092`), built with Next's
`basePath: "/admin"` and reverse-proxied under that prefix. Nginx passes the
prefix through untouched; the app expects to receive it.

`www.coda.eracoda.com` and `coda.eracoda.sa` are canonical names for the public
site but not for the dashboard: they `308` `/admin` to `www.coda.eracoda.ai`.
One origin is a requirement, not a preference — the app rejects any mutation
whose `Origin` header is not exactly `ADMIN_ORIGIN`, and a session cookie
belongs to a single origin.

## What sharing an origin costs

The dashboard previously had its own subdomain. On a shared origin, the public
site's own scripts and any third-party script it loads are same-origin with
`/admin`. Two things follow, and they are the price of this layout:

- Session and CSRF cookies are `Path=/admin` and use the `__Secure-` prefix
  rather than `__Host-`, so public pages neither receive the session nor can
  read the CSRF token out of `document.cookie`. `__Host-` was not an option: it
  requires `Path=/`.
- An XSS on the public site can still *send* same-origin `GET`s to `/admin/api`
  with the session cookie attached, because cookies are matched by request path.
  Mutations remain out of reach without the CSRF token. Auditing the public
  site's third-party scripts therefore matters more than it did before.

## Required external inputs

No DNS record and no new certificate. `/admin` rides the existing
`www.coda.eracoda.ai` vhost and its existing certificate. Still required:

1. Create a dedicated Supabase server secret for this service.
2. Choose the owner email and generate a unique password of at least 24 random
   characters in a password manager. Do not put either value in Git or shell
   history.
3. In Supabase Dashboard, open Authentication security settings and enable
   leaked-password protection. This Auth setting is not exposed through SQL or
   the connected Supabase MCP, so it cannot be enabled by the migration.

## Runtime and service account

Install a supported Node 22 LTS binary side-by-side under
`/opt/coda-node22`; do not replace `/usr/bin/node`, which is used by the public
frontend. The directory must contain `bin/node`, `bin/npm`, and `bin/npx`.

Create the locked-down service account and environment directory:

```bash
id -u coda-admin >/dev/null 2>&1 || \
  sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin coda-admin
sudo install -d -m 0750 -o root -g coda-admin /etc/coda-admin
if [ ! -e /etc/coda-admin/admin.env ]; then
  sudo install -m 0640 -o root -g coda-admin \
    frontend/coda-admin/.env.example /etc/coda-admin/admin.env
fi
```

Fill `/etc/coda-admin/admin.env` directly on the server. Generate
`ADMIN_SESSION_HMAC_SECRET` with at least 32 random bytes. The browser must never
receive the Supabase server secret or the HMAC secret.

Install the service definition:

```bash
sudo install -m 0644 systemd/coda-admin-frontend.service \
  /etc/systemd/system/coda-admin-frontend.service
sudo systemctl daemon-reload
```

## Database and owner provisioning

Apply the committed migration through the Supabase MCP, regenerate database
types, and rerun both security and performance advisors. Then use the
application's one-time provisioning command from an interactive terminal. It
creates the Supabase email/password user and the private owner allowlist row.
`read -s` keeps the password off the screen and out of shell history:

```bash
cd /home/Coda-digital-asset/Digital_Asset_live_kit/frontend/coda-admin
set -a
. /etc/coda-admin/admin.env
set +a
read -r -p "Owner email: " ADMIN_OWNER_EMAIL
read -r -s -p "Generated owner password (24+ characters): " ADMIN_OWNER_PASSWORD
printf '\n'
export ADMIN_OWNER_EMAIL ADMIN_OWNER_PASSWORD
PATH=/opt/coda-node22/bin:$PATH npm run admin:provision
unset ADMIN_OWNER_EMAIL ADMIN_OWNER_PASSWORD
```

The dashboard intentionally has no public signup, reset, or admin-management
page. For a password reset or account-disable operation, keep the dashboard
offline until every opaque session has been revoked:

1. Stop `coda-admin-frontend.service`.
2. Change or disable the dedicated Auth user out of band in Supabase.
3. Set `ADMIN_OWNER_USER_ID` to that Auth user's UUID without putting it in
   shell history, source `/etc/coda-admin/admin.env` as above, and run
   `npm run admin:invalidate-sessions`.
4. Start the service only after the invalidation command succeeds. A password
   change by itself does not invalidate the dashboard's opaque sessions.

## Nginx

Install the snippets and the rate-limit zones. All four are inert until a
server block includes them:

```bash
sudo install -m 0644 ops/nginx/coda-admin-path.conf \
  ops/nginx/coda-admin-canonical-redirect.conf \
  ops/nginx/coda-admin-proxy-body.conf /etc/nginx/snippets/
sudo install -m 0644 ops/nginx/coda-admin-limits.conf /etc/nginx/conf.d/
```

Then add four includes to `/etc/nginx/sites-available/coda-front.conf`, each
one **above** that block's `include .../coda-frontend-proxy.conf`, because the
frontend's `location /` is the catch-all:

| Server block | Include |
| --- | --- |
| `www.coda.eracoda.ai` | `snippets/coda-admin-path.conf` |
| `www.coda.eracoda.com` | `snippets/coda-admin-canonical-redirect.conf` |
| `coda.eracoda.ai` (apex) | `snippets/coda-admin-canonical-redirect.conf` |
| `coda.eracoda.sa` | `snippets/coda-admin-canonical-redirect.conf` |

The apex include has to go before its `location /`, for the same reason the TTS
include does: that block redirects from inside a location, and a `301` would
downgrade a POST to a GET.

`www.coda.eracoda.sa` needs nothing. It redirects at the server level, before
location selection, and lands on `coda.eracoda.sa`, which redirects `/admin`
properly.

Validate with `sudo nginx -t`, then `sudo systemctl reload nginx`. The reload is
what makes `/admin` public. Start `coda-admin-frontend.service` first, so the
path never answers `502`.

## Current state

Live and signable-in as of 2026-08-18. The includes are in
`/etc/nginx/sites-available/coda-front.conf` (previous version kept alongside as
`coda-front.conf.bak-20260818225347`), nginx is reloaded, the service is enabled
and running, `/etc/coda-admin/admin.env` holds real values, and the owner
(`mohamed.rabee@electropi.ai`) is provisioned. Verified end to end: login →
authenticated read of `/api/overview` → logout, with the session revoked
afterwards and both attempts recorded in `private.admin_login_attempts`.

Two things are deliberately still outstanding:

1. **`SUPABASE_SERVICE_ROLE_KEY` is the shared project key**, the same one the
   LiveKit agent and the public frontend use, not a dedicated secret. Revoking or
   rotating it currently takes down all three. Swapping in a dedicated key means
   editing that one line and restarting the service.
2. **Leaked-password protection is still off.** It is an Auth setting, reachable
   only through the Supabase Dashboard — not SQL, not the MCP.
3. **The owner password is stored in `/etc/coda-admin/admin.env`**, at the
   owner's request, rather than living only in a password manager. `systemd`
   loads that file wholesale, so after the next restart the plaintext is
   readable from `/proc/<pid>/environ` by the `coda-admin` user. The app never
   reads `ADMIN_OWNER_PASSWORD`; only `npm run admin:provision` does. Deleting
   the line costs nothing once the password is in a manager.

`private.admin_audit_log` is empty and should be: it records data mutations
(quota changes, booking transitions), not sign-ins. Logins live in
`private.admin_login_attempts`.

## Build and activation

```bash
sudo chmod 0600 \
  /home/Coda-digital-asset/Digital_Asset_live_kit/.env.local \
  /home/Coda-digital-asset/Digital_Asset_live_kit/frontend/coda-frontend/coda-next/.env.local
sudo ./scripts/deploy-coda-admin.sh
sudo systemctl enable coda-admin-frontend.service
```

Verify security headers, login throttling, session timeouts, ordinary-user
rejection, quota mutations, audit entries, and that port 8092 is bound only to
`127.0.0.1`. Specific to the path mount, check that:

- `https://www.coda.eracoda.ai/admin` reaches the dashboard and
  `/administration` (or any other name merely starting with "admin") still
  reaches the public site;
- `/admin/_next/static/...` returns `200` — a `404` here means the build lost
  its `basePath` and the dashboard will render unstyled;
- the session cookie is issued with `Path=/admin`, not `Path=/`;
- `/admin` on `www.coda.eracoda.com` and `coda.eracoda.sa` returns `308`;
- `/admin` responses carry exactly one `Content-Security-Policy` header. Nginx
  deliberately adds no security headers on this path; the app sets them all in
  `next.config.ts`. A second copy from nginx would be silently merged.
