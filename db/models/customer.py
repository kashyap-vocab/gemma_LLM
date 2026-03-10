"""
Customer ORM model.

Maps to: CUSTOMER_TABLE
Primary key: agreement_no (business key from the loan agreement)
"""
from sqlalchemy import Column, BigInteger, String, Boolean, Date, DateTime, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db.database import Base


class Customer(Base):
    __tablename__ = "customer"

    # Business PK — agreement number from LTFS loan system
    agreement_no = Column(String(64), primary_key=True, nullable=False)

    # Auto-incrementing numeric identifier (1, 2, 3, …)
    # The DEFAULT is set at the DB level by init_db / lifespan startup — not in the
    # ORM model — so that CREATE TABLE succeeds even before the sequence exists.
    id = Column(BigInteger, nullable=True)

    # Upload batch identifier — shared by all records from the same Excel upload
    upload_id = Column(String(64), nullable=True)

    # Organisational hierarchy
    branch = Column(String(100))
    zone = Column(String(100))
    product = Column(String(100))
    bkt_grp_may = Column(String(50))
    bkt_grp_june = Column(String(50))
    ncm_name = Column(String(100))
    agency_code = Column(String(50))
    agency_name = Column(String(150))
    roll = Column(String(50))
    am_name = Column(String(100))
    rcm_name = Column(String(100))
    zcm_name = Column(String(100))

    # Customer identity
    customer_name = Column(String(150), nullable=False)
    contact_number = Column(String(20), nullable=False)
    state = Column(String(50))
    area = Column(String(100))

    # Asset details
    dealer_name = Column(String(150))
    asset = Column(String(100))
    registration_no = Column(String(50))

    # Repo / settlement
    repo_status = Column(String(30))
    repo_intimation_date = Column(Date)
    settlement_done = Column(Boolean)
    receipt_date = Column(Date)
    deposition_date = Column(Date)

    # Audit timestamps
    uploaded_at = Column(
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
    call_records = relationship("CallMetadata", back_populates="customer")
    conversations = relationship("Conversation", back_populates="customer")
    feedbacks = relationship("CustomerFeedback", back_populates="customer")

    __table_args__ = (
        Index("idx_customer_contact_number", "contact_number"),
        Index("idx_customer_name", "customer_name"),
        Index("idx_customer_uploaded_at", "uploaded_at"),
        Index("idx_customer_upload_id", "upload_id"),
    )
