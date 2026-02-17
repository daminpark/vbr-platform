"""Scheduled message template engine.

Runs as a background task, checking every 60 seconds for templates
that need to be sent based on reservation dates.

Triggers:
  - day_before_checkin: day before check-in
  - checkin_day: check-in day
  - day_after_checkin: day after check-in
  - day_before_checkout: day before check-out
  - checkout_day: check-out day

All templates start DISABLED. Pierre enables them when he turns off
Host Tools templates.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_session
from app.db.models import MessageTemplate, ScheduledMessageLog, Reservation, Listing
from app.services.hosttools import HostToolsClient

logger = logging.getLogger(__name__)

# Map trigger names to date offsets from check_in/check_out
TRIGGER_DATE_MAP = {
    "day_before_checkin": ("check_in", -1),
    "checkin_day": ("check_in", 0),
    "day_after_checkin": ("check_in", 1),
    "day_before_checkout": ("check_out", -1),
    "checkout_day": ("check_out", 0),
}


def _substitute_placeholders(body: str, reservation: Reservation, listing: Listing | None) -> str:
    """Replace placeholders in template body."""
    guest_first = reservation.guest_name.split()[0] if reservation.guest_name else "Guest"
    check_in_fmt = reservation.check_in.strftime("%d %b") if reservation.check_in else ""
    check_out_fmt = reservation.check_out.strftime("%d %b") if reservation.check_out else ""
    listing_name = listing.name if listing else ""

    return (
        body
        .replace("{guest_name}", guest_first)
        .replace("{check_in}", check_in_fmt)
        .replace("{check_out}", check_out_fmt)
        .replace("{listing_name}", listing_name)
        .replace("{num_guests}", str(reservation.num_guests or ""))
    )


async def check_and_send_templates(hosttools: HostToolsClient) -> int:
    """Check all enabled templates and send matching ones.

    Returns number of messages sent.
    """
    today = date.today()
    now = datetime.utcnow()
    sent_count = 0

    async with get_session() as session:
        # Load enabled templates
        result = await session.execute(
            select(MessageTemplate).where(MessageTemplate.enabled == True)
        )
        templates = result.scalars().all()

        if not templates:
            return 0

        for template in templates:
            trigger_info = TRIGGER_DATE_MAP.get(template.trigger)
            if not trigger_info:
                logger.warning("Unknown trigger: %s", template.trigger)
                continue

            date_field, offset = trigger_info
            target_date = today - timedelta(days=offset)  # reverse: find reservations where date == target

            # Find reservations that match this trigger date
            if date_field == "check_in":
                # We want reservations where check_in date == today + offset
                query = (
                    select(Reservation)
                    .options(selectinload(Reservation.listing))
                    .where(
                        Reservation.check_in >= datetime.combine(today + timedelta(days=offset), datetime.min.time()),
                        Reservation.check_in < datetime.combine(today + timedelta(days=offset + 1), datetime.min.time()),
                    )
                )
            else:  # check_out
                query = (
                    select(Reservation)
                    .options(selectinload(Reservation.listing))
                    .where(
                        Reservation.check_out >= datetime.combine(today + timedelta(days=offset), datetime.min.time()),
                        Reservation.check_out < datetime.combine(today + timedelta(days=offset + 1), datetime.min.time()),
                    )
                )

            res_result = await session.execute(query)
            reservations = res_result.scalars().unique().all()

            for reservation in reservations:
                # Check house_code filter
                if template.house_code:
                    listing_house = reservation.listing.house_code if reservation.listing else None
                    if listing_house and listing_house != template.house_code and listing_house != "both":
                        continue

                # Check hour — only send if we've passed the scheduled hour
                if now.hour < template.hours_offset:
                    continue

                # Check if already sent
                existing = await session.execute(
                    select(ScheduledMessageLog).where(
                        and_(
                            ScheduledMessageLog.template_id == template.id,
                            ScheduledMessageLog.reservation_id == reservation.id,
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                # Substitute placeholders
                body = _substitute_placeholders(
                    template.body, reservation, reservation.listing
                )

                # Send via Host Tools
                try:
                    await hosttools.send_message(reservation.hosttools_id, body)
                except Exception as e:
                    logger.error(
                        "Failed to send template '%s' for reservation %s: %s",
                        template.name, reservation.hosttools_id, e,
                    )
                    continue

                # Log it
                log_entry = ScheduledMessageLog(
                    template_id=template.id,
                    reservation_id=reservation.id,
                    body_sent=body,
                )
                session.add(log_entry)
                sent_count += 1

                logger.info(
                    "Sent template '%s' to %s (reservation %s)",
                    template.name, reservation.guest_name, reservation.hosttools_id,
                )

    return sent_count


async def template_scheduler_loop(hosttools: HostToolsClient, interval: int = 60):
    """Background loop that checks templates every `interval` seconds."""
    await asyncio.sleep(60)  # Wait 60s after startup
    while True:
        try:
            sent = await check_and_send_templates(hosttools)
            if sent > 0:
                logger.info("Template scheduler: sent %d messages", sent)
        except Exception as e:
            logger.error("Template scheduler error: %s", e)
        await asyncio.sleep(interval)
