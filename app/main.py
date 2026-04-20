import csv
import io
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from app.auth import check_login_rate_limit, require_admin, verify_admin_credentials
from app.database import (
    add_subscriber_manual,
    confirm_by_token,
    create_newsletter,
    create_or_update_subscriber,
    create_webhook,
    delete_newsletter,
    delete_subscriber,
    delete_webhook,
    get_all_tags,
    get_delivery_failures,
    get_newsletter,
    get_newsletter_analytics,
    get_subscriber,
    get_subscriber_by_email,
    get_subscriber_count_by_date,
    init_db,
    list_confirmed_subscribers,
    list_newsletters,
    list_scheduled_newsletters,
    list_subscribers,
    list_webhooks,
    record_analytics_event,
    record_delivery,
    unsubscribe_by_token,
    update_newsletter,
    update_subscriber_notes,
    update_subscriber_tags,
)
from app.email_service import EmailService
from app.webhooks import fire_webhook

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):  # type: ignore[no-untyped-def]
    init_db()
    logger.info("Newsletter Service started")
    yield
    logger.info("Newsletter Service shutting down")


app = FastAPI(
    title="Newsletter Service",
    description="Self-hosted newsletter service with double opt-in, admin dashboard, analytics, and more.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://festas-builds.com", "https://www.festas-builds.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "newsletter-session-secret"),
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
email_service = EmailService()

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "5"))
MAX_BUCKET_SIZE = 1000
_rate_bucket: dict[str, list[float]] = {}

BRAND_NAME = os.getenv("BRAND_NAME", "Newsletter")


class SendPayload(BaseModel):
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    template: str = "minimal"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.fullmatch(email.strip()))


def _get_base_url(request: Request) -> str:
    return os.getenv("BASE_URL") or str(request.base_url).rstrip("/")


def _is_admin_logged_in(request: Request) -> bool:
    return bool(request.session.get("admin_logged_in"))


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - 60
    entries = [timestamp for timestamp in _rate_bucket.get(ip, []) if timestamp > window_start]
    if len(entries) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many subscribe requests. Please try again later.",
        )
    entries.append(now)
    _rate_bucket[ip] = entries

    # Bounded cleanup to prevent memory growth
    if len(_rate_bucket) > MAX_BUCKET_SIZE:
        stale = [k for k, v in _rate_bucket.items() if not any(t > window_start for t in v)]
        for k in stale:
            del _rate_bucket[k]


