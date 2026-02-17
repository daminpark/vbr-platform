"""API routes for VBR Platform."""

import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response, Request
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.db.database import get_session
from app.db.models import Listing, Reservation, Message, KnowledgeEntry

logger = logging.getLogger(__name__)

router = APIRouter()

# Will be set from main.py on startup
_hosttools = None
_ntfy = None
_ai_drafter = None


def set_services(hosttools, ntfy, ai_drafter=None):
    global _hosttools, _ntfy, _ai_drafter
    _hosttools = hosttools
    _ntfy = ntfy
    _ai_drafter = ai_drafter


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    pin: str


@router.post("/auth/login")
async def login(req: LoginRequest, response: Response):
    """Authenticate with PIN and set session cookie."""
    from app.core.auth import create_session_cookie, COOKIE_NAME, COOKIE_MAX_AGE
    from app.core.config import settings

    if req.pin == settings.owner_pin:
        role = "owner"
    elif req.pin == settings.cleaner_pin:
        role = "cleaner"
    else:
        raise HTTPException(status_code=401, detail="Invalid PIN")

    cookie_value = create_session_cookie(role)
    response.set_cookie(
        COOKIE_NAME,
        cookie_value,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return {"role": role}


@router.get("/auth/check")
async def auth_check(request: Request):
    """Check if current session is valid."""
    from app.core.auth import verify_session_cookie, COOKIE_NAME

    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return {"authenticated": False}
    role = verify_session_cookie(cookie)
    return {"authenticated": bool(role), "role": role}


@router.post("/auth/logout")
async def logout(response: Response):
    """Clear session cookie."""
    from app.core.auth import COOKIE_NAME

    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

@router.get("/listings")
async def get_listings():
    """Get all synced listings."""
    async with get_session() as session:
        result = await session.execute(
            select(Listing).order_by(Listing.name)
        )
        listings = result.scalars().all()
        return [
            {
                "id": l.id,
                "hosttools_id": l.hosttools_id,
                "name": l.name,
                "platform": l.platform,
                "house_code": l.house_code,
                "picture_url": l.picture_url,
                "last_synced": l.last_synced.isoformat() if l.last_synced else None,
            }
            for l in listings
        ]


def _detect_house_code(name: str) -> str | None:
    """Detect house code from listing name. '3.x' or '193' = 193, '5.x' or '195' = 195."""
    if not name:
        return None
    prefix = name.split(" ")[0].split("·")[0].strip().upper()
    if prefix.startswith("3.") or prefix.startswith("193"):
        return "193"
    if prefix.startswith("5.") or prefix.startswith("195"):
        return "195"
    if "193195" in prefix or "ROCHESTER" in name.upper():
        return "both"
    return None


@router.post("/sync/listings")
async def sync_listings():
    """Pull listings from Host Tools and sync to DB."""
    if not _hosttools:
        raise HTTPException(status_code=503, detail="Host Tools not configured")

    raw_listings = await _hosttools.get_listings()
    synced = []

    async with get_session() as session:
        for raw in raw_listings:
            ht_id = str(raw.get("_id") or raw.get("id", ""))
            if not ht_id:
                continue

            result = await session.execute(
                select(Listing).where(Listing.hosttools_id == ht_id)
            )
            listing = result.scalar_one_or_none()

            name = raw.get("nickname") or raw.get("name") or raw.get("title", "Unknown")
            house_code = _detect_house_code(name)

            if listing:
                listing.name = name
                listing.platform = raw.get("source") or raw.get("platform")
                listing.house_code = house_code
                listing.picture_url = raw.get("picture") or raw.get("thumbnail")
                listing.raw_data = json.dumps(raw, default=str)
                listing.last_synced = datetime.utcnow()
            else:
                listing = Listing(
                    hosttools_id=ht_id,
                    name=name,
                    platform=raw.get("source") or raw.get("platform"),
                    house_code=house_code,
                    picture_url=raw.get("picture") or raw.get("thumbnail"),
                    raw_data=json.dumps(raw, default=str),
                    last_synced=datetime.utcnow(),
                )
                session.add(listing)

            synced.append(name)

    return {"synced": len(synced), "listings": synced}


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------

@router.get("/reservations")
async def get_reservations(
    listing_id: Optional[int] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    active_only: bool = True,
):
    """Get reservations, optionally filtered."""
    async with get_session() as session:
        query = select(Reservation).options(selectinload(Reservation.listing))

        if listing_id:
            query = query.where(Reservation.listing_id == listing_id)
        if active_only:
            today = date.today()
            query = query.where(Reservation.check_out >= datetime.combine(today, datetime.min.time()))
        if from_date:
            query = query.where(Reservation.check_out >= datetime.combine(from_date, datetime.min.time()))
        if to_date:
            query = query.where(Reservation.check_in <= datetime.combine(to_date, datetime.max.time()))

        query = query.order_by(Reservation.check_in.desc())
        result = await session.execute(query)
        reservations = result.scalars().all()

        return [
            {
                "id": r.id,
                "hosttools_id": r.hosttools_id,
                "listing_name": r.listing.name if r.listing else None,
                "house_code": r.listing.house_code if r.listing else None,
                "guest_name": r.guest_name,
                "guest_phone": r.guest_phone,
                "guest_picture_url": r.guest_picture_url,
                "check_in": r.check_in.isoformat(),
                "check_out": r.check_out.isoformat(),
                "num_guests": r.num_guests,
                "platform": r.platform,
                "status": r.status,
            }
            for r in reservations
        ]


def _parse_num_guests(raw_value) -> int:
    """Parse num_guests from Host Tools — can be int, dict, or None."""
    if raw_value is None:
        return 1
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, dict):
        # Host Tools sometimes returns {"children": 0, "infants": 0, "pets": 0}
        # The total adults is often in a separate field, default to 1
        adults = raw_value.get("adults", 1)
        children = raw_value.get("children", 0)
        return adults + children if isinstance(adults, int) else 1
    try:
        return int(raw_value)
    except (ValueError, TypeError):
        return 1


