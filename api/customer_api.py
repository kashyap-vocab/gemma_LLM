"""
FastAPI router for customer management and call triggering.
"""
import logging
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel
import pandas as pd
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import CustomerData, CallMetadata

logger = logging.getLogger(__name__)

router = APIRouter()


class CustomerResponse(BaseModel):
    id: int
    agreement_no: Optional[str]
    branch: Optional[str]
    zone: Optional[str]
    product: Optional[str]
    bkt_grp_may: Optional[str]
    bkt_grp_june: Optional[str]
    ncm_name: Optional[str]
    agency_code: Optional[str]
    agency_name: Optional[str]
    roll: Optional[str]
    am_name: Optional[str]
    rcm_name: Optional[str]
    zcm_name: Optional[str]
    customer_name: str
    contact_number: str
    emi: Optional[float]
    state: Optional[str]
    area: Optional[str]
    dealer_name: Optional[str]
    asset: Optional[str]
    registration_no: Optional[str]
    repo_status: Optional[str]
    repo_intimation_date: Optional[str]
    settlement_done: Optional[str]
    receipt_date: Optional[str]
    deposition_date: Optional[str]
    payment_amt: Optional[float]
    uploaded_at: str
    updated_at: str


class CallTriggerRequest(BaseModel):
    customer_id: int
    phone_number: str
    customer_name: str


@router.post("/customers/upload", response_model=dict)
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Upload Excel file and import customer data.
    """
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="File must be Excel (.xlsx or .xls)")

    try:
        # Read Excel file
        file_content = await file.read()
        from io import BytesIO
        df = pd.read_excel(BytesIO(file_content))
        
        # Normalize column names (handle spaces, case variations)
        df.columns = df.columns.str.strip()
        
        # Map Excel columns to database columns
        column_mapping = {
            'Agreement No': 'agreement_no',
            'Branch': 'branch',
            'Zone': 'zone',
            'Product': 'product',
            'BKT GRP- May': 'bkt_grp_may',
            'BKT GRP - June': 'bkt_grp_june',
            'NCM Name': 'ncm_name',
            'Agecy Code': 'agency_code',
            'Agency Name': 'agency_name',
            'ROLL': 'roll',
            'AM NAME': 'am_name',
            'RCM NAME': 'rcm_name',
            'ZCM NAME': 'zcm_name',
            'Customer Name': 'customer_name',
            'Contact Number': 'contact_number',
            'EMI': 'emi',
            'State': 'state',
            'Area': 'area',
            'Dealer Name': 'dealer_name',
            'Asset': 'asset',
            'Registration no': 'registration_no',
            'Repo Status': 'repo_status',
            'Repo Intimation Date': 'repo_intimation_date',
            'Settlement Done or not': 'settlement_done',
            'Reciept Date': 'receipt_date',
            'Deposition Date': 'deposition_date',
            'Payment Amt.': 'payment_amt',
        }
        
        # Rename columns
        df = df.rename(columns=column_mapping)
        
        # Ensure required columns exist
        if 'customer_name' not in df.columns or 'contact_number' not in df.columns:
            raise HTTPException(
                status_code=400,
                detail="Excel must contain 'Customer Name' and 'Contact Number' columns"
            )
        
        # Clean and prepare data
        df = df.fillna('')  # Replace NaN with empty string
        df['contact_number'] = df['contact_number'].astype(str).str.strip()
        df['customer_name'] = df['customer_name'].astype(str).str.strip()
        
        # Filter out rows with missing required fields
        df = df[(df['customer_name'] != '') & (df['contact_number'] != '')]
        
        if len(df) == 0:
            raise HTTPException(status_code=400, detail="No valid customer data found in Excel")
        
        # Insert into database using ORM
        inserted_count = 0
        updated_count = 0
        
        try:
            for _, row in df.iterrows():
                # Check if customer already exists (by contact_number)
                existing = db.query(CustomerData).filter(
                    CustomerData.contact_number == row['contact_number']
                ).first()
                
                customer_data = {
                    'agreement_no': row.get('agreement_no') or None,
                    'branch': row.get('branch') or None,
                    'zone': row.get('zone') or None,
                    'product': row.get('product') or None,
                    'bkt_grp_may': row.get('bkt_grp_may') or None,
                    'bkt_grp_june': row.get('bkt_grp_june') or None,
                    'ncm_name': row.get('ncm_name') or None,
                    'agency_code': row.get('agency_code') or None,
                    'agency_name': row.get('agency_name') or None,
                    'roll': row.get('roll') or None,
                    'am_name': row.get('am_name') or None,
                    'rcm_name': row.get('rcm_name') or None,
                    'zcm_name': row.get('zcm_name') or None,
                    'customer_name': row['customer_name'],
                    'contact_number': row['contact_number'],
                    'emi': _to_numeric(row.get('emi')),
                    'state': row.get('state') or None,
                    'area': row.get('area') or None,
                    'dealer_name': row.get('dealer_name') or None,
                    'asset': row.get('asset') or None,
                    'registration_no': row.get('registration_no') or None,
                    'repo_status': row.get('repo_status') or None,
                    'repo_intimation_date': _to_date(row.get('repo_intimation_date')),
                    'settlement_done': row.get('settlement_done') or None,
                    'receipt_date': _to_date(row.get('receipt_date')),
                    'deposition_date': _to_date(row.get('deposition_date')),
                    'payment_amt': _to_numeric(row.get('payment_amt')),
                }
                
                if existing:
                    # Update existing record
                    for key, value in customer_data.items():
                        setattr(existing, key, value)
                    updated_count += 1
                else:
                    # Insert new record
                    new_customer = CustomerData(**customer_data)
                    db.add(new_customer)
                    inserted_count += 1
            
            db.commit()
        except Exception as e:
            db.rollback()
            raise
        
        return {
            "success": True,
            "message": f"Uploaded {len(df)} rows: {inserted_count} inserted, {updated_count} updated",
            "inserted": inserted_count,
            "updated": updated_count,
            "total": len(df)
        }
    
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="Excel file is empty")
    except Exception as e:
        logger.error(f"Error uploading Excel: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing Excel: {str(e)}")


def _to_numeric(value):
    """Convert value to numeric, return None if invalid."""
    if pd.isna(value) or value == '' or value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_date(value):
    """Convert value to date, return None if invalid."""
    if pd.isna(value) or value == '' or value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        return value.date()
    try:
        return pd.to_datetime(value).date()
    except (ValueError, TypeError):
        return None


@router.get("/customers", response_model=List[CustomerResponse])
async def list_customers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    List all customers with pagination.
    """
    from db.utils import model_to_dict
    
    customers = db.query(CustomerData).order_by(
        CustomerData.uploaded_at.desc()
    ).offset(skip).limit(limit).all()
    
    return [model_to_dict(customer) for customer in customers]


