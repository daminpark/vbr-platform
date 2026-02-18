"""AI-powered inventory operations — Gemini integration for parsing,
searching, and suggesting locations for inventory items.

Uses the same Gemini 2.0 Flash model and patterns as the guest messaging
AI drafter, but with inventory-specific system prompts and output parsing.
"""

import json
import logging
import re
from typing import Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PARSE_SYSTEM_PROMPT = """\
You are an inventory assistant for two London guesthouses (193 and 195 Vauxhall Bridge Road).
Your job is to parse natural language input into structured inventory data.

You will receive:
1. The user's free-text input (one or more items)
2. A list of known storage locations

Extract each item mentioned and return JSON:
{
  "items": [
    {
      "name": "Human-readable item name (capitalised, concise)",
      "quantity": 1,
      "unit": null or "bottles"/"packs"/"rolls"/"pairs"/etc,
      "category": "cleaning"/"tools"/"electrical"/"plumbing"/"linen"/"kitchen"/"hardware"/"paint"/"renovation"/"guest_supplies"/"laundry"/"other",
      "location_code": "matched location code or null if unclear",
      "location_name": "matched location name or the raw text if no match"
    }
  ]
}

Rules:
- Infer category from the item name (bleach → cleaning, drill bits → tools, sheets → linen)
- Match location text to the closest known location code (e.g., "195 kitchen" → "195.K")
- If quantity isn't mentioned, default to 1
- If unit isn't clear, set to null
- Be generous with matching — "toolshed" = "193.W.S", "under the sink" likely = kitchen
- Keep item names concise but descriptive: "WD-40" not "a can of WD-40 spray"
- For ambiguous items, make your best guess for category
"""

SEARCH_SYSTEM_PROMPT = """\
You are a search assistant for a London guesthouse inventory system.
Cleaners and handymen search using casual, imprecise language — often British English slang.

Your job is to match their search query to the correct inventory items.

Examples of how people search:
- "drain stuff" → Drain Unblocker
- "that thing for blocked sinks" → Drain Unblocker / Plunger
- "loo roll" → Toilet Paper
- "kitchen spray" → Kitchen Surface Cleaner
- "hoover bags" → Vacuum Cleaner Bags
- "allen key" → Allen Key Set
- "the blue cleaning stuff" → could be multiple products
- "spare sheets" → Bed Sheets
- "bulbs" → Spare Light Bulbs

Return JSON array of matches:
[
  {"item_id": 123, "name": "Drain Unblocker", "score": 0.95, "reason": "direct synonym match"}
]

Rules:
- Only return items that genuinely match. Don't pad results.
- Score: 1.0 = exact match, 0.7+ = very likely, 0.5+ = possible, <0.5 = don't include
- Include a brief reason for each match
- Maximum 10 results, sorted by score descending
- If nothing matches at all, return an empty array []
"""

LOCATION_SUGGEST_PROMPT = """\
You are helping organise inventory across two London guesthouses (193 and 195 VBR).
Suggest the best storage location for a new item.

Consider these factors (in order of importance):
1. WHO needs it? Cleaners daily → cleaning storage (Z). Renovation/trade → shed (W) or V areas.
2. GUEST-VISIBLE? If the location is guest-accessible, only store tidy/boxed items.
3. HOW OFTEN accessed? Daily → indoor, easy reach. Rare → shed, upper shelves, V areas.
4. WEATHER? Outdoor locations — only items that handle damp/cold.
5. SIZE? Bulky → large cupboards, W areas, U wardrobe. Small → Z upper shelves, kitchen cabinets.
6. CATEGORY GROUPING? Keep like with like — all plumbing together, all electrical together.
7. SECURITY? Valuable → locked locations (193.W Shed, 195.U).

Return JSON:
{
  "suggestions": [
    {"location_code": "193.Z", "location_name": "Cleaning Storage", "reason": "Daily-use cleaning product, keep with other cleaning supplies"},
    {"location_code": "193.Z.U", "location_name": "Cleaning Storage Upper", "reason": "Backup stock, not needed every day"}
  ]
}

Return 1-3 suggestions, best first.
"""

