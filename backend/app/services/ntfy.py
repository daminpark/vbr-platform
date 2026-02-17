"""ntfy.sh notification service (self-hosted).

Priority levels:
  1 = min (no sound/vibration)
  2 = low
  3 = default
  4 = high (bypasses DND on most devices)
  5 = max (persistent, bypasses DND, stays until dismissed)

Docs: https://docs.ntfy.sh/publish/
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Emergency keywords that trigger priority 5 (max)
EMERGENCY_KEYWORDS = [
    "emergency", "fire", "flood", "locked out", "lockout",
    "lock out", "can't get in", "cant get in", "stuck outside",
    "help me", "urgent", "police", "ambulance",
]


class NtfyClient:
    """Send notifications via ntfy.sh (self-hosted or public)."""

    def __init__(self, url: str, topic: str, token: str = ""):
        self.url = url.rstrip("/") if url else ""
        self.topic = topic
        self.token = token
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.topic)

    async def close(self):
        await self._client.aclose()

    async def send(
        self,
        message: str,
        title: Optional[str] = None,
        priority: int = 3,
        tags: Optional[list[str]] = None,
        click_url: Optional[str] = None,
        actions: Optional[list[dict]] = None,
    ) -> bool:
        """Send an ntfy notification.

        Returns True if sent successfully.
        """
        if not self.configured:
            logger.warning("ntfy not configured, skipping notification")
            return False

        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # ntfy uses plain-text posting with headers for metadata
        headers["Title"] = title or "VBR"
        headers["Priority"] = str(priority)

        if tags:
            headers["Tags"] = ",".join(tags)

        if click_url:
            headers["Click"] = click_url

        if actions:
            # ntfy action buttons: https://docs.ntfy.sh/publish/#action-buttons
            action_strs = []
            for a in actions:
                action_strs.append(
                    f"{a.get('action', 'view')}, {a.get('label', 'Open')}, {a.get('url', '')}"
                )
            headers["Actions"] = "; ".join(action_strs)

        try:
            resp = await self._client.post(
                f"{self.url}/{self.topic}",
                content=message.encode("utf-8"),
                headers=headers,
            )
            if resp.status_code == 200:
                logger.info("ntfy notification sent: %s", title or message[:50])
                return True
            else:
                logger.error("ntfy error %d: %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("ntfy send failed: %s", e)
            return False

    # ---- Convenience methods ----

    async def notify_new_message(
        self, guest_name: str, message_preview: str, reservation_url: str = ""
    ):
        """Normal priority notification for a new guest message."""
        await self.send(
            title=f"💬 {guest_name}",
            message=message_preview[:200],
            priority=3,
            tags=["speech_balloon"],
            click_url=reservation_url or None,
        )

    async def notify_escalation(
        self, guest_name: str, message_preview: str, reservation_url: str = ""
    ):
        """High priority — AI couldn't answer, Pierre needs to respond."""
        await self.send(
            title=f"🤖 AI needs help: {guest_name}",
            message=message_preview[:200],
            priority=4,
            tags=["robot_face", "warning"],
            click_url=reservation_url or None,
        )

    async def notify_emergency(
        self, guest_name: str, message_text: str, reservation_url: str = ""
    ):
        """Max priority — persistent notification until dismissed."""
        await self.send(
            title=f"🚨 EMERGENCY: {guest_name}",
            message=message_text[:200],
            priority=5,
            tags=["rotating_light", "sos"],
            click_url=reservation_url or None,
        )

    async def notify_issue_report(self, note: str):
        """Cleaner reported an issue (damage/breakage)."""
        await self.send(
            title="🔧 Cleaner Issue Report",
            message=note[:200] if note else "Photo attached — check app",
            priority=4,
            tags=["wrench"],
        )

    async def notify_running_low(self, item_name: str, location: str = ""):
        """Cleaner flagged an item as running low."""
        msg = item_name
        if location:
            msg += f" ({location})"
        await self.send(
            title="📦 Running Low",
            message=msg,
            priority=3,
            tags=["package"],
        )

    async def notify_server_down(self):
        """Server health check failed — self-monitoring alert."""
        await self.send(
            title="🔴 VBR Server Down",
            message="The VBR platform is not responding. Host Tools app is your fallback.",
            priority=5,
            tags=["red_circle", "sos"],
        )


def is_emergency_message(text: str) -> bool:
    """Check if a guest message contains emergency keywords."""
    lower = text.lower()
    return any(kw in lower for kw in EMERGENCY_KEYWORDS)
