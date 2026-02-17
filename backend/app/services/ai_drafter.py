"""AI Draft Reply Engine — Gemini integration for guest message drafts.

Generates suggested replies to guest messages using Gemini Flash, injecting
property knowledge and conversation context. Draft-only: all messages
require human approval before sending.
"""

import logging
import re
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import KnowledgeEntry, Message, Reservation

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Pierre's AI assistant for managing guest communications at two London \
guesthouses: 193 and 195 Vauxhall Bridge Road, Pimlico, SW1V 1ER.

YOUR GOAL: Help Pierre achieve 5-star reviews by crafting perfect guest replies.

STYLE GUIDE:
- Concise and warm — never rambling, never cold
- One step ahead: answer the question, then anticipate what they'll ask next
- Use the guest's first name naturally
- Friendly but professional — boutique host, not a corporate hotel
- Keep messages short (2-4 sentences for simple questions, up to a short paragraph for complex ones)
- Use line breaks for readability if replying to multiple points
- Never use excessive exclamation marks or emojis (one emoji max, only if natural)
- Never start with "Thank you for your message" or similar filler
- If you don't know something, say so honestly and offer to find out — never fabricate
- For time-sensitive questions (locked out, can't get in), prioritise speed and clarity

RULES:
- Only share door codes, WiFi passwords, or security info if the knowledge base \
explicitly provides it for this guest's property
- Never promise things you're unsure about (early check-in, late checkout) — \
instead say "let me check and get back to you"
- For complaints or damage reports: acknowledge, empathise, say Pierre will look \
into it personally
- For emergencies: provide the emergency WhatsApp number (+44 7443 618207) immediately
- For questions about other guests or bookings: maintain privacy, never share details
- Sign off warmly but briefly ("Enjoy your stay!" / "Let me know if you need anything")

You will receive:
1. The guest's booking context (property, dates, number of guests)
2. Relevant knowledge base entries for the property
3. The conversation history
4. The latest guest message to reply to

Generate a reply that Pierre can send as-is or edit."""

# Question categories for tracking AI accuracy per topic
QUESTION_CATEGORIES = [
    "WiFi", "CheckIn", "CheckOut", "Bathroom", "Kitchen", "Laundry",
    "Heating", "TV", "Transport", "LocalArea", "LockInfo", "Amenities",
    "EarlyCheckIn", "LateCheckOut", "LuggageStorage", "Complaint",
    "Emergency", "Pricing", "Cancellation", "SpecialRequest", "General",
]


class AIDrafter:
    """Gemini-powered draft reply generator."""

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-2.0-flash"

    async def generate_draft(
        self,
        session: AsyncSession,
        reservation_id: int,
    ) -> dict:
        """Generate an AI draft reply for the latest guest message.

        Returns:
            {
                "draft": str,           # The suggested reply text
                "confidence": float,    # 0.0-1.0 confidence score
                "category": str,        # Detected question category
                "knowledge_used": list, # IDs of knowledge entries referenced
                "tokens_used": int,     # Total tokens consumed
            }
        """
        # 1. Load reservation with listing
        res_result = await session.execute(
            select(Reservation)
            .options(selectinload(Reservation.listing))
            .where(Reservation.id == reservation_id)
        )
        reservation = res_result.scalar_one_or_none()
        if not reservation:
            raise ValueError(f"Reservation {reservation_id} not found")

        # 2. Load conversation history (excluding templates and unsent drafts)
        msg_result = await session.execute(
            select(Message)
            .where(
                Message.reservation_id == reservation_id,
                Message.is_template == False,
                Message.is_draft == False,
            )
            .order_by(Message.timestamp.asc())
        )
        messages = msg_result.scalars().all()

        if not messages:
            raise ValueError("No messages in conversation")

        # 3. Load relevant knowledge entries (filtered by house)
        house_code = reservation.listing.house_code if reservation.listing else None
        knowledge_entries = await self._get_relevant_knowledge(session, house_code)

        # 4. Build the prompt
        user_prompt = self._build_user_prompt(reservation, messages, knowledge_entries)

        # 5. Call Gemini
        logger.info(
            "Generating draft for reservation %d (%s)",
            reservation_id,
            reservation.guest_name,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=1024,
                temperature=0.4,
            ),
        )

        # 6. Parse response
        raw_text = response.text
        draft, confidence, category = self._parse_response(raw_text)

        # Token usage
        tokens_used = 0
        if response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count or 0
            response_tokens = response.usage_metadata.candidates_token_count or 0
            tokens_used = prompt_tokens + response_tokens

        logger.info(
            "Draft generated: category=%s confidence=%.2f tokens=%d",
            category, confidence, tokens_used,
        )

        return {
            "draft": draft,
            "confidence": confidence,
            "category": category,
            "knowledge_used": [e.id for e in knowledge_entries],
            "tokens_used": tokens_used,
        }

    async def _get_relevant_knowledge(
        self,
        session: AsyncSession,
        house_code: Optional[str],
    ) -> list[KnowledgeEntry]:
        """Get knowledge entries relevant to the guest's property.

        With ~50 entries total, we inject all house-relevant entries.
        No embeddings or RAG needed at this scale.
        """
        result = await session.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.active == True)
        )
        all_entries = result.scalars().all()

        relevant = []
        for entry in all_entries:
            answer_lower = entry.answer.lower()
            # Skip entries tagged for the wrong house
            if house_code == "193" and "[195 only]" in answer_lower:
                continue
            if house_code == "195" and "[193 only]" in answer_lower:
                continue
            relevant.append(entry)

        return relevant

    def _build_user_prompt(
        self,
        reservation: Reservation,
        messages: list[Message],
        knowledge: list[KnowledgeEntry],
    ) -> str:
        """Build the user prompt with booking context, knowledge, and conversation."""
        parts = []

        # Booking context
        listing_name = reservation.listing.name if reservation.listing else "Unknown"
        house_code = reservation.listing.house_code if reservation.listing else "unknown"
        guest_first = reservation.guest_name.split()[0] if reservation.guest_name else "Guest"

        parts.append(f"""## Booking Context
- Guest: {reservation.guest_name} (first name: {guest_first})
- Property: {listing_name} (House {house_code})
- Check-in: {reservation.check_in.strftime('%a %d %b %Y') if reservation.check_in else 'unknown'}
- Check-out: {reservation.check_out.strftime('%a %d %b %Y') if reservation.check_out else 'unknown'}
- Guests: {reservation.num_guests or 'unknown'}
- Platform: {reservation.platform or 'unknown'}
- Today: {datetime.utcnow().strftime('%a %d %b %Y %H:%M UTC')}""")

        # Knowledge base
        if knowledge:
            kb_lines = []
            for e in knowledge:
                if e.question:
                    kb_lines.append(f"[{e.category}] Q: {e.question}\nA: {e.answer}")
                else:
                    kb_lines.append(f"[{e.category}] {e.answer}")
            parts.append("## Property Knowledge Base\n" + "\n\n".join(kb_lines))

        # Conversation history (last 20 messages to stay within context)
        recent_messages = messages[-20:]
        conv_lines = []
        for msg in recent_messages:
            role = "GUEST" if msg.sender == "guest" else "HOST"
            time_str = msg.timestamp.strftime("%d %b %H:%M") if msg.timestamp else ""
            conv_lines.append(f"[{time_str}] {role}: {msg.body}")
        parts.append("## Conversation History\n" + "\n\n".join(conv_lines))

        # Task
        parts.append("""## Your Task
Reply to the guest's latest message. Provide your response in this exact format:

REPLY:
<your suggested reply here>

CONFIDENCE: <number between 0.0 and 1.0>
CATEGORY: <one of: WiFi, CheckIn, CheckOut, Bathroom, Kitchen, Laundry, Heating, TV, Transport, LocalArea, LockInfo, Amenities, EarlyCheckIn, LateCheckOut, LuggageStorage, Complaint, Emergency, Pricing, Cancellation, SpecialRequest, General>""")

        return "\n\n".join(parts)

    def _parse_response(self, raw: str) -> tuple[str, float, str]:
        """Parse Gemini's structured response into (draft, confidence, category).

        Falls back gracefully if the format is unexpected.
        """
        # Extract REPLY section
        reply_match = re.search(
            r"REPLY:\s*\n(.*?)(?=\nCONFIDENCE:|\Z)",
            raw,
            re.DOTALL,
        )
        draft = reply_match.group(1).strip() if reply_match else raw.strip()

        # If the draft still contains our markers, clean them out
        draft = re.sub(r"\nCONFIDENCE:.*", "", draft, flags=re.DOTALL).strip()

        # Extract CONFIDENCE
        conf_match = re.search(r"CONFIDENCE:\s*([\d.]+)", raw)
        confidence = float(conf_match.group(1)) if conf_match else 0.7

        # Extract CATEGORY
        cat_match = re.search(r"CATEGORY:\s*(\w+)", raw)
        category = cat_match.group(1) if cat_match else "General"
        if category not in QUESTION_CATEGORIES:
            category = "General"

        return draft, min(max(confidence, 0.0), 1.0), category
