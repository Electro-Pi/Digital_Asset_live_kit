# Podcast: bookings, alerts, and scheduled episodes

Three things run off one feature area:

| Piece | Where | Storage |
| --- | --- | --- |
| Seat booking form | `/podcast` → `POST /api/podcast/seat` | `public.coda_podcast` |
| “اشترك في التنبيهات” | `/podcast` → `POST /api/podcast/subscribe` | `public.coda_podcast_subscribers` |
| Episode upload + schedule | `/admin/podcast` | `public.coda_podcast_episodes` + `podcast-video` bucket |

All three tables have RLS on with **no policies**, exactly like `dialect_entries`:
the public routes reach them with the service-role key, and the dashboard
reaches them through `SECURITY DEFINER` RPCs that re-check the admin session and
write the audit log. Neither `anon` nor `authenticated` can read a row.

Migrations: `supabase/migrations/20260830120000_coda_podcast.sql` and
`20260830130000_coda_podcast_report_replaced_video.sql` — both already applied
to the `Coda` project, `weowkfsgbcjaacuomyrd`.

## How an episode goes live

1. An operator opens **/admin → Podcast → New episode**, picks a video file, and
   sets the date and time it should appear. **That time is Riyadh time (AST,
   UTC+3)** — the form says so, and converts with a fixed `+03:00` offset before
   sending. Saudi Arabia has observed no DST since 1990, so that offset is exact.
2. The file uploads straight from the browser to Supabase Storage using a
   one-shot signed URL. It never passes through the dashboard, which is what
   makes a 500 MB episode viable.
3. `GET /api/podcast` shows episodes where `status = 'scheduled'` **and**
   `publish_at <= now()`. Visibility is therefore evaluated per request — the
   episode is hidden until its instant and visible on the first request after
   it, with no job in between.
4. Separately, the alert dispatcher (below) mails every active subscriber the
   moment that instant passes.

Statuses: **Draft** stays hidden and sends nothing. **Scheduled** publishes and
emails at the set time. **Archived** pulls a published episode back off the page.
Pushing a published episode's time back into the future re-arms its alert.

## Wiring the alert email (Gmail)

Mail goes out over SMTP as `smtp.gmail.com:587` with STARTTLS, authenticating
with a **Google app password**. Google removed "less secure app access" in 2022,
so this is the only password-shaped credential Gmail still accepts.

> Microsoft 365 was tried first and abandoned: `electropi.ai` has Security
> Defaults on, which rejects every form of basic authentication — app passwords
> included — with `535 5.7.139 … locked by your organization's security defaults
> policy`. Getting mail out of that mailbox needs OAuth2 with an Entra app
> registration. If you ever go back to it, the change is confined to
> `src/lib/email/mailer.ts`; nothing else in this feature is provider-aware.

### Creating the app password

1. **2-Step Verification must be on** for the sending account. Without it Google
   does not offer app passwords at all — this is the usual reason the page looks
   empty. <https://myaccount.google.com/signinoptions/twosv>
2. Go to <https://myaccount.google.com/apppasswords>, name it `codapodcast`,
   and copy the 16 characters.
3. **Strip the spaces.** Google prints them in four groups of four for
   legibility; they are not part of the password. (The mailer strips whitespace
   defensively, but the value is clearer without them.)

On a Google Workspace domain an admin can disable app passwords org-wide, in
which case the page returns nothing and OAuth2 is the only route.

### Configure the app

Add to `frontend/coda-frontend/coda-next/.env.local` and restart the frontend
service:

```bash
SMTP_USER="<the Gmail address that owns the app password>"
SMTP_PASSWORD="<16 characters, no spaces>"
SMTP_FROM="<same address, unless verified under Send mail as>"
SMTP_FROM_NAME="Coda Podcast"

# Bearer secret the dispatcher timer presents. Nothing sends without it.
#   openssl rand -hex 32
PODCAST_CRON_SECRET="<32 random hex bytes>"

# Optional. Defaults shown.
# SMTP_HOST="smtp.gmail.com"
# SMTP_PORT="587"
# PODCAST_PUBLIC_URL="https://www.coda.eracoda.ai"   # falls back to APP_URL
# PODCAST_MAIL_INTERVAL_MS="1200"   # pause between messages
# PODCAST_MAIL_MAX="5000"           # ceiling per episode per run
```

