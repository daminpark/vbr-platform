"""Import property knowledge from 195vbr en.json into KnowledgeEntry table.

Parses the content_html and static_html sections, strips HTML to plain text,
and creates categorised entries for the AI knowledge base.
"""

import logging
import re
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeEntry

logger = logging.getLogger(__name__)

# Map en.json keys → knowledge categories
CATEGORY_MAP = {
    # content_html keys
    "house193": "Address",
    "house195": "Address",
    "wifi193": "WiFi",
    "wifi195": "WiFi",
    "room1": "Bedroom",
    "room2": "Bedroom",
    "room3": "Bedroom",
    "room4": "Bedroom",
    "room5": "Bedroom",
    "room6": "Bedroom",
    "rooma": "Bedroom",
    "roomb": "Bedroom",
    "wholeHomeBedroomsDetailed": "Bedroom",
    "wholeHomeBedroomsCombined": "Bedroom",
    "storageUnderBed": "Bedroom",
    "storageInBed": "Bedroom",
    "wholeHomeBathrooms": "Bathroom",
    "wholeHomeBathroomsCombined": "Bathroom",
    "bathroomA": "Bathroom",
    "bathroomB": "Bathroom",
    "bathroomC": "Bathroom",
    "bathroomShared": "Bathroom",
    "bathroomPrivate": "Bathroom",
    "kitchenShared": "Kitchen",
    "kitchenPrivate": "Kitchen",
    "kitchenBase": "Kitchen",
    "windowsStandard": "Windows",
    "windowsTiltTurn": "Windows",
    "noLaundry": "Laundry",
    "hasLaundry": "Laundry",
    "wholeHomeLuggage": "CheckIn",
    "checkinStaticDetailed": "CheckIn",
    "checkoutStatic": "CheckOut",
    "checkoutWholeHome": "CheckOut",
    "checkoutStaticDetailed": "CheckOut",
    "heatingBase": "Heating",
    "heatingSmartAddon": "Heating",
    "lightsNote": "Lighting",
    "wholeHomeRubbish": "Rubbish",
    # static_html keys
    "what_not_to_bring": "Amenities",
    "domestic_directions": "Transport",
    "airport_directions": "Transport",
    "getting_around": "Transport",
    "codetimes": "LockInfo",
    "ironing": "Laundry",
    "troubleshooting": "Troubleshooting",
    "contact": "Contact",
    "tv": "TV",
    "local_guidebook": "LocalArea",
}

# Natural-language questions for common keys (improves prompt context)
QUESTION_MAP = {
    "wifi193": "What is the WiFi for 193?",
    "wifi195": "What is the WiFi for 195?",
    "house193": "What is the address of 193?",
    "house195": "What is the address of 195?",
    "checkinStaticDetailed": "How do I check in?",
    "checkoutStatic": "What time is check-out?",
    "checkoutStaticDetailed": "What are the check-out procedures?",
    "checkoutWholeHome": "What is check-out like for whole-home bookings?",
    "wholeHomeLuggage": "Where can I store my luggage?",
    "kitchenShared": "How do I use the shared kitchen?",
    "kitchenPrivate": "How does the private kitchen work?",
    "kitchenBase": "What appliances are in the kitchen?",
    "noLaundry": "Is there a washing machine?",
    "hasLaundry": "How do I use the washing machine?",
    "heatingBase": "How do I control the heating?",
    "heatingSmartAddon": "How do I adjust the thermostat?",
    "codetimes": "How do the door codes work?",
    "what_not_to_bring": "What do I need to bring?",
    "domestic_directions": "How do I get to the house?",
    "airport_directions": "How do I get from the airport?",
    "getting_around": "How do I get around London?",
    "tv": "How does the TV work?",
    "contact": "How do I contact the host?",
    "local_guidebook": "What restaurants and sights are nearby?",
    "ironing": "Is there an iron?",
    "troubleshooting": "What if the door lock runs out of battery?",
    "bathroomA": "Where is Bathroom A?",
    "bathroomB": "Where is Bathroom B?",
    "bathroomC": "Where is Bathroom C?",
    "bathroomShared": "How do the shared bathrooms work?",
    "bathroomPrivate": "Do I have a private bathroom?",
    "lightsNote": "How do the lights work?",
    "windowsStandard": "How do the windows work?",
    "windowsTiltTurn": "How do the tilt-and-turn windows work?",
}


def strip_html(html: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<li>", "- ", text)
    text = re.sub(r"</?(p|ul|ol|li|div|h[1-6]|strong|em|b|i|a|span|iframe|img|table|tr|td|th|thead|tbody)[^>]*>", "", text)
    text = re.sub(r"<[^>]+>", "", text)  # catch-all remaining tags
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get_house_tag(key: str) -> str:
    """Tag entry with house specificity based on the key name."""
    if "193" in key and "195" not in key:
        return " [193 only]"
    if "195" in key and "193" not in key:
        return " [195 only]"
    if "Combined" in key or "wholeHome" in key.lower():
        return " [whole house booking]"
    return ""


async def import_from_en_json(
    session: AsyncSession,
    json_data: dict,
    replace: bool = True,
) -> int:
    """Import knowledge entries from parsed en.json data.

    Args:
        session: Database session
        json_data: Parsed en.json dict (must have content_html and/or static_html)
        replace: If True, delete existing imported entries first

    Returns:
        Number of entries imported
    """
    if replace:
        await session.execute(
            delete(KnowledgeEntry).where(KnowledgeEntry.source == "imported")
        )

    count = 0
    for section_key in ("content_html", "static_html"):
        section = json_data.get(section_key, {})
        for key, html_value in section.items():
            if not html_value or not html_value.strip():
                continue

            category = CATEGORY_MAP.get(key)
            if not category:
                logger.debug("Skipping unmapped key: %s.%s", section_key, key)
                continue

            plain_text = strip_html(html_value)
            if not plain_text:
                continue

            question = QUESTION_MAP.get(key)
            house_tag = _get_house_tag(key)

            entry = KnowledgeEntry(
                category=category,
                question=question,
                answer=plain_text + house_tag,
                source="imported",
                active=True,
            )
            session.add(entry)
            count += 1

    logger.info("Imported %d knowledge entries from en.json", count)
    return count