@router.post("/sync/reservations")
async def sync_reservations(full_history: bool = False):
    """Pull reservations from Host Tools for all listings and sync to DB.

    Also extracts any embedded messages from reservation data.
    Set full_history=true to pull all data back to 2024 (first run / backfill).
    """
    if not _hosttools:
        raise HTTPException(status_code=503, detail="Host Tools not configured")

    async with get_session() as session:
        result = await session.execute(select(Listing))
        listings = result.scalars().all()

        if not listings:
            return {"error": "No listings synced yet. Run /api/sync/listings first."}

        total_synced = 0
        total_messages = 0
        if full_history:
            start = "2025-10-01"
        else:
            start = (date.today() - timedelta(days=30)).isoformat()
        end = (date.today() + timedelta(days=365)).isoformat()

        for listing in listings:
            try:
                raw_reservations = await _hosttools.get_reservations(
                    listing.hosttools_id, start, end
                )
            except Exception as e:
                logger.error("Failed to fetch reservations for %s: %s", listing.name, e)
                continue

            for raw in raw_reservations:
                ht_id = str(raw.get("_id") or raw.get("id", ""))
                if not ht_id:
                    continue

                res_result = await session.execute(
                    select(Reservation).where(Reservation.hosttools_id == ht_id)
                )
                reservation = res_result.scalar_one_or_none()

                check_in = raw.get("checkinDateLocalized") or raw.get("checkin") or raw.get("startDate")
                check_out = raw.get("checkoutDateLocalized") or raw.get("checkout") or raw.get("endDate")

                if not check_in or not check_out:
                    continue

                # Parse dates
                if isinstance(check_in, str):
                    check_in = datetime.fromisoformat(check_in.replace("Z", "+00:00"))
                if isinstance(check_out, str):
                    check_out = datetime.fromisoformat(check_out.replace("Z", "+00:00"))

                # Parse guest info — Host Tools uses firstName/lastName at top level
                first = raw.get("firstName", "")
                last = raw.get("lastName", "")
                guest_name = f"{first} {last}".strip() or raw.get("guestName") or "Unknown"
                guest_phone = raw.get("phone") or raw.get("guestPhone")
                guest_email = raw.get("email") or raw.get("guestEmail")
                guest_pic = raw.get("guestPicture") or raw.get("guestPictureUrl")

                # Parse num_guests safely
                num_guests = _parse_num_guests(
                    raw.get("numberOfGuests") or raw.get("guests") or raw.get("guestCount")
                )

                platform = raw.get("source") or raw.get("platform") or raw.get("channelName") or "unknown"
                status = raw.get("status") or "confirmed"

                if reservation:
                    reservation.guest_name = guest_name
                    reservation.guest_phone = guest_phone
                    reservation.guest_email = guest_email
                    reservation.guest_picture_url = guest_pic
                    reservation.check_in = check_in
                    reservation.check_out = check_out
                    reservation.num_guests = num_guests
                    reservation.platform = platform
                    reservation.status = status
                    reservation.raw_data = json.dumps(raw, default=str)
                    reservation.last_synced = datetime.utcnow()
                else:
                    reservation = Reservation(
                        hosttools_id=ht_id,
                        listing_id=listing.id,
                        guest_name=guest_name,
                        guest_phone=guest_phone,
                        guest_email=guest_email,
                        guest_picture_url=guest_pic,
                        check_in=check_in,
                        check_out=check_out,
                        num_guests=num_guests,
                        platform=platform,
                        status=status,
                        raw_data=json.dumps(raw, default=str),
                        last_synced=datetime.utcnow(),
                    )
                    session.add(reservation)

                # Flush to get reservation.id for message linking
                await session.flush()

                # Extract messages from 'posts' (Host Tools terminology)
                posts = raw.get("posts") or raw.get("messages") or raw.get("thread") or []
                if isinstance(posts, list):
                    for msg_raw in posts:
                        msg_body = msg_raw.get("message") or msg_raw.get("body") or msg_raw.get("text", "")
                        if not msg_body:
                            continue

                        # Determine sender — Host Tools uses isGuest boolean
                        is_guest = msg_raw.get("isGuest", False)
                        role = msg_raw.get("role", "")
                        sender = "guest" if is_guest or role == "guest" else "host"

                        # Parse message timestamp
                        msg_time = msg_raw.get("sentTimestamp") or msg_raw.get("createdAt") or msg_raw.get("timestamp")
                        if msg_time and isinstance(msg_time, str):
                            try:
                                msg_time = datetime.fromisoformat(msg_time.replace("Z", "+00:00"))
                            except ValueError:
                                msg_time = datetime.utcnow()
                        elif not msg_time:
                            msg_time = datetime.utcnow()

                        # Dedup by reservation + hosttools message ID or timestamp + sender
                        ht_msg_id = msg_raw.get("_id", "")
                        if ht_msg_id:
                            existing = await session.execute(
                                select(Message).where(
                                    and_(
                                        Message.reservation_id == reservation.id,
                                        Message.hosttools_id == ht_msg_id,
                                    )
                                )
                            )
                        else:
                            existing = await session.execute(
                                select(Message).where(
                                    and_(
                                        Message.reservation_id == reservation.id,
                                        Message.timestamp == msg_time,
                                        Message.sender == sender,
                                    )
                                )
                            )
                        if existing.scalar_one_or_none():
                            continue

                        message = Message(
                            reservation_id=reservation.id,
                            hosttools_id=ht_msg_id or None,
                            timestamp=msg_time,
                            sender=sender,
                            body=msg_body,
                            is_sent=True,
                        )
                        session.add(message)
                        total_messages += 1

                total_synced += 1

    # Run template detection on all host messages
    from app.services.template_detector import detect_and_tag_templates

    templates_tagged = 0
    async with get_session() as tag_session:
        templates_tagged = await detect_and_tag_templates(tag_session)

    return {
        "synced": total_synced,
        "messages_imported": total_messages,
        "templates_tagged": templates_tagged,
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats():
    """Get data stats for training overview."""
    async with get_session() as session:
        listings = (await session.execute(select(func.count(Listing.id)))).scalar()
        reservations = (await session.execute(select(func.count(Reservation.id)))).scalar()
        total_msgs = (await session.execute(select(func.count(Message.id)))).scalar()
        guest_msgs = (await session.execute(
            select(func.count(Message.id)).where(Message.sender == "guest")
        )).scalar()
        host_msgs = (await session.execute(
            select(func.count(Message.id)).where(Message.sender == "host")
        )).scalar()
        templates = (await session.execute(
            select(func.count(Message.id)).where(Message.is_template == True)
        )).scalar()
        real_replies = (await session.execute(
            select(func.count(Message.id)).where(
                and_(Message.sender == "host", Message.is_template == False)
            )
        )).scalar()

        return {
            "listings": listings,
            "reservations": reservations,
            "total_messages": total_msgs,
            "guest_messages": guest_msgs,
            "host_messages": host_msgs,
            "template_messages": templates,
            "real_host_replies": real_replies,
            "training_data_size": real_replies,
        }


# ---------------------------------------------------------------------------
# Conversations / Messages
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def get_conversations(include_empty: bool = False):
    """Get conversations ordered by most recent message.

    By default only returns reservations that have messages.
    Set include_empty=true to also show reservations without messages.
    """
    async with get_session() as session:
        today = date.today()
        query = (
            select(Reservation)
            .options(selectinload(Reservation.listing), selectinload(Reservation.messages))
            .where(Reservation.check_out >= datetime.combine(today - timedelta(days=7), datetime.min.time()))
            .order_by(Reservation.check_in.desc())
        )
        result = await session.execute(query)
        reservations = result.scalars().unique().all()

        conversations = []
        for r in reservations:
            messages = sorted(r.messages, key=lambda m: m.timestamp, reverse=True)
            last_msg = messages[0] if messages else None

            # Skip reservations without messages unless requested
            if not include_empty and not messages:
                continue

            # "needs attention" = last message is from guest and no host/AI reply after it
            needs_attention = False
            if last_msg and last_msg.sender == "guest":
                needs_attention = True

            # Count unreviewed AI drafts
            pending_drafts = sum(1 for m in messages if m.is_draft and not m.is_sent)

            conversations.append({
                "reservation_id": r.id,
                "hosttools_id": r.hosttools_id,
                "guest_name": r.guest_name,
                "guest_picture_url": r.guest_picture_url,
                "listing_name": r.listing.name if r.listing else None,
                "house_code": r.listing.house_code if r.listing else None,
                "platform": r.platform,
                "check_in": r.check_in.isoformat(),
                "check_out": r.check_out.isoformat(),
                "num_guests": r.num_guests,
                "last_message_time": last_msg.timestamp.isoformat() if last_msg else None,
                "last_message_preview": last_msg.body[:100] if last_msg else None,
                "last_message_sender": last_msg.sender if last_msg else None,
                "needs_attention": needs_attention,
                "pending_drafts": pending_drafts,
                "message_count": len(messages),
            })

        # Sort: needs_attention first, then by most recent message
        conversations.sort(
            key=lambda c: (
                not c["needs_attention"],
                -(datetime.fromisoformat(c["last_message_time"]).timestamp() if c["last_message_time"] else 0),
            )
        )

        return conversations


@router.get("/conversations/{reservation_id}/messages")
async def get_messages(reservation_id: int):
    """Get all messages for a reservation (conversation thread)."""
    async with get_session() as session:
        # Get reservation with listing info
        res_result = await session.execute(
            select(Reservation)
            .options(selectinload(Reservation.listing))
            .where(Reservation.id == reservation_id)
        )
        reservation = res_result.scalar_one_or_none()
        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")

        # Get messages ordered by time
        msg_result = await session.execute(
            select(Message)
            .where(Message.reservation_id == reservation_id)
            .order_by(Message.timestamp.asc())
        )
        messages = msg_result.scalars().all()

        return {
            "reservation": {
                "id": reservation.id,
                "guest_name": reservation.guest_name,
                "guest_picture_url": reservation.guest_picture_url,
                "listing_name": reservation.listing.name if reservation.listing else None,
                "house_code": reservation.listing.house_code if reservation.listing else None,
                "platform": reservation.platform,
                "check_in": reservation.check_in.isoformat(),
                "check_out": reservation.check_out.isoformat(),
                "num_guests": reservation.num_guests,
            },
            "messages": [
                {
                    "id": m.id,
                    "timestamp": m.timestamp.isoformat(),
                    "sender": m.sender,
                    "body": m.body,
                    "body_original": m.body_original,
                    "detected_language": m.detected_language,
                    "translated": m.translated,
                    "is_draft": m.is_draft,
                    "is_sent": m.is_sent,
                    "ai_generated": m.ai_generated,
                    "ai_confidence": m.ai_confidence,
                    "ai_auto_sent": m.ai_auto_sent,
                    "was_edited": m.was_edited,
                    "is_template": m.is_template,
                }
                for m in messages
            ],
        }


class SendMessageRequest(BaseModel):
    body: str
    was_edited: bool = False
    original_ai_draft: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_category: Optional[str] = None


@router.post("/conversations/{reservation_id}/send")
async def send_message(reservation_id: int, req: SendMessageRequest):
    """Send a message to a guest via Host Tools."""
    if not _hosttools:
        raise HTTPException(status_code=503, detail="Host Tools not configured")

    async with get_session() as session:
        res_result = await session.execute(
            select(Reservation).where(Reservation.id == reservation_id)
        )
        reservation = res_result.scalar_one_or_none()
        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")

        # Send via Host Tools
        try:
            await _hosttools.send_message(reservation.hosttools_id, req.body)
        except Exception as e:
            logger.error("Failed to send message: %s", e)
            raise HTTPException(status_code=502, detail=f"Host Tools error: {e}")

        # Store in our DB
        message = Message(
            reservation_id=reservation_id,
            timestamp=datetime.utcnow(),
            sender="host",
            body=req.body,
            is_sent=True,
            ai_generated=req.original_ai_draft is not None,
            ai_confidence=req.ai_confidence,
            was_edited=req.was_edited,
            original_ai_draft=req.original_ai_draft,
        )
        session.add(message)

    return {"sent": True, "message_id": message.id}


# ---------------------------------------------------------------------------
# AI Drafts
# ---------------------------------------------------------------------------

@router.post("/conversations/{reservation_id}/draft")
async def generate_draft(reservation_id: int):
    """Generate an AI draft reply for a conversation."""
    if not _ai_drafter:
        raise HTTPException(status_code=503, detail="AI not configured (GEMINI_API_KEY not set)")

    async with get_session() as session:
        try:
            result = await _ai_drafter.generate_draft(session, reservation_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("AI draft generation failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="AI generation failed")

    return result


# ---------------------------------------------------------------------------
# Knowledge Base
# ---------------------------------------------------------------------------

class KnowledgeEntryRequest(BaseModel):
    category: str
    question: Optional[str] = None
    answer: str


@router.get("/knowledge")
async def get_knowledge(category: Optional[str] = None):
    """Get knowledge base entries, optionally filtered by category."""
    async with get_session() as session:
        query = select(KnowledgeEntry).where(KnowledgeEntry.active == True)
        if category:
            query = query.where(KnowledgeEntry.category == category)
        query = query.order_by(KnowledgeEntry.category, KnowledgeEntry.id)
        result = await session.execute(query)
        entries = result.scalars().all()
        return [
            {
                "id": e.id,
                "category": e.category,
                "question": e.question,
                "answer": e.answer,
                "source": e.source,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ]


@router.post("/knowledge")
async def create_knowledge(req: KnowledgeEntryRequest):
    """Create a new knowledge base entry."""
    async with get_session() as session:
        entry = KnowledgeEntry(
            category=req.category,
            question=req.question,
            answer=req.answer,
            source="manual",
            active=True,
        )
        session.add(entry)
        await session.flush()
        return {"id": entry.id, "created": True}


@router.put("/knowledge/{entry_id}")
async def update_knowledge(entry_id: int, req: KnowledgeEntryRequest):
    """Update a knowledge base entry."""
    async with get_session() as session:
        result = await session.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        entry.category = req.category
        entry.question = req.question
        entry.answer = req.answer
        return {"id": entry.id, "updated": True}


@router.delete("/knowledge/{entry_id}")
async def delete_knowledge(entry_id: int):
    """Soft-delete a knowledge base entry."""
    async with get_session() as session:
        result = await session.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        entry.active = False
        return {"id": entry_id, "deleted": True}


class ImportKnowledgeRequest(BaseModel):
    json_data: dict
    replace: bool = True


@router.post("/knowledge/import")
async def import_knowledge(req: ImportKnowledgeRequest):
    """Bulk import knowledge from 195vbr en.json data."""
    from app.services.knowledge_importer import import_from_en_json

    async with get_session() as session:
        count = await import_from_en_json(session, req.json_data, req.replace)
    return {"imported": count}


# ---------------------------------------------------------------------------
# Webhooks from Host Tools
# ---------------------------------------------------------------------------

class WebhookMessagePayload(BaseModel):
    """Payload from Host Tools webhook for new messages."""
    reservationId: Optional[str] = None
    message: Optional[str] = None
    guestName: Optional[str] = None
    # Accept any extra fields
    class Config:
        extra = "allow"


@router.post("/webhooks/hosttools/message")
async def webhook_message(payload: WebhookMessagePayload):
    """Receive new message webhook from Host Tools."""
    logger.info("Webhook received: message from %s", payload.guestName or "unknown")

    if not payload.reservationId or not payload.message:
        return {"ok": True, "skipped": "missing data"}

    async with get_session() as session:
        # Find reservation
        res_result = await session.execute(
            select(Reservation).where(Reservation.hosttools_id == payload.reservationId)
        )
        reservation = res_result.scalar_one_or_none()

        if not reservation:
            logger.warning("Webhook: unknown reservation %s", payload.reservationId)
            return {"ok": True, "skipped": "unknown reservation"}

        # Store message
        message = Message(
            reservation_id=reservation.id,
            timestamp=datetime.utcnow(),
            sender="guest",
            body=payload.message,
            is_sent=True,
            needs_review=True,
        )
        session.add(message)

    # Send ntfy notification
    if _ntfy:
        from app.services.ntfy import is_emergency_message

        if is_emergency_message(payload.message):
            await _ntfy.notify_emergency(
                guest_name=payload.guestName or reservation.guest_name,
                message_text=payload.message,
            )
        else:
            await _ntfy.notify_new_message(
                guest_name=payload.guestName or reservation.guest_name,
                message_preview=payload.message,
            )

    return {"ok": True, "stored": True}


class WebhookReservationPayload(BaseModel):
    """Payload from Host Tools webhook for reservation events."""
    reservationId: Optional[str] = None
    event: Optional[str] = None  # new, modified, cancelled, etc.
    class Config:
        extra = "allow"


@router.post("/webhooks/hosttools/reservation")
async def webhook_reservation(payload: WebhookReservationPayload):
    """Receive reservation event webhook from Host Tools."""
    logger.info("Webhook received: reservation event %s for %s", payload.event, payload.reservationId)
    # For now just log it — full sync will pick up details
    return {"ok": True}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "hosttools_configured": bool(_hosttools and _hosttools.auth_token),
        "ntfy_configured": bool(_ntfy and _ntfy.configured),
        "ai_configured": bool(_ai_drafter),
    }
