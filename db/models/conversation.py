"""
Conversation ORM model.

Maps to: CONVERSATION_TABLE
One row per conversation turn (speaker utterance).

Links to both CallMetadata (via call_id) and Customer (via agreement_no)
so turns can be queried by call or by customer independently.
"""
from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db.database import Base


class Conversation(Base):
    __tablename__ = "conversation"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # FK to CallMetadata (nullable — stored even if call record is missing)
    call_id = Column(
        String(64),
        ForeignKey("call_metadata.call_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # FK to Customer (nullable — populated when agreement_no is available)
    agreement_no = Column(
        String(64),
        ForeignKey("customer_data.agreement_no", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Denormalised for fast lookup without joins
    customer_phone = Column(String(20))

    # Transcript content (one column per speaker per turn)
    customer_transcript = Column(Text)
    agent_transcript = Column(Text)

    # Metadata
    speaker_id = Column(String(20))
    language = Column(String(20))

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    call_record = relationship("CallMetadata", back_populates="conversations")
    customer = relationship("Customer", back_populates="conversations")

    __table_args__ = (
        Index("idx_conversation_call_id", "call_id"),
        Index("idx_conversation_agreement", "agreement_no"),
        Index("idx_conversation_phone", "customer_phone"),
        Index("idx_conversation_created", "created_at"),
    )
