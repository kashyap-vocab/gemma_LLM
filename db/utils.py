"""
Database utility functions for ORM operations.
"""
from typing import Optional
from datetime import datetime, date
import pandas as pd
from sqlalchemy.orm import Session
from db.models import CustomerData, CallMetadata


def model_to_dict(model_instance, date_fields=None, datetime_fields=None):
    """
    Convert SQLAlchemy model instance to dictionary, converting dates/datetimes to strings.
    
    Args:
        model_instance: SQLAlchemy model instance
        date_fields: List of field names that are dates (optional, auto-detected)
        datetime_fields: List of field names that are datetimes (optional, auto-detected)
    
    Returns:
        Dictionary representation of the model
    """
    result = {}
    for column in model_instance.__table__.columns:
        value = getattr(model_instance, column.name)
        
        # Convert date/datetime to ISO string
        if isinstance(value, date) and not isinstance(value, datetime):
            result[column.name] = value.isoformat() if value else None
        elif isinstance(value, datetime):
            result[column.name] = value.isoformat() if value else None
        else:
            result[column.name] = value
    
    return result
