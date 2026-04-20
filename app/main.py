import os
import re
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from app.auth import require_admin, verify_admin_credentials
from app.database import (
    confirm_by_token,
    create_or_update_subscriber,
    init_db,
    list_confirmed_subscribers,
    list_subscribers,
    unsubscribe_by_token,
)
from app.email_service import EmailService

load_dotenv()

app = FastAPI(title="Newsletter Service")

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
_rate_bucket: dict[str, list[float]] = {}


class SendPayload(BaseModel):
    subject: str
    body_text: str | None = None
    body_html: str | None = None


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


async def _send_newsletter_to_confirmed(
    request: Request,
    subject: str,
    body_text: str | None,
    body_html: str | None,
) -> int:
    if not body_text and not body_html:
        raise HTTPException(status_code=400, detail="body_text or body_html is required")

    subscribers = list_confirmed_subscribers()
    base_url = _get_base_url(request)

    sent = 0
    for subscriber in subscribers:
        unsubscribe_url = f"{base_url}/unsubscribe?token={subscriber['token']}"
        await email_service.send_newsletter(
            recipient=subscriber["email"],
            subject=subject,
            text_body=body_text,
            html_body=body_html,
            unsubscribe_url=unsubscribe_url,
        )
        sent += 1

    return sent


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/subscribe")
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

    return JSONResponse(
        {"message": "Subscription created. Please confirm via email."},
        status_code=status.HTTP_201_CREATED,
    )


@app.get("/confirm", response_class=HTMLResponse)
def confirm(token: str) -> HTMLResponse:
    if confirm_by_token(token):
        return HTMLResponse("<h1>Subscription confirmed.</h1>")
    return HTMLResponse("<h1>Invalid or expired token.</h1>", status_code=400)


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str) -> HTMLResponse:
    if unsubscribe_by_token(token):
        return HTMLResponse("<h1>You have been unsubscribed.</h1>")
    return HTMLResponse("<h1>Invalid unsubscribe token.</h1>", status_code=400)


@app.post("/send")
async def send_newsletter(
    payload: SendPayload,
    request: Request,
    _: str = Depends(require_admin),
) -> JSONResponse:
    sent = await _send_newsletter_to_confirmed(
        request,
        subject=payload.subject,
        body_text=payload.body_text,
        body_html=payload.body_html,
    )
    return JSONResponse({"sent": sent})


@app.get("/subscribers")
def subscribers(_: str = Depends(require_admin)) -> dict[str, object]:
    all_subscribers = list_subscribers()
    confirmed_count = sum(1 for subscriber in all_subscribers if subscriber["confirmed"])
    return {"count": len(all_subscribers), "confirmed": confirmed_count, "items": all_subscribers}


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request) -> HTMLResponse:
    if not _is_admin_logged_in(request):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})

    all_subscribers = list_subscribers()
    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "subscribers": all_subscribers,
            "count": len(all_subscribers),
            "message": None,
            "error": None,
        },
    )


@app.post("/admin/login", response_class=HTMLResponse)
def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse:
    if not verify_admin_credentials(username, password):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=401,
        )

    request.session["admin_logged_in"] = True
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/logout")
def admin_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/send", response_class=HTMLResponse)
async def admin_send_newsletter(
    request: Request,
    subject: str = Form(...),
    body_text: str = Form(default=""),
    body_html: str = Form(default=""),
) -> HTMLResponse:
    if not _is_admin_logged_in(request):
        return RedirectResponse(url="/admin", status_code=303)

    all_subscribers = list_subscribers()

    try:
        sent = await _send_newsletter_to_confirmed(
            request,
            subject=subject,
            body_text=body_text or None,
            body_html=body_html or None,
        )
        message = f"Newsletter sent to {sent} subscribers."
        error = None
    except HTTPException as exc:
        message = None
        error = str(exc.detail)

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "subscribers": all_subscribers,
            "count": len(all_subscribers),
            "message": message,
            "error": error,
        },
    )


@app.get("/embed", response_class=HTMLResponse)
def embed_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("embed.html", {"request": request})
