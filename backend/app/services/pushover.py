"""Pushover notification service.

Priority levels:
  -2 = no notification
  -1 = quiet
   0 = normal (default)
   1 = high priority (bypasses quiet hours)
   2 = emergency (repeats every 30s until acknowledged)

Docs: https://pushover.net/api
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

# Emergency keywords that trigger priority 2
EMERGENCY_KEYWORDS = [
    "emergency", "fire", "flood", "locked out", "lockout",
    "lock out", "can't get in", "cant get in", "stuck outside",
    "help me", "urgent", "police", "ambulance",
]


class PushoverClient:
    """Send notifications via Pushover."""

    def __init__(self, app_token: str, user_key: str):
        self.app_token = app_token
        self.user_key = user_key
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        await self._client.aclose()

    async def send(
        self,
        message: str,
        title: Optional[str] = None,
        priority: int = 0,
        url: Optional[str] = None,
        url_title: Optional[str] = None,
        sound: Optional[str] = None,
    ) -> bool:
        """Send a Pushover notification.

        Returns True if sent successfully.
        """
        if not self.app_token or not self.user_key:
            logger.warning("Pushover not configured, skipping notification")
            return False

        data = {
            "token": self.app_token,
            "user": self.user_key,
            "message": message,
        }
        if title:
            data["title"] = title
        if url:
            data["url"] = url
        if url_title:
            data["url_title"] = url_title
        if sound:
            data["sound"] = sound

        data["priority"] = str(priority)

        # Emergency priority requires retry and expire params
        if priority == 2:
            data["retry"] = "30"  # retry every 30 seconds
            data["expire"] = "3600"  # stop after 1 hour

        try:
            resp = await self._client.post(PUSHOVER_API_URL, data=data)
            if resp.status_code == 200:
                logger.info("Pushover notification sent: %s", title or message[:50])
                return True
            else:
                logger.error("Pushover error %d: %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("Pushover send failed: %s", e)
            return False

    async def notify_new_message(self, guest_name: str, message_preview: str, reservation_url: str = ""):
        """Normal priority notification for a new guest message."""
        await self.send(
            title=f"Message from {guest_name}",
            message=message_preview[:200],
            url=reservation_url,
            url_title="Open conversation",
            priority=0,
        )

    async def notify_escalation(self, guest_name: str, message_preview: str, reservation_url: str = ""):
        """High priority notification — AI couldn't answer, Pierre needs to respond."""
        await self.send(
            title=f"AI needs help: {guest_name}",
            message=message_preview[:200],
            url=reservation_url,
            url_title="Open conversation",
            priority=1,
            sound="siren",
        )

    async def notify_emergency(self, guest_name: str, message_text: str, reservation_url: str = ""):
        """Emergency priority — repeats every 30s until acknowledged."""
        await self.send(
            title=f"EMERGENCY: {guest_name}",
            message=message_text[:200],
            url=reservation_url,
            url_title="Open conversation",
            priority=2,
        )

    async def notify_issue_report(self, note: str):
        """Cleaner reported an issue (damage/breakage)."""
        await self.send(
            title="Cleaner Issue Report",
            message=note[:200] if note else "Photo attached — check app",
            priority=1,
        )

    async def notify_running_low(self, item_name: str, location: str = ""):
        """Cleaner flagged an item as running low."""
        msg = f"{item_name}"
        if location:
            msg += f" ({location})"
        await self.send(
            title="Running Low",
            message=msg,
            priority=0,
        )


def is_emergency_message(text: str) -> bool:
    """Check if a guest message contains emergency keywords."""
    lower = text.lower()
    return any(kw in lower for kw in EMERGENCY_KEYWORDS)