async def _send_newsletter_to_confirmed(
    request: Request,
    subject: str,
    body_text: str | None,
    body_html: str | None,
    newsletter_id: int | None = None,
) -> int:
    if not body_text and not body_html:
        raise HTTPException(status_code=400, detail="body_text or body_html is required")

    subscribers = list_confirmed_subscribers()
    base_url = _get_base_url(request)

    # Inject click tracking if we have a newsletter ID
    if newsletter_id and body_html:
        body_html = email_service.inject_tracking_links(body_html, base_url, newsletter_id)

    sent = 0
    for subscriber in subscribers:
        unsubscribe_url = f"{base_url}/unsubscribe?token={subscriber['token']}"
        tracking_pixel_url = None
        if newsletter_id:
            tracking_pixel_url = f"{base_url}/track/open?newsletter_id={newsletter_id}&subscriber_id={subscriber['id']}"

        try:
            await email_service.send_newsletter(
                recipient=subscriber["email"],
                subject=subject,
                text_body=body_text,
                html_body=body_html,
                unsubscribe_url=unsubscribe_url,
                tracking_pixel_url=tracking_pixel_url,
                newsletter_id=newsletter_id,
            )
            sent += 1
            if newsletter_id:
                record_delivery(newsletter_id, subscriber["id"], "sent")
        except Exception as exc:
            logger.error("Failed to send to %s: %s", subscriber["email"], exc)
            if newsletter_id:
                record_delivery(newsletter_id, subscriber["id"], "failed", str(exc))

    return sent


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@app.post("/subscribe", tags=["Subscribers"])
async def subscribe(request: Request, email: str | None = Form(default=None)) -> JSONResponse:
    _check_rate_limit(request)

    payload_email = email
    if payload_email is None:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
            payload_email = payload.get("email")

    if not payload_email or not _is_valid_email(payload_email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    token = secrets.token_urlsafe(32)
    result = create_or_update_subscriber(payload_email.lower().strip(), token)

    if result["status"] == "already_confirmed":
        return JSONResponse({"message": "Email already subscribed"})

    base_url = _get_base_url(request)
    confirm_url = f"{base_url}/confirm?token={token}"
    await email_service.send_confirmation_email(payload_email, confirm_url)

    await fire_webhook("subscriber.pending", {"email": payload_email})

    return JSONResponse(
        {"message": "Subscription created. Please confirm via email."},
        status_code=status.HTTP_201_CREATED,
    )


@app.get("/confirm", response_class=HTMLResponse, tags=["Subscribers"])
def confirm(request: Request, token: str) -> HTMLResponse:
    if confirm_by_token(token):
        return templates.TemplateResponse("confirm_success.html", {"request": request, "brand_name": BRAND_NAME})
    return templates.TemplateResponse("confirm_fail.html", {"request": request, "brand_name": BRAND_NAME}, status_code=400)


@app.get("/unsubscribe", response_class=HTMLResponse, tags=["Subscribers"])
def unsubscribe(request: Request, token: str) -> HTMLResponse:
    if unsubscribe_by_token(token):
        return templates.TemplateResponse("unsubscribe_success.html", {"request": request, "brand_name": BRAND_NAME})
    return templates.TemplateResponse("unsubscribe_fail.html", {"request": request, "brand_name": BRAND_NAME}, status_code=400)


# ---------------------------------------------------------------------------
# Tracking endpoints
# ---------------------------------------------------------------------------

# 1x1 transparent PNG pixel
_TRACKING_PIXEL = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@app.get("/track/open", tags=["Analytics"])
def track_open(
    newsletter_id: int = Query(...),
    subscriber_id: int = Query(default=0),
) -> StreamingResponse:
    if subscriber_id:
        try:
            record_analytics_event(newsletter_id, subscriber_id, "open")
        except Exception:
            pass
    return StreamingResponse(
        io.BytesIO(_TRACKING_PIXEL),
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/track/click", tags=["Analytics"])
def track_click(
    url: str = Query(...),
    newsletter_id: int = Query(default=0),
    subscriber_id: int = Query(default=0),
) -> RedirectResponse:
    if newsletter_id and subscriber_id:
        try:
            record_analytics_event(newsletter_id, subscriber_id, "click", url=url)
        except Exception:
            pass
    return RedirectResponse(url=unquote(url))


# ---------------------------------------------------------------------------
# API endpoints (Basic auth / API key)
# ---------------------------------------------------------------------------

@app.post("/send", tags=["API"])
async def send_newsletter_api(
    payload: SendPayload,
    request: Request,
    _: str = Depends(require_admin),
) -> JSONResponse:
    nl_id = create_newsletter(
        subject=payload.subject,
        body_text=payload.body_text,
        body_html=payload.body_html,
        template=payload.template,
        status="sent",
    )
    sent = await _send_newsletter_to_confirmed(
        request,
        subject=payload.subject,
        body_text=payload.body_text,
        body_html=payload.body_html,
        newsletter_id=nl_id,
    )
    update_newsletter(nl_id, recipient_count=sent, sent_at=__import__("app.database", fromlist=["_now_iso"])._now_iso())
    await fire_webhook("newsletter.sent", {"newsletter_id": nl_id, "sent": sent})
    return JSONResponse({"sent": sent, "newsletter_id": nl_id})


@app.get("/subscribers", tags=["API"])
def subscribers_api(
    _: str = Depends(require_admin),
    search: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    all_subscribers, total = list_subscribers(search=search, page=page, per_page=per_page)
    confirmed_count = sum(1 for s in all_subscribers if s["confirmed"])
    return {"count": total, "confirmed": confirmed_count, "page": page, "items": all_subscribers}


@app.get("/subscribers/{subscriber_id}", tags=["API"])
def get_subscriber_api(subscriber_id: int, _: str = Depends(require_admin)) -> dict[str, Any]:
    sub = get_subscriber(subscriber_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return sub


@app.delete("/subscribers/{subscriber_id}", tags=["API"])
def delete_subscriber_api(subscriber_id: int, _: str = Depends(require_admin)) -> JSONResponse:
    if delete_subscriber(subscriber_id):
        return JSONResponse({"deleted": True})
    raise HTTPException(status_code=404, detail="Subscriber not found")


@app.get("/newsletters", tags=["API"])
def newsletters_api(
    _: str = Depends(require_admin),
    status_filter: str = Query(default=""),
    page: int = Query(default=1, ge=1),
) -> dict[str, Any]:
    items, total = list_newsletters(status_filter=status_filter, page=page)
    return {"count": total, "page": page, "items": items}


@app.get("/newsletters/{newsletter_id}", tags=["API"])
def get_newsletter_api(newsletter_id: int, _: str = Depends(require_admin)) -> dict[str, Any]:
    nl = get_newsletter(newsletter_id)
    if not nl:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    nl["analytics"] = get_newsletter_analytics(newsletter_id)
    return nl


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Operations"])
def health() -> dict[str, str]:
    try:
        from app.database import get_connection
        with get_connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "healthy", "database": "ok"}
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service unhealthy")


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse, tags=["Admin"])
def admin_dashboard(
    request: Request,
    page: int = Query(default=1, ge=1),
    search: str = Query(default=""),
    tab: str = Query(default="compose"),
) -> HTMLResponse:
    if not _is_admin_logged_in(request):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": None, "brand_name": BRAND_NAME})

    all_subscribers, total_subscribers = list_subscribers(search=search, page=page)
    confirmed_count = sum(1 for s in all_subscribers if s["confirmed"])
    total_pages = max(1, (total_subscribers + 49) // 50)

    sent_newsletters, total_newsletters = list_newsletters(status_filter="sent")
    draft_newsletters, total_drafts = list_newsletters(status_filter="draft")
    all_tags = get_all_tags()

    growth_data = get_subscriber_count_by_date()
    failures = get_delivery_failures(limit=20)

    # Add analytics to sent newsletters
    for nl in sent_newsletters:
        nl["analytics"] = get_newsletter_analytics(nl["id"])

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "subscribers": all_subscribers,
            "count": total_subscribers,
            "confirmed_count": confirmed_count,
            "unconfirmed_count": total_subscribers - confirmed_count,
            "page": page,
            "total_pages": total_pages,
            "search": search,
            "tab": tab,
            "sent_newsletters": sent_newsletters,
            "draft_newsletters": draft_newsletters,
            "all_tags": all_tags,
            "growth_data": growth_data,
            "failures": failures,
            "message": None,
            "error": None,
            "brand_name": BRAND_NAME,
        },
    )


@app.post("/admin/login", response_class=HTMLResponse, tags=["Admin"])
def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse:
    ip = request.client.host if request.client else "unknown"
    check_login_rate_limit(ip)

    if not verify_admin_credentials(username, password):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "Invalid credentials", "brand_name": BRAND_NAME},
            status_code=401,
        )

    request.session["admin_logged_in"] = True
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/logout", tags=["Admin"])
def admin_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/send", response_class=HTMLResponse, tags=["Admin"])
async def admin_send_newsletter(
    request: Request,
    subject: str = Form(...),
    body_text: str = Form(default=""),
    body_html: str = Form(default=""),
    template: str = Form(default="minimal"),
) -> HTMLResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)  # type: ignore[return-value]

    try:
        nl_id = create_newsletter(
            subject=subject,
            body_text=body_text or None,
            body_html=body_html or None,
            template=template,
            status="sent",
        )
        sent = await _send_newsletter_to_confirmed(
            request,
            subject=subject,
            body_text=body_text or None,
            body_html=body_html or None,
            newsletter_id=nl_id,
        )
        from app.database import _now_iso
        update_newsletter(nl_id, recipient_count=sent, sent_at=_now_iso())
        await fire_webhook("newsletter.sent", {"newsletter_id": nl_id, "sent": sent})
        message = f"Newsletter sent to {sent} subscriber(s)."
        error = None
    except HTTPException as exc:
        message = None
        error = str(exc.detail)

    return RedirectResponse(url=f"/admin?tab=compose&msg={message or error or ''}", status_code=303)  # type: ignore[return-value]


