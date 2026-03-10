"""
CallMetadata ORM model.

Maps to: CALL_METADATA_TABLE
Primary key: call_id (LiveKit room name / SmartFlo call SID)

Merges the old separate CallMetadata and ActiveCallContext tables into one:
- Tracks the full lifecycle of a call (scheduling → dialling → active → completed)
- Links to the customer via agreement_no FK
- phone_number is the runtime lookup key (used by agent and auto-dialer)
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db.database import Base


class CallMetadata(Base):
    __tablename__ = "call_metadata"

    # PK — LiveKit room name (e.g. "call-9876543210") or SmartFlo callSid
    call_id = Column(String(64), primary_key=True, nullable=False)

    # FK to Customer (nullable — may not be known at call creation time)
    agreement_no = Column(
        String(64),
        ForeignKey("customer_data.agreement_no", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Runtime contact details
    phone_number = Column(String(20), index=True)

    # Call lifecycle
    # Values: pending | active | completed | missedcall
    #   pending    → call not yet triggered via SmartFlo
    #   active     → SmartFlo has initiated the outbound call (ringing/in-progress)
    #   completed  → call ended with a connected conversation
    #   missedcall → call attempted but customer did not connect
    call_status = Column(String(30), default="pending")
    last_call_at = Column(DateTime(timezone=True))
    scheduled_time = Column(DateTime(timezone=True))
    manually_enabled = Column(Boolean, default=False)

    # Statistics
    call_count = Column(Integer, default=0)
    call_duration = Column(Integer)  # seconds

    # Audit timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    customer = relationship("Customer", back_populates="call_records")
    conversations = relationship("Conversation", back_populates="call_record")
    feedbacks = relationship("CustomerFeedback", back_populates="call_record")

    __table_args__ = (
        Index("idx_call_metadata_phone", "phone_number"),
        Index("idx_call_metadata_status", "call_status"),
        Index("idx_call_metadata_agreement", "agreement_no"),
        Index("idx_call_metadata_created", "created_at"),
    )