ALIASES_SYSTEM_PROMPT = """\
You are helping build a search index for a guesthouse inventory system.
Given an item name and category, generate common alternative names, slang terms,
and descriptions that someone (especially a British cleaner or handyman) might
use when searching for this item.

Return a JSON array of 5-10 alternative search terms:
["drain cleaner", "sink unblocker", "drain stuff", "unblocking liquid", "blocked drain fix"]

Rules:
- Include British English terms (loo = toilet, hoover = vacuum, etc.)
- Include abbreviated forms (bleach = "bleach", "beach" (typo))
- Include descriptive phrases ("that thing for blocked drains")
- Include common misspellings or close variants
- Don't include the original item name — that's already indexed
- Keep each alias short (1-5 words)
"""

BULK_IMPORT_SYSTEM_PROMPT = """\
You are an inventory assistant. Parse a freeform text dump of items and their locations
into structured data. The text is from someone rapidly listing what's in each storage area.

Format varies — could be:
- "193 toolshed: wd40, electrical tape, spare bulbs, drill bits"
- "Under 195 kitchen sink — fairy, bleach, drain unblocker"
- "Cleaning room: 3x bottles of bleach, sponges (new pack), mop bucket"

You will also receive a list of known locations to match against.

Return JSON:
{
  "items": [
    {
      "name": "WD-40",
      "quantity": 1,
      "unit": null,
      "category": "tools",
      "location_code": "193.W.S",
      "location_name": "Shed (Toolshed)"
    }
  ]
}

Rules:
- Parse ALL items mentioned, even if formatting is inconsistent
- Infer category from item type (cleaning products, tools, linen, etc.)
- Match location text to known location codes where possible
- "3x bottles" → quantity: 3, unit: "bottles"
- "(new pack)" or similar notes → include in the name or notes, not as separate items
- Keep item names clean and consistent: "WD-40" not "wd40", "Electrical Tape" not "electrical tape"
- Capitalise item names properly
- If a location applies to multiple items listed after it, apply it to all
"""