@app.post("/admin/draft/save", response_class=HTMLResponse, tags=["Admin"])
async def admin_save_draft(
    request: Request,
    subject: str = Form(default=""),
    body_text: str = Form(default=""),
    body_html: str = Form(default=""),
    template: str = Form(default="minimal"),
    draft_id: int = Form(default=0),
) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)

    if draft_id:
        update_newsletter(draft_id, subject=subject, body_text=body_text or None, body_html=body_html or None, template=template)
    else:
        create_newsletter(subject=subject, body_text=body_text, body_html=body_html, template=template, status="draft")

    return RedirectResponse(url="/admin?tab=drafts", status_code=303)


@app.get("/admin/draft/{draft_id}", response_class=HTMLResponse, tags=["Admin"])
def admin_load_draft(request: Request, draft_id: int) -> HTMLResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)  # type: ignore[return-value]

    draft = get_newsletter(draft_id)
    if not draft or draft["status"] != "draft":
        return RedirectResponse(url="/admin?tab=drafts", status_code=303)  # type: ignore[return-value]

    all_subscribers, total_subscribers = list_subscribers()
    confirmed_count = sum(1 for s in all_subscribers if s["confirmed"])
    sent_newsletters, _ = list_newsletters(status_filter="sent")
    draft_newsletters, _ = list_newsletters(status_filter="draft")
    all_tags = get_all_tags()
    growth_data = get_subscriber_count_by_date()
    failures = get_delivery_failures(limit=20)
    for nl in sent_newsletters:
        nl["analytics"] = get_newsletter_analytics(nl["id"])

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "subscribers": all_subscribers,
            "count": total_subscribers,
            "confirmed_count": confirmed_count,
            "unconfirmed_count": total_subscribers - confirmed_count,
            "page": 1,
            "total_pages": 1,
            "search": "",
            "tab": "compose",
            "sent_newsletters": sent_newsletters,
            "draft_newsletters": draft_newsletters,
            "all_tags": all_tags,
            "growth_data": growth_data,
            "failures": failures,
            "message": None,
            "error": None,
            "brand_name": BRAND_NAME,
            "draft": draft,
        },
    )


