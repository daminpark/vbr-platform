"""API routes for VBR Platform."""

import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response, Request
from pydantic import BaseModel
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload

from app.db.database import get_session
from app.db.models import (
    Listing, Reservation, Message, KnowledgeEntry, MessageTemplate,
    InventoryLocation, InventoryItem, StockReport,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Will be set from main.py on startup
_hosttools = None
_ntfy = None
_ai_drafter = None
_inventory_ai = None


def set_services(hosttools, ntfy, ai_drafter=None, inventory_ai=None):
    global _hosttools, _ntfy, _ai_drafter, _inventory_ai
    _hosttools = hosttools
    _ntfy = ntfy
    _ai_drafter = ai_drafter
    _inventory_ai = inventory_ai


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    pin: str


@router.post("/auth/login")
async def login(req: LoginRequest, request: Request, response: Response):
    """Authenticate with PIN and set session cookie."""
    from app.core.auth import (
        create_session_cookie, COOKIE_NAME, COOKIE_MAX_AGE,
        check_rate_limit, record_failed_attempt, clear_attempts,
    )
    from app.core.config import settings

    ip = request.headers.get("cf-connecting-ip") or request.client.host
    wait = check_rate_limit(ip)
    if wait is not None:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {wait // 60 + 1} minutes.",
        )

    if req.pin == settings.owner_pin:
        role = "owner"
    elif req.pin == settings.cleaner_pin:
        role = "cleaner"
    else:
        record_failed_attempt(ip)
        raise HTTPException(status_code=401, detail="Invalid PIN")

    clear_attempts(ip)
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

            # Guest status: current / future / past
            check_in_date = r.check_in.date() if isinstance(r.check_in, datetime) else r.check_in
            check_out_date = r.check_out.date() if isinstance(r.check_out, datetime) else r.check_out
            if check_in_date <= today <= check_out_date:
                guest_status = "current"
                status_detail = None
            elif check_in_date > today:
                guest_status = "future"
                days_until = (check_in_date - today).days
                status_detail = f"{days_until}d" if days_until > 0 else "today"
            else:
                guest_status = "past"
                days_since = (today - check_out_date).days
                status_detail = f"{days_since}d ago" if days_since > 0 else "today"

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
                "guest_status": guest_status,
                "status_detail": status_detail,
                "last_message_time": last_msg.timestamp.isoformat() if last_msg else None,
                "last_message_preview": last_msg.body[:100] if last_msg else None,
                "last_message_sender": last_msg.sender if last_msg else None,
                "needs_attention": needs_attention,
                "pending_drafts": pending_drafts,
                "message_count": len(messages),
            })

        # Sort: needs_attention first, then by guest status (current > future > past),
        # then by most recent message
        status_order = {"current": 0, "future": 1, "past": 2}
        conversations.sort(
            key=lambda c: (
                not c["needs_attention"],
                status_order.get(c["guest_status"], 3),
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
            feedback_note=req.ai_category,  # used by learning module
        )
        session.add(message)
        await session.flush()

        # Auto-learn from this reply
        try:
            from app.services.learning import record_reply_outcome
            await record_reply_outcome(session, message, reservation)
        except Exception as e:
            logger.error("Learning failed (non-fatal): %s", e)

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
# Scheduled Message Templates
# ---------------------------------------------------------------------------

class TemplateRequest(BaseModel):
    name: str
    trigger: str
    body: str
    hours_offset: int = 14
    enabled: bool = False
    house_code: Optional[str] = None


@router.get("/templates")
async def list_templates():
    """List all message templates."""
    async with get_session() as session:
        result = await session.execute(
            select(MessageTemplate).order_by(MessageTemplate.trigger)
        )
        templates = result.scalars().all()
        return [
            {
                "id": t.id,
                "name": t.name,
                "trigger": t.trigger,
                "body": t.body,
                "hours_offset": t.hours_offset,
                "enabled": t.enabled,
                "house_code": t.house_code,
            }
            for t in templates
        ]


@router.post("/templates")
async def create_template(req: TemplateRequest):
    """Create a new message template."""
    async with get_session() as session:
        template = MessageTemplate(
            name=req.name,
            trigger=req.trigger,
            body=req.body,
            hours_offset=req.hours_offset,
            enabled=req.enabled,
            house_code=req.house_code,
        )
        session.add(template)
        await session.flush()
        return {"id": template.id, "created": True}


@router.put("/templates/{template_id}")
async def update_template(template_id: int, req: TemplateRequest):
    """Update a message template."""
    async with get_session() as session:
        result = await session.execute(
            select(MessageTemplate).where(MessageTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        template.name = req.name
        template.trigger = req.trigger
        template.body = req.body
        template.hours_offset = req.hours_offset
        template.enabled = req.enabled
        template.house_code = req.house_code
        return {"id": template.id, "updated": True}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int):
    """Delete a message template."""
    async with get_session() as session:
        result = await session.execute(
            select(MessageTemplate).where(MessageTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        await session.delete(template)
        return {"id": template_id, "deleted": True}


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
        "inventory_ai_configured": bool(_inventory_ai),
    }


# ---------------------------------------------------------------------------
# Inventory — Locations
# ---------------------------------------------------------------------------

class LocationRequest(BaseModel):
    house_code: str
    name: str
    code: Optional[str] = None
    parent_id: Optional[int] = None
    description: Optional[str] = None
    guest_accessible: bool = False
    locked: bool = False
    outdoor: bool = False


def _serialize_location(loc: InventoryLocation, include_children: bool = False) -> dict:
    """Serialize a location to a dict."""
    d = {
        "id": loc.id,
        "house_code": loc.house_code,
        "name": loc.name,
        "code": loc.code,
        "parent_id": loc.parent_id,
        "description": loc.description,
        "guest_accessible": loc.guest_accessible,
        "locked": loc.locked,
        "outdoor": loc.outdoor,
        "sort_order": loc.sort_order,
        "item_count": len(loc.items) if loc.items else 0,
    }
    if include_children and loc.children:
        d["children"] = [_serialize_location(c) for c in loc.children]
    return d


def _serialize_item(item: InventoryItem) -> dict:
    """Serialize an inventory item to a dict."""
    loc = item.location
    unresolved = [r for r in (item.stock_reports or []) if not r.resolved]
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "location_id": item.location_id,
        "location_name": loc.name if loc else None,
        "location_code": loc.code if loc else None,
        "house_code": loc.house_code if loc else None,
        "quantity": item.quantity,
        "unit": item.unit,
        "min_quantity": item.min_quantity,
        "brand": item.brand,
        "purchase_url": item.purchase_url,
        "status": item.status,
        "notes": item.notes,
        "product_description": item.product_description,
        "usage_instructions": item.usage_instructions,
        "suitable_for": item.suitable_for,
        "has_alert": len(unresolved) > 0,
        "alert_count": len(unresolved),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


async def _get_locations_context(session) -> list[dict]:
    """Get all locations formatted for AI context injection."""
    result = await session.execute(
        select(InventoryLocation)
        .options(selectinload(InventoryLocation.parent))
        .where(InventoryLocation.active == True)
        .order_by(InventoryLocation.sort_order)
    )
    locations = result.scalars().all()
    return [
        {
            "id": loc.id,
            "code": loc.code,
            "house_code": loc.house_code,
            "name": loc.name,
            "parent_name": loc.parent.name if loc.parent else None,
            "description": loc.description,
            "guest_accessible": loc.guest_accessible,
            "locked": loc.locked,
            "outdoor": loc.outdoor,
        }
        for loc in locations
    ]


@router.get("/inventory/locations")
async def get_inventory_locations(house_code: Optional[str] = None):
    """Get all storage locations, optionally filtered by house."""
    async with get_session() as session:
        query = (
            select(InventoryLocation)
            .options(
                selectinload(InventoryLocation.children),
                selectinload(InventoryLocation.items),
            )
            .where(InventoryLocation.active == True)
        )
        if house_code:
            query = query.where(InventoryLocation.house_code == house_code)
        query = query.where(InventoryLocation.parent_id == None)  # top-level only
        query = query.order_by(InventoryLocation.sort_order, InventoryLocation.house_code, InventoryLocation.name)
        result = await session.execute(query)
        locations = result.scalars().unique().all()
        return [_serialize_location(loc, include_children=True) for loc in locations]


@router.post("/inventory/locations")
async def create_inventory_location(req: LocationRequest, request: Request):
    """Create a new storage location."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        loc = InventoryLocation(
            house_code=req.house_code,
            name=req.name,
            code=req.code,
            parent_id=req.parent_id,
            description=req.description,
            guest_accessible=req.guest_accessible,
            locked=req.locked,
            outdoor=req.outdoor,
        )
        session.add(loc)
        await session.flush()
        return {"id": loc.id, "created": True}


@router.put("/inventory/locations/{location_id}")
async def update_inventory_location(location_id: int, req: LocationRequest, request: Request):
    """Update a storage location."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        result = await session.execute(
            select(InventoryLocation).where(InventoryLocation.id == location_id)
        )
        loc = result.scalar_one_or_none()
        if not loc:
            raise HTTPException(status_code=404, detail="Location not found")
        loc.house_code = req.house_code
        loc.name = req.name
        loc.code = req.code
        loc.parent_id = req.parent_id
        loc.description = req.description
        loc.guest_accessible = req.guest_accessible
        loc.locked = req.locked
        loc.outdoor = req.outdoor
        return {"id": loc.id, "updated": True}


@router.delete("/inventory/locations/{location_id}")
async def delete_inventory_location(location_id: int, request: Request):
    """Soft-delete a storage location."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        result = await session.execute(
            select(InventoryLocation).where(InventoryLocation.id == location_id)
        )
        loc = result.scalar_one_or_none()
        if not loc:
            raise HTTPException(status_code=404, detail="Location not found")
        loc.active = False
        return {"id": location_id, "deleted": True}


# ---------------------------------------------------------------------------
# Inventory — Seed Locations
# ---------------------------------------------------------------------------

@router.post("/inventory/locations/seed")
async def seed_inventory_locations(request: Request):
    """Seed initial storage locations from STORAGE_LOCATIONS.md data."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    # Check if already seeded
    async with get_session() as session:
        count = (await session.execute(
            select(func.count(InventoryLocation.id))
        )).scalar()
        if count > 0:
            return {"seeded": False, "message": "Locations already exist", "count": count}

    SEED_LOCATIONS = [
        # Outside — 193
        {"code": "193.W", "house_code": "193", "name": "Kitchen Yard", "outdoor": True,
         "description": "Go outside from kitchen. Some rain cover to cupboard, none to shed."},
        {"code": "193.W.C", "house_code": "193", "name": "Cupboard (Paint/Chemicals)", "outdoor": True,
         "parent_code": "193.W",
         "description": "Electricity meter inside. Very dusty. Under cover. Good for paints/chemicals."},
        {"code": "193.W.S", "house_code": "193", "name": "Shed (Toolshed)", "outdoor": True, "locked": True,
         "parent_code": "193.W",
         "description": "Cramped, dark, exposed to rain getting to it. Main tool storage. Shelves on both sides."},
        # Outside — 195
        {"code": "195.W", "house_code": "195", "name": "Outside Storage Area", "outdoor": True,
         "description": "External storage with Keter boxes and cupboard."},
        {"code": "195.W.K1", "house_code": "195", "name": "Keter Box 1 (Sheets)", "outdoor": True,
         "parent_code": "195.W",
         "description": "Spare sheets/linen. Check for damp periodically."},
        {"code": "195.W.K2", "house_code": "195", "name": "Keter Box 2 (Door Hardware)", "outdoor": True,
         "parent_code": "195.W",
         "description": "Door hardware — needs sorting and audit. Currently a jumble."},
        {"code": "195.W.C", "house_code": "195", "name": "Cupboard (Large Appliances)", "outdoor": True,
         "parent_code": "195.W",
         "description": "Dehumidifier, pressure washer. Good for bulky seasonal items."},
        # Patio — 193
        {"code": "193.P", "house_code": "193", "name": "Patio (Ground Floor)", "outdoor": True,
         "description": "Accessible through a guest room. Awkward access if guests are in."},
        {"code": "193.P.K", "house_code": "193", "name": "Keter Box", "outdoor": True,
         "parent_code": "193.P",
         "description": "Overflow trade supplies. Plumbing bits, electrical parts."},
        # Kitchen — both houses
        {"code": "193.K", "house_code": "193", "name": "Kitchen", "guest_accessible": True,
         "description": "Under sink + cabinets. Limited space. Kitchen supplies only."},
        {"code": "195.K", "house_code": "195", "name": "Kitchen", "guest_accessible": True,
         "description": "Under sink + cabinets. Limited space. Kitchen supplies only."},
        # Dining Room — both houses
        {"code": "193.0", "house_code": "193", "name": "Dining Room", "guest_accessible": True,
         "description": "Whole wall of shelves. Guest-visible — must be tidy/boxed. Good for guest supplies, spare toiletries."},
        {"code": "195.0", "house_code": "195", "name": "Dining Room", "guest_accessible": True,
         "description": "Whole wall of shelves. Guest-visible — must be tidy/boxed. Good for guest supplies, spare toiletries."},
        # Utility / Laundry — both houses
        {"code": "193.Y", "house_code": "193", "name": "Utility / Laundry",
         "description": "Washing machine + dryer. Linen and laundry related stuff only."},
        {"code": "195.Y", "house_code": "195", "name": "Utility / Laundry",
         "description": "Washing machine + dryer. Linen and laundry related stuff only."},
        # Cleaning Storage — both houses
        {"code": "193.Z", "house_code": "193", "name": "Cleaning Storage",
         "description": "Primary cleaning supply location. Lower area: daily products. Step stool available."},
        {"code": "193.Z.U", "house_code": "193", "name": "Upper Shelves", "parent_code": "193.Z",
         "description": "Backup/bulk cleaning stock. Step stool hanging in room. Spare bottles, bulk packs."},
        {"code": "195.Z", "house_code": "195", "name": "Cleaning Storage",
         "description": "Primary cleaning supply location. Lower area: daily products. Step stool available."},
        {"code": "195.Z.U", "house_code": "195", "name": "Upper Shelves", "parent_code": "195.Z",
         "description": "Backup/bulk cleaning stock. Step stool hanging in room. Spare bottles, bulk packs."},
        # Luggage / Basement — both houses
        {"code": "193.V", "house_code": "193", "name": "Basement (Luggage Area)", "guest_accessible": True,
         "description": "Primary: guest luggage. Less accessible corner: carpet/underlay (renovation materials)."},
        {"code": "195.V", "house_code": "195", "name": "Basement (Luggage Area)", "guest_accessible": True,
         "description": "Primary: guest luggage. Less accessible part: tiles, grout, cement, filler (renovation materials)."},
        # Wardrobes — upper floors
        {"code": "195.U", "house_code": "195", "name": "First Floor Wardrobe", "locked": True,
         "description": "Personal stuff + two guest cots. Locked."},
        {"code": "193.U", "house_code": "193", "name": "Wardrobe",
         "description": "Some stuff for sale, two cots, spare TV. Largely empty — good overflow capacity."},
    ]

    async with get_session() as session:
        # First pass: create all locations without parents
        code_to_id = {}
        for i, loc_data in enumerate(SEED_LOCATIONS):
            loc = InventoryLocation(
                code=loc_data["code"],
                house_code=loc_data["house_code"],
                name=loc_data["name"],
                outdoor=loc_data.get("outdoor", False),
                locked=loc_data.get("locked", False),
                guest_accessible=loc_data.get("guest_accessible", False),
                description=loc_data.get("description"),
                sort_order=i,
            )
            session.add(loc)
            await session.flush()
            code_to_id[loc_data["code"]] = loc.id

        # Second pass: set parent_ids
        for loc_data in SEED_LOCATIONS:
            parent_code = loc_data.get("parent_code")
            if parent_code and parent_code in code_to_id:
                result = await session.execute(
                    select(InventoryLocation).where(InventoryLocation.code == loc_data["code"])
                )
                loc = result.scalar_one_or_none()
                if loc:
                    loc.parent_id = code_to_id[parent_code]

    return {"seeded": True, "count": len(SEED_LOCATIONS)}


# ---------------------------------------------------------------------------
# Inventory — Items
# ---------------------------------------------------------------------------

class ItemRequest(BaseModel):
    name: str
    category: str
    location_id: Optional[int] = None
    quantity: int = 1
    unit: Optional[str] = None
    min_quantity: int = 0
    brand: Optional[str] = None
    purchase_url: Optional[str] = None
    notes: Optional[str] = None
    status: str = "in_use"
    product_description: Optional[str] = None
    usage_instructions: Optional[str] = None
    suitable_for: Optional[str] = None


@router.get("/inventory/items")
async def get_inventory_items(
    house_code: Optional[str] = None,
    category: Optional[str] = None,
    location_id: Optional[int] = None,
    status: Optional[str] = None,
    low_stock: bool = False,
):
    """Get inventory items with optional filters."""
    async with get_session() as session:
        query = (
            select(InventoryItem)
            .options(
                selectinload(InventoryItem.location),
                selectinload(InventoryItem.stock_reports),
            )
            .where(InventoryItem.active == True)
        )
        if house_code:
            query = query.outerjoin(InventoryLocation).where(
                InventoryLocation.house_code == house_code
            )
        if category:
            query = query.where(InventoryItem.category == category)
        if location_id:
            query = query.where(InventoryItem.location_id == location_id)
        if status:
            query = query.where(InventoryItem.status == status)
        if low_stock:
            query = query.where(
                and_(
                    InventoryItem.min_quantity > 0,
                    InventoryItem.quantity <= InventoryItem.min_quantity,
                )
            )
        query = query.order_by(InventoryItem.name)
        result = await session.execute(query)
        items = result.scalars().unique().all()
        return [_serialize_item(item) for item in items]


@router.get("/inventory/items/{item_id}")
async def get_inventory_item(item_id: int):
    """Get a single inventory item."""
    async with get_session() as session:
        result = await session.execute(
            select(InventoryItem)
            .options(
                selectinload(InventoryItem.location),
                selectinload(InventoryItem.stock_reports),
            )
            .where(InventoryItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        return _serialize_item(item)


@router.post("/inventory/items")
async def create_inventory_item(req: ItemRequest, request: Request):
    """Create a new inventory item. Also generates AI search aliases."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        item = InventoryItem(
            name=req.name,
            category=req.category,
            location_id=req.location_id,
            quantity=req.quantity,
            unit=req.unit,
            min_quantity=req.min_quantity,
            brand=req.brand,
            purchase_url=req.purchase_url,
            notes=req.notes,
            status=req.status,
            product_description=req.product_description,
            usage_instructions=req.usage_instructions,
            suitable_for=req.suitable_for,
        )
        session.add(item)
        await session.flush()
        item_id = item.id

    # Generate search aliases in background (non-blocking)
    if _inventory_ai:
        try:
            aliases = await _inventory_ai.generate_search_aliases(req.name, req.category)
            if aliases:
                async with get_session() as session:
                    result = await session.execute(
                        select(InventoryItem).where(InventoryItem.id == item_id)
                    )
                    item = result.scalar_one_or_none()
                    if item:
                        item.search_aliases = ", ".join(aliases)
        except Exception as e:
            logger.error("Alias generation failed (non-fatal): %s", e)

    return {"id": item_id, "created": True}


@router.put("/inventory/items/{item_id}")
async def update_inventory_item(item_id: int, req: ItemRequest, request: Request):
    """Update an inventory item."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        result = await session.execute(
            select(InventoryItem).where(InventoryItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        old_name = item.name
        item.name = req.name
        item.category = req.category
        item.location_id = req.location_id
        item.quantity = req.quantity
        item.unit = req.unit
        item.min_quantity = req.min_quantity
        item.brand = req.brand
        item.purchase_url = req.purchase_url
        item.notes = req.notes
        item.status = req.status
        item.product_description = req.product_description
        item.usage_instructions = req.usage_instructions
        item.suitable_for = req.suitable_for

    # Regenerate aliases if name changed
    if _inventory_ai and req.name != old_name:
        try:
            aliases = await _inventory_ai.generate_search_aliases(req.name, req.category)
            if aliases:
                async with get_session() as session:
                    result = await session.execute(
                        select(InventoryItem).where(InventoryItem.id == item_id)
                    )
                    item = result.scalar_one_or_none()
                    if item:
                        item.search_aliases = ", ".join(aliases)
        except Exception as e:
            logger.error("Alias regeneration failed (non-fatal): %s", e)

    return {"id": item_id, "updated": True}


class MoveItemRequest(BaseModel):
    location_id: int


@router.put("/inventory/items/{item_id}/move")
async def move_inventory_item(item_id: int, req: MoveItemRequest, request: Request):
    """Move an item to a new location."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        result = await session.execute(
            select(InventoryItem).where(InventoryItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        item.location_id = req.location_id
        return {"id": item_id, "moved": True, "location_id": req.location_id}


@router.delete("/inventory/items/{item_id}")
async def delete_inventory_item(item_id: int, request: Request):
    """Soft-delete an inventory item."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        result = await session.execute(
            select(InventoryItem).where(InventoryItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        item.active = False
        return {"id": item_id, "deleted": True}


# ---------------------------------------------------------------------------
# Inventory — AI-powered Search
# ---------------------------------------------------------------------------

class InventorySearchRequest(BaseModel):
    query: str
    house_code: Optional[str] = None


@router.post("/inventory/search")
async def search_inventory(req: InventorySearchRequest):
    """Fuzzy search inventory items. Tier 1: DB LIKE search. Tier 2: AI fallback."""
    query_lower = req.query.lower().strip()
    if not query_lower:
        return []

    async with get_session() as session:
        # Tier 1: DB search against name and search_aliases
        filters = [InventoryItem.active == True]
        name_filter = or_(
            func.lower(InventoryItem.name).contains(query_lower),
            func.lower(InventoryItem.search_aliases).contains(query_lower),
        )

        query = (
            select(InventoryItem)
            .options(
                selectinload(InventoryItem.location),
                selectinload(InventoryItem.stock_reports),
            )
            .where(and_(*filters, name_filter))
        )
        if req.house_code:
            query = query.outerjoin(InventoryLocation).where(
                InventoryLocation.house_code == req.house_code
            )
        query = query.order_by(InventoryItem.name)
        result = await session.execute(query)
        items = result.scalars().unique().all()

        if items:
            return [_serialize_item(item) for item in items]

        # Tier 2: AI fallback if no DB matches
        if _inventory_ai:
            all_result = await session.execute(
                select(InventoryItem)
                .options(selectinload(InventoryItem.location))
                .where(InventoryItem.active == True)
            )
            all_items = all_result.scalars().unique().all()
            if not all_items:
                return []

            items_summary = [
                {
                    "id": i.id,
                    "name": i.name,
                    "category": i.category,
                    "location_name": i.location.name if i.location else "unknown",
                }
                for i in all_items
            ]
            matches = await _inventory_ai.fuzzy_search(req.query, items_summary)

            # Fetch full item data for matches
            matched_ids = [m.get("item_id") for m in matches if m.get("item_id")]
            if matched_ids:
                matched_result = await session.execute(
                    select(InventoryItem)
                    .options(
                        selectinload(InventoryItem.location),
                        selectinload(InventoryItem.stock_reports),
                    )
                    .where(InventoryItem.id.in_(matched_ids))
                )
                matched_items = {i.id: i for i in matched_result.scalars().unique().all()}
                return [
                    {**_serialize_item(matched_items[m["item_id"]]), "ai_match": True, "match_reason": m.get("reason", "")}
                    for m in matches
                    if m.get("item_id") in matched_items
                ]

        return []


# ---------------------------------------------------------------------------
# Inventory — AI Parse & Suggest
# ---------------------------------------------------------------------------

class NLInputRequest(BaseModel):
    text: str


@router.post("/inventory/ai/parse")
async def ai_parse_inventory_input(req: NLInputRequest, request: Request):
    """Parse natural language input into structured inventory items."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    if not _inventory_ai:
        raise HTTPException(status_code=503, detail="Inventory AI not configured")

    async with get_session() as session:
        locations = await _get_locations_context(session)

    result = await _inventory_ai.parse_natural_language_input(req.text, locations)
    return result


class BulkImportRequest(BaseModel):
    text: str


@router.post("/inventory/ai/bulk-import")
async def ai_bulk_import_preview(req: BulkImportRequest, request: Request):
    """Parse a text dump into structured items (preview — does not save)."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    if not _inventory_ai:
        raise HTTPException(status_code=503, detail="Inventory AI not configured")

    async with get_session() as session:
        locations = await _get_locations_context(session)

    result = await _inventory_ai.parse_bulk_import(req.text, locations)
    return result


class BulkImportConfirmItem(BaseModel):
    name: str
    category: str
    location_code: Optional[str] = None
    quantity: int = 1
    unit: Optional[str] = None


class BulkImportConfirmRequest(BaseModel):
    items: list[BulkImportConfirmItem]


@router.post("/inventory/ai/bulk-import/confirm")
async def ai_bulk_import_confirm(req: BulkImportConfirmRequest, request: Request):
    """Confirm and save bulk import items to database."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    created = 0
    async with get_session() as session:
        # Build code-to-id map
        loc_result = await session.execute(
            select(InventoryLocation).where(InventoryLocation.active == True)
        )
        code_to_id = {
            loc.code: loc.id for loc in loc_result.scalars().all() if loc.code
        }

        for item_data in req.items:
            location_id = code_to_id.get(item_data.location_code)
            item = InventoryItem(
                name=item_data.name,
                category=item_data.category,
                location_id=location_id,
                quantity=item_data.quantity,
                unit=item_data.unit,
            )
            session.add(item)
            created += 1
        await session.flush()

    # Generate search aliases for all new items (fire-and-forget style)
    if _inventory_ai:
        for item_data in req.items:
            try:
                aliases = await _inventory_ai.generate_search_aliases(item_data.name, item_data.category)
                if aliases:
                    async with get_session() as session:
                        result = await session.execute(
                            select(InventoryItem).where(
                                and_(
                                    InventoryItem.name == item_data.name,
                                    InventoryItem.active == True,
                                )
                            )
                        )
                        item = result.scalar_one_or_none()
                        if item and not item.search_aliases:
                            item.search_aliases = ", ".join(aliases)
            except Exception as e:
                logger.error("Alias generation failed for %s (non-fatal): %s", item_data.name, e)

    return {"created": created}


class SuggestLocationRequest(BaseModel):
    item_name: str
    category: str


@router.post("/inventory/ai/suggest-location")
async def ai_suggest_location(req: SuggestLocationRequest, request: Request):
    """Get AI suggestions for where to store an item."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    if not _inventory_ai:
        raise HTTPException(status_code=503, detail="Inventory AI not configured")

    async with get_session() as session:
        locations = await _get_locations_context(session)

    suggestions = await _inventory_ai.suggest_location(req.item_name, req.category, locations)

    # Enrich suggestions with location IDs
    async with get_session() as session:
        loc_result = await session.execute(
            select(InventoryLocation).where(InventoryLocation.active == True)
        )
        code_to_loc = {
            loc.code: {"id": loc.id, "name": loc.name, "house_code": loc.house_code}
            for loc in loc_result.scalars().all() if loc.code
        }
        for s in suggestions:
            loc_info = code_to_loc.get(s.get("location_code"))
            if loc_info:
                s["location_id"] = loc_info["id"]
                s["house_code"] = loc_info["house_code"]

    return {"suggestions": suggestions}


# ---------------------------------------------------------------------------
# Inventory — Stock Reports
# ---------------------------------------------------------------------------

class StockReportRequest(BaseModel):
    item_id: int
    report_type: str  # "low" or "missing"
    notes: Optional[str] = None


@router.post("/inventory/reports")
async def create_stock_report(req: StockReportRequest, request: Request):
    """Create a stock report (any role can report)."""
    role = getattr(request.state, "role", "cleaner")

    async with get_session() as session:
        # Verify item exists
        item_result = await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.location))
            .where(InventoryItem.id == req.item_id)
        )
        item = item_result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        report = StockReport(
            item_id=req.item_id,
            report_type=req.report_type,
            reported_by=role,
            notes=req.notes,
        )
        session.add(report)
        await session.flush()
        report_id = report.id

        # Get info for notification
        item_name = item.name
        location_name = item.location.name if item.location else ""

    # Send ntfy notification to Pierre
    if _ntfy and _ntfy.configured:
        if req.report_type == "missing":
            await _ntfy.send(
                title="🚫 Out of Stock",
                message=f"{item_name}" + (f" ({location_name})" if location_name else ""),
                priority=4,
                tags=["x"],
            )
        else:
            await _ntfy.notify_running_low(item_name, location_name)

    return {"id": report_id, "created": True}


@router.get("/inventory/reports")
async def get_stock_reports(
    resolved: Optional[bool] = None,
    request: Request = None,
):
    """Get stock reports. Default: unresolved only."""
    async with get_session() as session:
        query = (
            select(StockReport)
            .options(selectinload(StockReport.item).selectinload(InventoryItem.location))
            .order_by(StockReport.created_at.desc())
        )
        if resolved is not None:
            query = query.where(StockReport.resolved == resolved)
        else:
            query = query.where(StockReport.resolved == False)

        result = await session.execute(query)
        reports = result.scalars().all()
        return [
            {
                "id": r.id,
                "item_id": r.item_id,
                "item_name": r.item.name if r.item else None,
                "item_category": r.item.category if r.item else None,
                "location_name": r.item.location.name if r.item and r.item.location else None,
                "house_code": r.item.location.house_code if r.item and r.item.location else None,
                "report_type": r.report_type,
                "reported_by": r.reported_by,
                "notes": r.notes,
                "resolved": r.resolved,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
        ]


@router.put("/inventory/reports/{report_id}/resolve")
async def resolve_stock_report(report_id: int, request: Request):
    """Mark a stock report as resolved."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        result = await session.execute(
            select(StockReport).where(StockReport.id == report_id)
        )
        report = result.scalar_one_or_none()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        report.resolved = True
        report.resolved_at = datetime.utcnow()
        return {"id": report_id, "resolved": True}


@router.get("/inventory/shopping-list")
async def get_shopping_list(request: Request):
    """Get aggregated shopping list from unresolved stock reports."""
    if request.state.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    async with get_session() as session:
        result = await session.execute(
            select(StockReport)
            .options(selectinload(StockReport.item).selectinload(InventoryItem.location))
            .where(StockReport.resolved == False)
            .order_by(StockReport.created_at.asc())
        )
        reports = result.scalars().all()

        # Aggregate by item (multiple reports for same item → one shopping list entry)
        seen_items = {}
        for r in reports:
            if r.item_id not in seen_items:
                seen_items[r.item_id] = {
                    "item_id": r.item_id,
                    "name": r.item.name if r.item else "Unknown",
                    "category": r.item.category if r.item else None,
                    "brand": r.item.brand if r.item else None,
                    "purchase_url": r.item.purchase_url if r.item else None,
                    "house_code": r.item.location.house_code if r.item and r.item.location else None,
                    "location_name": r.item.location.name if r.item and r.item.location else None,
                    "report_count": 0,
                    "latest_report": None,
                    "worst_status": "low",
                    "report_ids": [],
                }
            entry = seen_items[r.item_id]
            entry["report_count"] += 1
            entry["report_ids"].append(r.id)
            if r.created_at:
                entry["latest_report"] = r.created_at.isoformat()
            if r.report_type == "missing":
                entry["worst_status"] = "missing"

        return sorted(seen_items.values(), key=lambda x: (0 if x["worst_status"] == "missing" else 1, x["name"]))
