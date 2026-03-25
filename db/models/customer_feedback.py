"""
CustomerFeedback ORM model.

Maps to: CUSTOMER_FEEDBACK_TABLE
One row per call outcome — stores all structured data collected by the agent
plus post-call disposition fields filled by the ops team.

Links to CallMetadata (via call_id) and Customer (via agreement_no).
"""
from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Boolean,
    Date,
    Time,
    DateTime,
    JSON,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db.database import Base


class CustomerFeedback(Base):
    __tablename__ = "customer_feedback"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # FK to CallMetadata
    call_id = Column(
        String(64),
        ForeignKey("call_metadata.call_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # FK to Customer
    agreement_no = Column(
        String(64),
        ForeignKey("customer_data.agreement_no", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Denormalised contact reference
    customer_phone = Column(String(20))

    # ── Identity & loan ─────────────────────────────────────────────────────
    identity_confirmed = Column(Boolean)
    loan_taken = Column(Boolean)
    last_month_payment = Column(Boolean)

    # ── Payee details ────────────────────────────────────────────────────────
    payee = Column(String(100))
    payee_name = Column(String(100))
    payee_contact = Column(String(20))

    # ── Payment details ──────────────────────────────────────────────────────
    payment_date = Column(Date)
    payment_amount = Column(String(100))
    payment_mode = Column(String(50))
    payment_reason = Column(String(150))

    # ── Field executive ──────────────────────────────────────────────────────
    field_executive_name = Column(String(100))
    field_executive_contact = Column(String(20))

    # ── Call outcome / disposition ───────────────────────────────────────────
    stage = Column(String(50))
    customer_name = Column(String(150))
    confirmed = Column(Boolean)
    category = Column(String(50))
    # disposition: "connected" (customer answered + spoke with agent) or "not_connected"
    disposition = Column(String(100))
    # sub_disposition: reserved for future use (NULL for now)
    sub_disposition = Column(String(100))

    # ── Campaign metadata ────────────────────────────────────────────────────
    campaign_name = Column(String(100))
    call_date = Column(Date)
    call_time = Column(Time)

    # ── Audit timestamps ─────────────────────────────────────────────────────
    started_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Full conversation transcript (JSON blob) ──────────────────────────────
    conversation_json = Column(JSON)

    # ── Cross-reference ──────────────────────────────────────────────────────
    conversation_id = Column(String(64))
    call_timestamp = Column(DateTime(timezone=True))

    # Relationships
    call_record = relationship("CallMetadata", back_populates="feedbacks")
    customer = relationship("Customer", back_populates="feedbacks")

    __table_args__ = (
        Index("idx_feedback_call_id", "call_id"),
        Index("idx_feedback_agreement", "agreement_no"),
        Index("idx_feedback_phone", "customer_phone"),
        Index("idx_feedback_created", "created_at"),
        Index("idx_feedback_disposition", "disposition"),
    )
