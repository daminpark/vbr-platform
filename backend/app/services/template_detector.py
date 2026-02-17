"""Detect template/auto-messages vs real human replies.

Templates are identified by text similarity — if a host message body
(after stripping the greeting line) matches 3+ other messages, it's
a template.

This runs as a batch process after sync, and also provides a real-time
check for new messages.
"""

import logging
from collections import defaultdict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message

logger = logging.getLogger(__name__)


def _normalize_body(body: str) -> str:
    """Strip greeting line and normalize for fingerprint comparison."""
    lines = body.strip().split("\n")
    # Remove greeting line (Hi X, Hello X, etc.)
    if lines and any(
        lines[0].lower().startswith(g) for g in ("hi ", "hello ", "hey ", "dear ")
    ):
        lines = lines[1:]
    # Join and collapse whitespace, take first 150 chars as fingerprint
    text = " ".join(" ".join(lines).split())
    return text[:150]


async def detect_and_tag_templates(session: AsyncSession, min_occurrences: int = 3) -> int:
    """Scan all host messages and tag those appearing 3+ times as templates.

    Returns the number of messages tagged.
    """
    result = await session.execute(
        select(Message.id, Message.body).where(Message.sender == "host")
    )
    rows = result.all()

    # Build fingerprint → message IDs mapping
    fingerprints: dict[str, list[int]] = defaultdict(list)
    for msg_id, body in rows:
        fp = _normalize_body(body)
        fingerprints[fp].append(msg_id)

    # Collect IDs of template messages
    template_ids = []
    for fp, ids in fingerprints.items():
        if len(ids) >= min_occurrences:
            template_ids.extend(ids)

    if not template_ids:
        return 0

    # Batch update
    await session.execute(
        update(Message)
        .where(Message.id.in_(template_ids))
        .values(is_template=True)
    )

    # Also ensure non-templates are unmarked (in case a message was
    # previously wrongly tagged)
    non_template_ids = []
    for fp, ids in fingerprints.items():
        if len(ids) < min_occurrences:
            non_template_ids.extend(ids)
    if non_template_ids:
        await session.execute(
            update(Message)
            .where(Message.id.in_(non_template_ids))
            .values(is_template=False)
        )

    logger.info(
        "Template detection: %d templates tagged, %d real replies",
        len(template_ids),
        len(non_template_ids),
    )
    return len(template_ids)


def is_likely_template(body: str, known_fingerprints: set[str]) -> bool:
    """Real-time check if a new message matches known template fingerprints."""
    fp = _normalize_body(body)
    return fp in known_fingerprints