class InventoryAI:
    """Gemini-powered AI for inventory operations."""

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-2.0-flash"

    async def _call_gemini(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Make a Gemini API call and return the raw text response."""
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return response.text

    def _extract_json(self, text: str) -> dict | list:
        """Extract JSON from a Gemini response that may include markdown fences."""
        # Try to find JSON in code blocks first
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1).strip())

        # Try parsing the whole text as JSON
        # Find the first { or [ and last } or ]
        start_obj = text.find("{")
        start_arr = text.find("[")
        if start_obj == -1 and start_arr == -1:
            raise ValueError(f"No JSON found in response: {text[:200]}")

        if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
            end = text.rfind("]")
            return json.loads(text[start_arr : end + 1])
        else:
            end = text.rfind("}")
            return json.loads(text[start_obj : end + 1])

    def _format_locations_context(self, locations: list[dict]) -> str:
        """Format location data for injection into prompts."""
        lines = ["Known storage locations:"]
        for loc in locations:
            flags = []
            if loc.get("outdoor"):
                flags.append("outdoor")
            if loc.get("locked"):
                flags.append("locked")
            if loc.get("guest_accessible"):
                flags.append("guest-accessible")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            parent_info = f" (inside {loc['parent_name']})" if loc.get("parent_name") else ""
            desc = f" — {loc['description']}" if loc.get("description") else ""
            lines.append(
                f"- {loc['code']}: {loc['house_code']} {loc['name']}{parent_info}{flag_str}{desc}"
            )
        return "\n".join(lines)

    # ---- Public methods ----

    async def parse_natural_language_input(
        self,
        text: str,
        locations: list[dict],
    ) -> dict:
        """Parse natural language like 'put 3 sponges in 195 kitchen' into structured data.

        Returns: {"items": [{"name", "quantity", "unit", "category", "location_code", "location_name"}]}
        """
        user_prompt = f"{self._format_locations_context(locations)}\n\nParse this input:\n{text}"

        try:
            raw = await self._call_gemini(PARSE_SYSTEM_PROMPT, user_prompt)
            result = self._extract_json(raw)
            if isinstance(result, dict) and "items" in result:
                return result
            return {"items": result if isinstance(result, list) else []}
        except Exception as e:
            logger.error("AI parse failed: %s", e)
            return {"items": []}

    async def parse_bulk_import(
        self,
        text_dump: str,
        locations: list[dict],
    ) -> dict:
        """Parse a freeform text dump into structured inventory data.

        Returns: {"items": [{"name", "quantity", "unit", "category", "location_code", "location_name"}]}
        """
        user_prompt = (
            f"{self._format_locations_context(locations)}\n\n"
            f"Parse this inventory dump:\n\n{text_dump}"
        )

        try:
            raw = await self._call_gemini(
                BULK_IMPORT_SYSTEM_PROMPT, user_prompt, max_tokens=4096
            )
            result = self._extract_json(raw)
            if isinstance(result, dict) and "items" in result:
                return result
            return {"items": result if isinstance(result, list) else []}
        except Exception as e:
            logger.error("AI bulk import parse failed: %s", e)
            return {"items": []}

    async def suggest_location(
        self,
        item_name: str,
        category: str,
        locations: list[dict],
    ) -> list[dict]:
        """Suggest best storage locations for an item.

        Returns: [{"location_code", "location_name", "reason"}]
        """
        user_prompt = (
            f"{self._format_locations_context(locations)}\n\n"
            f"Where should I store this item?\n"
            f"Item: {item_name}\n"
            f"Category: {category}"
        )

        try:
            raw = await self._call_gemini(LOCATION_SUGGEST_PROMPT, user_prompt)
            result = self._extract_json(raw)
            if isinstance(result, dict) and "suggestions" in result:
                return result["suggestions"]
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.error("AI location suggestion failed: %s", e)
            return []

    async def fuzzy_search(
        self,
        query: str,
        items: list[dict],
    ) -> list[dict]:
        """Match a fuzzy/colloquial query to inventory items.

        Called as Tier 2 fallback when DB LIKE search returns no results.
        Returns: [{"item_id", "name", "score", "reason"}]
        """
        items_list = "\n".join(
            f"- ID {item['id']}: {item['name']} (category: {item['category']}, "
            f"location: {item.get('location_name', 'unknown')})"
            for item in items
        )
        user_prompt = (
            f"Inventory items:\n{items_list}\n\n"
            f"Search query: \"{query}\"\n\n"
            f"Which items match this search?"
        )

        try:
            raw = await self._call_gemini(SEARCH_SYSTEM_PROMPT, user_prompt)
            result = self._extract_json(raw)
            matches = result if isinstance(result, list) else []
            # Sort by score and limit
            matches.sort(key=lambda m: m.get("score", 0), reverse=True)
            return matches[:10]
        except Exception as e:
            logger.error("AI fuzzy search failed: %s", e)
            return []

    async def generate_search_aliases(
        self,
        item_name: str,
        category: str,
    ) -> list[str]:
        """Generate colloquial search aliases for an item.

        Called once on item creation. Results stored in search_aliases field.
        Returns: ["drain cleaner", "sink unblocker", "drain stuff", ...]
        """
        user_prompt = (
            f"Item: {item_name}\n"
            f"Category: {category}\n\n"
            f"Generate alternative search terms for this item."
        )

        try:
            raw = await self._call_gemini(ALIASES_SYSTEM_PROMPT, user_prompt, max_tokens=512)
            result = self._extract_json(raw)
            if isinstance(result, list):
                return [str(a).strip() for a in result if a]
            return []
        except Exception as e:
            logger.error("AI alias generation failed: %s", e)
            return []
