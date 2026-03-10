"""
Database utility functions for ORM operations.
"""
from datetime import datetime, date
from sqlalchemy.orm import Session

from db.models import Customer, CallMetadata


def model_to_dict(model_instance) -> dict:
    """
    Convert a SQLAlchemy model instance to a plain dictionary.
    date / datetime values are converted to ISO-format strings.
    """
    result = {}
    for column in model_instance.__table__.columns:
        value = getattr(model_instance, column.name)
        if isinstance(value, date) and not isinstance(value, datetime):
            result[column.name] = value.isoformat() if value is not None else None
        elif isinstance(value, datetime):
            result[column.name] = value.isoformat() if value is not None else None
        else:
            result[column.name] = value
    return result