@router.get("/customers/{customer_id}", response_model=CustomerResponse)
async def get_customer(customer_id: int, db: Session = Depends(get_db)):
    """
    Get customer by ID.
    """
    from db.utils import model_to_dict
    
    customer = db.query(CustomerData).filter(CustomerData.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    return model_to_dict(customer)


@router.post("/calls/trigger")
async def trigger_call(request: CallTriggerRequest, db: Session = Depends(get_db)):
    """
    Trigger a call to a customer via Smartflo.
    This endpoint would integrate with Smartflo API to initiate an outbound call.
    For now, it returns a success message. You'll need to integrate with Smartflo's API.
    """
    # TODO: Integrate with Smartflo API to initiate outbound call
    # Example: POST to Smartflo API with phone_number and customer_name
    
    logger.info(f"Triggering call to {request.customer_name} at {request.phone_number}")
    
    # Store call metadata for later use
    call_id = f"manual-{request.customer_id}-{datetime.now().timestamp()}"
    
    try:
        # Check if call_metadata already exists
        existing = db.query(CallMetadata).filter(CallMetadata.call_id == call_id).first()
        
        if existing:
            existing.customer_phone = request.phone_number
            existing.customer_name = request.customer_name
        else:
            call_metadata = CallMetadata(
                call_id=call_id,
                customer_phone=request.phone_number,
                customer_name=request.customer_name
            )
            db.add(call_metadata)
        
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not store call metadata: {e}")
    
    return {
        "success": True,
        "message": f"Call initiated to {request.customer_name} at {request.phone_number}",
        "customer_id": request.customer_id,
        "phone_number": request.phone_number,
        "customer_name": request.customer_name
    }


