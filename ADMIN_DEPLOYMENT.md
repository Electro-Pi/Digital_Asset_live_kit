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
cd /home/bassem/Coda/Digital_Asset_live_kit/frontend/coda-admin
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

Live as of 2026-08-23. `src/lib/` was recovered, the app builds
(typecheck clean, 45 tests passing), `coda-admin-frontend.service` is enabled and
healthy on `127.0.0.1:8092`, and the `/admin` mount is uncommented in
`sites-available/coda-front.conf` and reloaded. Verified through nginx: the
login page returns `200`, `/admin/_next/static/**` resolves, exactly one
`Content-Security-Policy` header is present, `/admin/api/*` returns `401`
unauthenticated, and `/administration` still reaches the public site.

`/admin/keys` issues scoped API keys — see `AGENT_API.md`.

Public over TLS since 2026-08-23 at <https://www.coda.eracoda.ai/admin>, which
is what `ADMIN_ORIGIN` is set to, so the app's exact-Origin check and its
`__Secure-` cookies both line up with reality. `/admin` `308`s to that name from
`coda.eracoda.ai`, `www.coda.eracoda.com` and `coda.eracoda.com`.

### Why src/lib went missing, and why it can't again

It was never forgotten — it was **ignored**. Line 17 of the repo-root
`.gitignore` is `lib/`, inherited from the Python template (it sits beside
`lib64/`, `parts/`, `sdist/` and is meant for a virtualenv). With no leading
slash git applies it at every depth, so it silently swallowed
`frontend/coda-admin/src/lib/` — nine modules the entire dashboard imports.
`next build` then failed on a missing `./src/lib/base-path`.

`.gitignore` now ends with `!frontend/coda-admin/src/lib/`, which un-ignores the
directory (the directory itself, because git will not re-include a path whose
parent is excluded). Verify after any `.gitignore` edit:

```bash
git check-ignore -v frontend/coda-admin/src/lib/auth.ts   # must print nothing
```

### Rebuilding

The checkout belongs to `bassem`; the service runs as `coda-admin`. Two ACLs
keep both working, and both must survive a rebuild:

```bash
# The service must read the build output. `next build` writes files that
# inherit no ACL, so re-apply after every build (deploy-coda-admin.sh does).
sudo setfacl -R -m u:coda-admin:rX frontend/coda-admin

# .next/cache is written by the service at runtime AND by the build. The
# default entries matter: the unit sets UMask=0077, and named ACL entries are
# the only thing that survives it for files the other account creates.
sudo setfacl -R -m u:bassem:rwX -m d:u:bassem:rwX frontend/coda-admin/.next/cache
sudo setfacl -R -m u:coda-admin:rwX -m d:u:coda-admin:rwX frontend/coda-admin/.next/cache
```

Without the second pair, a rebuild as `bassem` fails with
`EACCES ... .next/cache/.rscinfo`.

### Outstanding

1. **`SUPABASE_SERVICE_ROLE_KEY` is the shared project key**, the same one the
   LiveKit agent and the public frontend use, not a dedicated secret. Revoking or
   rotating it currently takes down all three. Swapping in a dedicated key means
   editing that one line and restarting the service.
2. **Leaked-password protection is still off.** It is an Auth setting, reachable
   only through the Supabase Dashboard — not SQL, not the MCP.
3. **The owner account carried over from the previous deployment.** One active
   `owner` row exists in `private.admin_users`; its password is not held on this
   host. If it is unknown, reset it out of band in Supabase and run
   `npm run admin:invalidate-sessions` before bringing the dashboard back up — a
   password change alone does not invalidate the opaque sessions.
4. **`API_KEY_PEPPER` is now also in `/etc/coda-admin/admin.env`**, byte-identical
   to `coda-next/.env.local` and `TTS-Endpoint-api/.env`. Rotating it invalidates
   every issued key everywhere, so all three must change together.

## Build and activation

```bash
sudo chmod 0600 \
  /home/bassem/Coda/Digital_Asset_live_kit/.env.local \
  /home/bassem/Coda/Digital_Asset_live_kit/frontend/coda-frontend/coda-next/.env.local
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