@app.post("/admin/draft/{draft_id}/delete", tags=["Admin"])
def admin_delete_draft(request: Request, draft_id: int) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)
    delete_newsletter(draft_id)
    return RedirectResponse(url="/admin?tab=drafts", status_code=303)


@app.post("/admin/subscriber/add", tags=["Admin"])
def admin_add_subscriber(
    request: Request,
    email: str = Form(...),
    tags: str = Form(default=""),
    notes: str = Form(default=""),
) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)
    if _is_valid_email(email):
        add_subscriber_manual(email.lower().strip(), tags=tags, notes=notes)
    return RedirectResponse(url="/admin?tab=subscribers", status_code=303)


@app.post("/admin/subscriber/{subscriber_id}/delete", tags=["Admin"])
def admin_delete_subscriber(request: Request, subscriber_id: int) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)
    delete_subscriber(subscriber_id)
    return RedirectResponse(url="/admin?tab=subscribers", status_code=303)


@app.post("/admin/subscriber/{subscriber_id}/tags", tags=["Admin"])
def admin_update_tags(
    request: Request,
    subscriber_id: int,
    tags: str = Form(default=""),
) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)
    update_subscriber_tags(subscriber_id, tags)
    return RedirectResponse(url="/admin?tab=subscribers", status_code=303)


