# Newsletter-Service

Self-hosted newsletter system built with **FastAPI + SQLite + SMTP**.

## Features

- Double Opt-In subscription flow (`/subscribe` + `/confirm`)
- DSGVO/GDPR-friendly unsubscribe (`/unsubscribe`)
- Admin-only API routes via HTTP Basic auth (`/send`, `/subscribers`)
- Admin dashboard at `/admin` with login form, subscriber list, and newsletter composer
- Embeddable signup widget at `/embed` (usable via `<iframe>`)
- SMTP-based email delivery with plain text + HTML newsletters
- SQLite persistence
- CORS configured for `https://festas-builds.com`
- Basic rate limiting on `/subscribe`

## Environment Variables

Copy `.env.example` to `.env` and set values:

- `BASE_URL`
- `DATABASE_PATH`
- `RATE_LIMIT_PER_MINUTE`
- `SESSION_SECRET`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `ADMIN_USER`
- `ADMIN_PASSWORD`

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## API Endpoints

- `POST /subscribe` – accepts JSON (`{"email":"..."}`) or form data
- `GET /confirm?token=...`
- `GET /unsubscribe?token=...`
- `POST /send` – Basic auth required, body:
  ```json
  {
    "subject": "Hello",
    "body_text": "Text body",
    "body_html": "<p>HTML body</p>"
  }
  ```
- `GET /subscribers` – Basic auth required
- `GET /embed` – embeddable iframe form
- `GET /admin` – dashboard login + UI

## Embed Usage

```html
<iframe src="https://newsletter.festas-builds.com/embed" width="420" height="240" style="border:0;"></iframe>
```

## Docker

```bash
docker compose up --build
```

Service is exposed on port `8000`.
