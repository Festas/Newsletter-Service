"""Fire-and-forget webhook notifications for key events."""

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from app.database import list_webhooks

logger = logging.getLogger(__name__)


async def fire_webhook(event: str, payload: dict[str, Any]) -> None:
    """Send POST to all registered webhooks that match the event."""
    webhooks = list_webhooks()
    for wh in webhooks:
        events = wh.get("events", "all")
        if events != "all" and event not in events.split(","):
            continue
        asyncio.ensure_future(_post_webhook(wh["url"], event, payload))


async def _post_webhook(url: str, event: str, payload: dict[str, Any]) -> None:
    """Attempt a POST request to webhook URL using urllib (no extra deps)."""
    data = json.dumps({"event": event, "data": payload}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))  # noqa: S310
        logger.info("Webhook fired: %s -> %s", event, url)
    except urllib.error.URLError as exc:
        logger.warning("Webhook failed: %s -> %s: %s", event, url, exc)
    except Exception as exc:
        logger.warning("Webhook error: %s -> %s: %s", event, url, exc)
