"""Auto-learning from host replies.

When Pierre sends a message, this module:
1. Tracks AI accuracy per category (for future auto-reply graduation)
2. Stores edited AI drafts as learned corrections in the knowledge base
3. Extracts patterns from manual replies for future AI context
"""

import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AutoReplyCategory, KnowledgeEntry, Message, Reservation

logger = logging.getLogger(__name__)


def _fingerprint(text: str) -> str:
    """Create a short fingerprint of text for dedup."""
    normalized = " ".join(text.lower().split())[:200]
    return normalized


async def record_reply_outcome(
    session: AsyncSession,
    message: Message,
    reservation: Reservation,
) -> None:
    """Process a sent message and extract learnings.

    Called after every message Pierre sends.
    """
    if not message.ai_generated:
        # Manual reply — no AI tracking needed for now
        # Future: could detect Q&A patterns and store as learned knowledge
        return

    category = message.feedback_note  # We store ai_category here via the route
    if not category:
        category = "General"

    # Update AutoReplyCategory stats
    result = await session.execute(
        select(AutoReplyCategory).where(AutoReplyCategory.category == category)
    )
    cat_record = result.scalar_one_or_none()

    if not cat_record:
        cat_record = AutoReplyCategory(category=category)
        session.add(cat_record)
        await session.flush()

    cat_record.total_drafts += 1
    cat_record.updated_at = datetime.utcnow()

    if not message.was_edited:
        # AI draft sent as-is — great, increment accuracy counter
        cat_record.sent_unedited += 1
        logger.info(
            "AI draft sent unedited for category %s (%d/%d accuracy)",
            category,
            cat_record.sent_unedited,
            cat_record.total_drafts,
        )
        return

    # AI draft was edited — store the correction as learned knowledge
    original = message.original_ai_draft
    final = message.body

    if not original or not final:
        return

    # Don't store if the edit was trivial (just whitespace/punctuation)
    if _fingerprint(original) == _fingerprint(final):
        logger.debug("Edit was trivial, skipping learning")
        return

    # Check for duplicate learned entries
    fp = _fingerprint(final)
    existing = await session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.source == "learned",
            KnowledgeEntry.category == category,
        )
    )
    for entry in existing.scalars().all():
        if _fingerprint(entry.answer) == fp:
            logger.debug("Duplicate learned entry, skipping")
            return

    # Build the learned knowledge entry
    guest_name = reservation.guest_name.split()[0] if reservation.guest_name else "guest"

    # Get the last guest message for context
    msgs = await session.execute(
        select(Message)
        .where(
            Message.reservation_id == reservation.id,
            Message.sender == "guest",
            Message.timestamp < message.timestamp,
        )
        .order_by(Message.timestamp.desc())
        .limit(1)
    )
    last_guest_msg = msgs.scalar_one_or_none()
    question_context = last_guest_msg.body[:200] if last_guest_msg else None

    # Store as learned knowledge
    learned = KnowledgeEntry(
        category=category,
        question=f"Guest asked: {question_context}" if question_context else None,
        answer=f"Preferred reply style: {final}",
        source="learned",
        active=True,
    )
    session.add(learned)

    logger.info(
        "Learned from edited AI draft in category %s (original: %d chars → final: %d chars)",
        category,
        len(original),
        len(final),
    )