# ---------------------------------------------------------------------------
# CSV import / export
# ---------------------------------------------------------------------------

@app.get("/admin/subscribers/export", tags=["Admin"])
def admin_export_subscribers(request: Request) -> StreamingResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)  # type: ignore[return-value]

    all_subscribers, _ = list_subscribers(per_page=100000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "confirmed", "subscribed_at", "confirmed_at", "tags", "notes"])
    for s in all_subscribers:
        writer.writerow([s["email"], s["confirmed"], s["subscribed_at"], s.get("confirmed_at", ""), s.get("tags", ""), s.get("notes", "")])
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


@app.post("/admin/subscribers/import", tags=["Admin"])
async def admin_import_subscribers(request: Request) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)

    form = await request.form()
    upload = form.get("file")
    if not upload:
        return RedirectResponse(url="/admin?tab=subscribers", status_code=303)

    content = (await upload.read()).decode("utf-8-sig")  # type: ignore[union-attr]
    reader = csv.DictReader(io.StringIO(content))
    count = 0
    for row in reader:
        email = row.get("email", "").strip().lower()
        if email and _is_valid_email(email):
            tags = row.get("tags", "")
            notes = row.get("notes", "")
            add_subscriber_manual(email, tags=tags, notes=notes)
            count += 1

    logger.info("Imported %d subscribers from CSV", count)
    return RedirectResponse(url="/admin?tab=subscribers", status_code=303)


# ---------------------------------------------------------------------------
# Public archive
# ---------------------------------------------------------------------------

@app.get("/archive", response_class=HTMLResponse, tags=["Public"])
def public_archive(request: Request, page: int = Query(default=1, ge=1)) -> HTMLResponse:
    newsletters, total = list_newsletters(status_filter="sent", page=page)
    total_pages = max(1, (total + 19) // 20)
    return templates.TemplateResponse(
        "archive.html",
        {
            "request": request,
            "newsletters": newsletters,
            "page": page,
            "total_pages": total_pages,
            "brand_name": BRAND_NAME,
        },
    )


@app.get("/archive/{newsletter_id}", response_class=HTMLResponse, tags=["Public"])
def public_archive_detail(request: Request, newsletter_id: int) -> HTMLResponse:
    nl = get_newsletter(newsletter_id)
    if not nl or nl["status"] != "sent":
        raise HTTPException(status_code=404, detail="Newsletter not found")
    return templates.TemplateResponse(
        "archive_detail.html",
        {"request": request, "newsletter": nl, "brand_name": BRAND_NAME},
    )


# ---------------------------------------------------------------------------
# Webhooks management
# ---------------------------------------------------------------------------

@app.post("/admin/webhooks/add", tags=["Admin"])
def admin_add_webhook(
    request: Request,
    url: str = Form(...),
    events: str = Form(default="all"),
) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)
    create_webhook(url, events)
    return RedirectResponse(url="/admin?tab=settings", status_code=303)


@app.post("/admin/webhooks/{webhook_id}/delete", tags=["Admin"])
def admin_delete_webhook(request: Request, webhook_id: int) -> RedirectResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)
    delete_webhook(webhook_id)
    return RedirectResponse(url="/admin?tab=settings", status_code=303)


# ---------------------------------------------------------------------------
# Embed form
# ---------------------------------------------------------------------------

@app.get("/embed", response_class=HTMLResponse, tags=["Public"])
def embed_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("embed.html", {"request": request, "brand_name": BRAND_NAME})
