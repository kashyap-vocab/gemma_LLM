"""
SQLAlchemy ORM models for all database tables.
"""
from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Text,
    Boolean,
    Numeric,
    Date,
    DateTime,
    Index,
)
from sqlalchemy.sql import func
from db.database import Base


class Conversation(Base):
    """
    Two-way call transcript: customer_transcript and agent_transcript columns,
    one row per turn.
    """
    __tablename__ = "conversation"

    id = Column(BigInteger, primary_key=True, index=True)
    call_id = Column(String(255), nullable=False, index=True)
    customer_phone = Column(String(50), index=True)
    customer_transcript = Column(Text)
    agent_transcript = Column(Text)
    speaker_id = Column(String(100))
    language = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_conversation_call_id", "call_id"),
        Index("idx_conversation_phone", "customer_phone"),
        Index("idx_conversation_created", "created_at"),
    )


class CustomerFeedbackData(Base):
    """
    Collected payment/feedback: each field from the JSON object stored as a column,
    keyed by customer_phone.
    """
    __tablename__ = "customer_feedback_data"

    id = Column(BigInteger, primary_key=True, index=True)
    customer_phone = Column(String(50), nullable=False, index=True)
    call_id = Column(String(255), index=True)
    
    # Identity & loan
    identity_confirmed = Column(String(50))
    loan_taken = Column(Boolean)
    last_month_payment = Column(String(255))
    
    # Payee
    payee = Column(String(50))
    payee_name = Column(String(255))
    payee_contact = Column(String(50))
    
    # Payment details
    payment_date = Column(String(20))
    payment_mode = Column(String(100))
    payment_reason = Column(String(100))
    payment_amount = Column(Numeric(15, 2))
    
    # Field executive (if applicable)
    field_executive_name = Column(String(255))
    field_executive_contact = Column(String(50))
    
    # Session / meta
    stage = Column(String(50))
    customer_name = Column(String(255))
    confirmed = Column(Boolean)
    category = Column(String(100))
    started_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_customer_feedback_phone", "customer_phone"),
        Index("idx_customer_feedback_call_id", "call_id"),
        Index("idx_customer_feedback_created", "created_at"),
    )


class CustomerData(Base):
    """
    Customer data from uploaded Excel sheets.
    """
    __tablename__ = "customer_data"

    id = Column(BigInteger, primary_key=True, index=True)
    agreement_no = Column(String(255))
    branch = Column(String(255))
    zone = Column(String(255))
    product = Column(String(255))
    bkt_grp_may = Column(String(100))
    bkt_grp_june = Column(String(100))
    ncm_name = Column(String(255))
    agency_code = Column(String(100))
    agency_name = Column(String(255))
    roll = Column(String(100))
    am_name = Column(String(255))
    rcm_name = Column(String(255))
    zcm_name = Column(String(255))
    customer_name = Column(String(255), nullable=False, index=True)
    contact_number = Column(String(50), nullable=False, index=True)
    emi = Column(Numeric(15, 2))
    state = Column(String(100))
    area = Column(String(255))
    dealer_name = Column(String(255))
    asset = Column(String(255))
    registration_no = Column(String(255))
    repo_status = Column(String(100))
    repo_intimation_date = Column(Date)
    settlement_done = Column(String(50))
    receipt_date = Column(Date)
    deposition_date = Column(Date)
    payment_amt = Column(Numeric(15, 2))
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_customer_data_contact_number", "contact_number"),
        Index("idx_customer_data_customer_name", "customer_name"),
        Index("idx_customer_data_uploaded_at", "uploaded_at"),
    )


class CallMetadata(Base):
    """
    Maps call_id to customer phone and name for agent personalization.
    """
    __tablename__ = "call_metadata"

    id = Column(BigInteger, primary_key=True, index=True)
    call_id = Column(String(255), unique=True, nullable=False, index=True)
    customer_phone = Column(String(50), index=True)
    customer_name = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_call_metadata_call_id", "call_id"),
        Index("idx_call_metadata_phone", "customer_phone"),
    )


class ActiveCallContext(Base):
    """
    Stores active call context for ongoing calls.
    Uses phone number as the primary lookup key for reliable agent-customer matching.
    """
    __tablename__ = "active_call_context"

    id = Column(BigInteger, primary_key=True, index=True)
    phone_number = Column(String(50), unique=True, nullable=False, index=True)
    customer_name = Column(String(255))
    customer_id = Column(BigInteger)
    call_status = Column(String(20), default='pending')  # pending/active/completed
    call_id = Column(String(255))  # Smartflo callSid when available
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_active_call_context_phone", "phone_number"),
        Index("idx_active_call_context_status", "call_status"),
        Index("idx_active_call_context_call_id", "call_id"),
    )
