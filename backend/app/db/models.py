"""Database models for VBR Platform."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Host Tools synced data
# ---------------------------------------------------------------------------

class Listing(Base):
    """A Host Tools listing (e.g., '195 Room 1', '193 Whole House')."""

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hosttools_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    house_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # "193" or "195"
    picture_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_synced: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    reservations: Mapped[list["Reservation"]] = relationship("Reservation", back_populates="listing")


class Reservation(Base):
    """A guest reservation from Host Tools."""

    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hosttools_id: Mapped[str] = mapped_column(String(100), unique=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    guest_name: Mapped[str] = mapped_column(String(255))
    guest_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    guest_picture_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    check_in: Mapped[datetime] = mapped_column(DateTime)
    check_out: Mapped[datetime] = mapped_column(DateTime)
    num_guests: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="confirmed")
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_synced: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    listing: Mapped["Listing"] = relationship("Listing", back_populates="reservations")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="reservation", order_by="Message.timestamp"
    )


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------

class Message(Base):
    """A message in a guest conversation."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), index=True)
    hosttools_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Who sent it: guest, host, ai, system/template
    sender: Mapped[str] = mapped_column(String(20))
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=True)

    # Content
    body: Mapped[str] = mapped_column(Text)
    body_original: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # pre-translation
    detected_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    translated: Mapped[bool] = mapped_column(Boolean, default=False)

    # AI metadata
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ai_auto_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Feedback: was the AI draft edited before sending?
    was_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    original_ai_draft: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # what AI originally wrote
    feedback_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Pierre's correction explanation

    # Template detection
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)

    reservation: Mapped["Reservation"] = relationship("Reservation", back_populates="messages")


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

class KnowledgeEntry(Base):
    """Knowledge base entry — injected into Claude's system prompt."""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual, learned, imported
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# AI auto-reply category tracking
# ---------------------------------------------------------------------------

class AutoReplyCategory(Base):
    """Tracks AI accuracy per question category for auto-reply graduation."""

    __tablename__ = "auto_reply_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(100), unique=True)
    total_drafts: Mapped[int] = mapped_column(Integer, default=0)
    sent_unedited: Mapped[int] = mapped_column(Integer, default=0)
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    never_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)  # flagged categories
    enabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# Scheduled message templates
# ---------------------------------------------------------------------------

class MessageTemplate(Base):
    """A scheduled message template (e.g., check-in instructions, checkout reminder)."""

    __tablename__ = "message_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    trigger: Mapped[str] = mapped_column(String(50))  # checkin_day, checkout_day, day_before_checkin, etc.
    body: Mapped[str] = mapped_column(Text)  # supports {guest_name}, {check_in}, {listing_name}, etc.
    hours_offset: Mapped[int] = mapped_column(Integer, default=14)  # hour of day to send (e.g., 14 = 2pm)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    house_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # "193", "195", or null=both
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScheduledMessageLog(Base):
    """Log of sent scheduled messages — prevents duplicates."""

    __tablename__ = "scheduled_message_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("message_templates.id"), index=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    body_sent: Mapped[str] = mapped_column(Text)  # actual body after placeholder substitution


# ---------------------------------------------------------------------------
# Inventory management
# ---------------------------------------------------------------------------

class InventoryLocation(Base):
    """A storage location within a property."""

    __tablename__ = "inventory_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    house_code: Mapped[str] = mapped_column(String(10), index=True)  # "193", "195", "shared"
    name: Mapped[str] = mapped_column(String(255))  # "Kitchen", "Toolshed"
    code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, unique=True)  # "193.W", "195.Z"
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=True, index=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # quirks, access notes
    guest_accessible: Mapped[bool] = mapped_column(Boolean, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    outdoor: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    parent: Mapped[Optional["InventoryLocation"]] = relationship(
        "InventoryLocation", remote_side="InventoryLocation.id", back_populates="children"
    )
    children: Mapped[list["InventoryLocation"]] = relationship(
        "InventoryLocation", back_populates="parent"
    )
    items: Mapped[list["InventoryItem"]] = relationship(
        "InventoryItem", back_populates="location"
    )


class InventoryItem(Base):
    """An inventory item tracked across properties."""

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(100), index=True)  # cleaning, tools, linen, etc.
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=True, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # bottles, packs, units
    min_quantity: Mapped[int] = mapped_column(Integer, default=0)  # 0 = no low-stock alert

    # AI-generated search aliases (comma-separated) for fast fuzzy search
    search_aliases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Product guide (future-ready)
    product_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suitable_for: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # "kitchen surfaces, tiles"

    # Renovation support
    status: Mapped[str] = mapped_column(String(20), default="in_use")  # in_use, out_for_renovation, retired

    # Shopping
    purchase_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Standard fields
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    location: Mapped[Optional["InventoryLocation"]] = relationship(
        "InventoryLocation", back_populates="items"
    )
    stock_reports: Mapped[list["StockReport"]] = relationship(
        "StockReport", back_populates="item", order_by="StockReport.created_at.desc()"
    )


class StockReport(Base):
    """A stock report from a cleaner — item running low or missing."""

    __tablename__ = "stock_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(20))  # "low", "missing"
    reported_by: Mapped[str] = mapped_column(String(50), default="cleaner")  # role who reported
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    item: Mapped["InventoryItem"] = relationship("InventoryItem", back_populates="stock_reports")
