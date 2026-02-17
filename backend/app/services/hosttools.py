"""Host Tools API client.

API docs: https://host-tools.readme.io/reference/intro
Base URL: https://app.hosttools.com/api/
Auth: Header `authToken: <TOKEN>`
"""

import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://app.hosttools.com/api"


class HostToolsClient:
    """Client for the Host Tools API."""

    def __init__(self, auth_token: str):
        self.auth_token = auth_token
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"authToken": auth_token},
            timeout=30.0,
        )

    async def close(self):
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """Make a GET request to the Host Tools API."""
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, data: dict | None = None) -> Any:
        """Make a POST request to the Host Tools API."""
        resp = await self._client.post(path, json=data)
        resp.raise_for_status()
        return resp.json()

    # ---- Listings ----

    async def get_listings(self) -> list[dict]:
        """Get all listings."""
        result = await self._get("/getlistings")
        # Host Tools returns { listings: [...] } or just a list
        if isinstance(result, dict) and "listings" in result:
            return result["listings"]
        if isinstance(result, list):
            return result
        return []

    # ---- Reservations ----

    async def get_reservations(
        self,
        listing_id: str,
        start: date | str,
        end: date | str,
    ) -> list[dict]:
        """Get reservations for a listing in a date range."""
        start_str = start.isoformat() if isinstance(start, date) else start
        end_str = end.isoformat() if isinstance(end, date) else end
        result = await self._get(f"/getreservations/{listing_id}/{start_str}/{end_str}")
        if isinstance(result, dict) and "reservations" in result:
            return result["reservations"]
        if isinstance(result, list):
            return result
        return []

    async def get_reservation(self, reservation_id: str) -> dict:
        """Get a single reservation by ID."""
        return await self._get(f"/getreservation/{reservation_id}")

    # ---- Messages ----

    async def send_message(self, reservation_id: str, message: str) -> dict:
        """Send a message to a guest.

        POST /api/sendmessage/{reservationid}
        Body: { "message": "text" }
        """
        return await self._post(
            f"/sendmessage/{reservation_id}",
            data={"message": message},
        )

    # ---- Reviews ----

    async def get_reviews(self, listing_id: str) -> list[dict]:
        """Get reviews for a listing."""
        result = await self._get(f"/getreviews/{listing_id}")
        if isinstance(result, dict) and "reviews" in result:
            return result["reviews"]
        if isinstance(result, list):
            return result
        return []

    # ---- Calendar ----

    async def get_calendar(
        self,
        listing_id: str,
        start: date | str,
        end: date | str,
    ) -> list[dict]:
        """Get calendar/availability for a listing."""
        start_str = start.isoformat() if isinstance(start, date) else start
        end_str = end.isoformat() if isinstance(end, date) else end
        result = await self._get(f"/getcalendar/{listing_id}/{start_str}/{end_str}")
        if isinstance(result, list):
            return result
        return []

    # ---- User / Account ----

    async def get_user(self) -> dict:
        """Get account info."""
        return await self._get("/getuser")

    # ---- Webhooks ----

    async def set_webhook(self, url: str, events: list[str] | None = None) -> dict:
        """Configure webhook URL for receiving events.

        Note: Webhooks are typically configured through the Host Tools
        message rules UI, not this endpoint. This is here for reference.
        """
        data: dict[str, Any] = {"url": url}
        if events:
            data["events"] = events
        return await self._post("/setwebhook", data=data)