**`SMTP_FROM` must be the authenticated account**, or an address verified under
Gmail → Settings → Accounts → *Send mail as*. Otherwise Gmail rewrites the From
header to the authenticated address. That failure is quiet — the mail arrives,
from the wrong sender, and nothing logs it — so check the From line on the test
message rather than assuming.

### Know the sending limits before launch day

Gmail caps **recipients per rolling 24 hours**, and the alert list is a blast:

| Account type | Roughly |
| --- | --- |
| Free `@gmail.com` | 500 recipients/day |
| Google Workspace | 2,000 recipients/day |

Exceeding it locks SMTP sending for up to 24 hours. If the list outgrows that —
or if bounce and complaint handling starts to matter — move to a dedicated
sending service (Resend, SES, Postmark) with SPF/DKIM on the domain. Only
`mailer.ts` changes; the dispatcher, templates and database do not.

### Prove it before you rely on it

```bash
cd frontend/coda-frontend/coda-next

# login + handshake only
/opt/coda-node22/bin/node scripts/verify-podcast-mail.cjs

# ...and actually deliver one message
/opt/coda-node22/bin/node scripts/verify-podcast-mail.cjs you@electropi.ai
```

| Message | Cause |
| --- | --- |
| `535 5.7.8` / Username and Password not accepted | Wrong app password, or 2-Step Verification is off |
| `534 5.7.9` Application-specific password required | The account password was used instead of an app password |
| `5.7.14` / unusual activity | Google blocked the sign-in; approve it on the account security page |
| `ETIMEDOUT` on connect | Outbound 587 is blocked from the server |
| Delivered, but From is wrong | `SMTP_FROM` is not the account and is not verified under *Send mail as* |

## The dispatcher timer

`POST /api/podcast/dispatch` claims every episode whose time has arrived and
mails the alert list. The claim stamps `notified_at` inside the same statement
that reads the rows, so overlapping runs cannot double-send. Run it every
minute:

```ini
# /etc/systemd/system/coda-podcast-alerts.service
[Unit]
Description=Coda podcast episode alerts
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/coda-podcast-alerts.env
ExecStart=/usr/bin/curl -fsS --max-time 900 -X POST \
  -H "Authorization: Bearer ${PODCAST_CRON_SECRET}" \
  http://127.0.0.1:8090/api/podcast/dispatch
```

```ini
# /etc/systemd/system/coda-podcast-alerts.timer
[Unit]
Description=Run Coda podcast episode alerts every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s

[Install]
WantedBy=timers.target
```

```bash
sudo install -m 0600 /dev/stdin /etc/coda-podcast-alerts.env <<'ENV'
PODCAST_CRON_SECRET=<the same value as in .env.local>
ENV
sudo systemctl daemon-reload
sudo systemctl enable --now coda-podcast-alerts.timer
sudo systemctl start coda-podcast-alerts.service   # one manual run
journalctl -u coda-podcast-alerts.service -n 40
```

Without the timer nothing is emailed, but episodes still appear on `/podcast` on
schedule — publication and notification are independent on purpose.

## Recovering a missed alert

The claim is burned before the send, so a run that dies mid-blast will not retry
on its own: a missed alert is recoverable, a duplicate blast to every subscriber
is not. To re-arm one episode:

```sql
update public.coda_podcast_episodes set notified_at = null where id = '<uuid>';
```

## Unsubscribing

Every alert carries a footer link and a `List-Unsubscribe` header pointing at
`/api/podcast/unsubscribe?token=…`. The token is the whole authorisation, so it
is long, random, unique, and stored in a column that holds nothing else.
Re-subscribing from the page revives the row — that is the only way back on the
list without an account.
