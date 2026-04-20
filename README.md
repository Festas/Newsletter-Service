# Newsletter-Service

Self-hosted newsletter system built with **FastAPI + SQLite + SMTP**.

A full-featured, privacy-respecting newsletter platform you can run on your own server — no third-party services required.

## Features

### Core
- Double Opt-In subscription flow (`/subscribe` + `/confirm`)
- GDPR/DSGVO-friendly unsubscribe with one-click `List-Unsubscribe` headers (RFC 8058)
- SMTP-based email delivery with retry logic and exponential backoff
- SQLite persistence with WAL mode
- CORS configured for your domain

### Admin Dashboard
- Modern, responsive admin UI with [Pico CSS](https://picocss.com)
- **Markdown editor** (EasyMDE) for composing newsletters with live HTML preview
- **Newsletter drafts** — save in-progress newsletters and come back later
- **Send history** with per-newsletter open/click analytics
- **Subscriber management** — search, filter, paginate, add/remove, tags/segments
- **CSV import/export** for subscribers
- **Confirmation dialog** before sending to prevent accidental sends
- **Subscriber growth chart** (Chart.js)
- **Delivery failure log** to track bounces and errors
- **Webhook management** for event-driven integrations

### Email Templates
- Polished, responsive HTML email template for newsletters
- Branded confirmation email with call-to-action button
- Configurable brand name via `BRAND_NAME` env var
- Automatic `List-Unsubscribe` and `List-Unsubscribe-Post` headers
- `Reply-To` and `Message-ID` headers for deliverability

### Analytics & Tracking
- **Open tracking** via invisible tracking pixel
- **Click tracking** via redirect endpoint that wraps links
- Per-newsletter stats: open rate, click rate
- Subscriber growth chart over time

### API
- RESTful API with HTTP Basic auth and optional API key (`X-API-Key`)
- `POST /subscribe` — subscribe (JSON or form data)
- `GET /confirm?token=...` — confirm subscription
- `GET /unsubscribe?token=...` — unsubscribe
- `POST /send` — send newsletter (admin)
- `GET /subscribers` — list subscribers with search/pagination
- `GET /subscribers/{id}` — get single subscriber
- `DELETE /subscribers/{id}` — remove subscriber
- `GET /newsletters` — list newsletters
- `GET /newsletters/{id}` — get newsletter with analytics
- `GET /health` — health check (DB connectivity)

### Public Pages
- **Newsletter archive** (`/archive`) — public-facing page for browsing past issues
- **Embeddable signup widget** (`/embed`) — polished iframe form with animations
- Styled confirmation and unsubscribe pages

### Security
- Token expiry for confirmation links (48 hours)
- Token rotation on confirm/unsubscribe (old links become invalid)
- Bounded rate limiting on `/subscribe` and `/admin/login`
- Timing-safe credential comparison (`secrets.compare_digest`)
- Session-based admin authentication

### Operations
- Structured logging throughout
- Health check endpoint (`/health`)
- Docker and Docker Compose support
- CI pipeline with GitHub Actions (tests + linting)
- Webhook notifications on key events

## Environment Variables

Copy `.env.example` to `.env` and set values:

| Variable | Description | Default |
|---|---|---|
| `BASE_URL` | Public URL of the service | `http://localhost:8000` |
| `DATABASE_PATH` | SQLite database file path | `newsletter.db` |
| `RATE_LIMIT_PER_MINUTE` | Subscribe rate limit per IP | `5` |
| `SESSION_SECRET` | Session cookie secret | (change this!) |
| `SMTP_HOST` | SMTP server hostname | — |
| `SMTP_PORT` | SMTP port (587/465/25) | `587` |
| `SMTP_USER` | SMTP username | — |
| `SMTP_PASSWORD` | SMTP password | — |
| `SMTP_FROM` | From address for emails | `newsletter@example.com` |
| `REPLY_TO` | Reply-To address | (same as SMTP_FROM) |
| `ADMIN_USER` | Admin username | `admin` |
| `ADMIN_PASSWORD` | Admin password | (change this!) |
| `BRAND_NAME` | Brand name shown in emails and UI | `Newsletter` |
| `API_KEY` | Optional API key for programmatic access | — |

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Run Tests

```bash
pip install pytest httpx
python -m pytest tests/ -v
```

## Lint

```bash
pip install ruff
ruff check app/ tests/
```

## API Endpoints

### Public
- `POST /subscribe` — accepts JSON (`{"email":"..."}`) or form data
- `GET /confirm?token=...` — confirm subscription
- `GET /unsubscribe?token=...` — unsubscribe
- `GET /archive` — public newsletter archive
- `GET /archive/{id}` — view a past newsletter
- `GET /embed` — embeddable iframe signup form
- `GET /health` — health check

### Admin (requires HTTP Basic auth or `X-API-Key` header)
- `POST /send` — send newsletter
- `GET /subscribers` — list all subscribers
- `GET /subscribers/{id}` — get subscriber details
- `DELETE /subscribers/{id}` — remove subscriber
- `GET /newsletters` — list newsletters
- `GET /newsletters/{id}` — get newsletter with analytics

### Tracking
- `GET /track/open?newsletter_id=...&subscriber_id=...` — open tracking pixel
- `GET /track/click?newsletter_id=...&url=...` — click tracking redirect

### Admin Dashboard
- `GET /admin` — dashboard UI
- `POST /admin/login` — login
- `POST /admin/logout` — logout
- `POST /admin/send` — send from dashboard
- `POST /admin/draft/save` — save draft
- `GET /admin/draft/{id}` — load draft for editing
- `POST /admin/draft/{id}/delete` — delete draft
- `POST /admin/subscriber/add` — add subscriber manually
- `POST /admin/subscriber/{id}/delete` — remove subscriber
- `GET /admin/subscribers/export` — export CSV
- `POST /admin/subscribers/import` — import CSV
- `POST /admin/webhooks/add` — add webhook
- `POST /admin/webhooks/{id}/delete` — delete webhook

## Embed Usage

```html
<iframe src="https://newsletter.festas-builds.com/embed" width="420" height="240" style="border:0;"></iframe>
```

## Docker

```bash
docker compose up --build
```

Service is exposed on port `8000`.

