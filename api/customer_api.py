"""
FastAPI router for customer management and call triggering.
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel
import pandas as pd
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import CustomerData, CallMetadata, ActiveCallContext
from api.smartflo_client import get_smartflo_client

logger = logging.getLogger(__name__)


def normalize_phone(number: str) -> str:
    """Strip +, country code 91, spaces, dashes to get bare 10-digit number."""
    if not number:
        return number
    clean = number.strip().replace(" ", "").replace("-", "")
    clean = clean.lstrip("+")
    if clean.startswith("91") and len(clean) > 10:
        clean = clean[2:]
    return clean


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


class CallContextRequest(BaseModel):
    phone_number: str
    customer_name: str
    customer_id: Optional[int] = None


@router.post("/customers/upload", response_model=dict)
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Upload Excel file and import customer data.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=400, detail="File must be Excel (.xlsx or .xls)"
        )

    try:
        # Read Excel file
        file_content = await file.read()
        from io import BytesIO

        df = pd.read_excel(BytesIO(file_content))

        # Normalize column names (handle spaces, case variations)
        df.columns = df.columns.str.strip()

        # Map Excel columns to database columns
        column_mapping = {
            "Agreement No": "agreement_no",
            "Branch": "branch",
            "Zone": "zone",
            "Product": "product",
            "BKT GRP- May": "bkt_grp_may",
            "BKT GRP - June": "bkt_grp_june",
            "NCM Name": "ncm_name",
            "Agecy Code": "agency_code",
            "Agency Name": "agency_name",
            "ROLL": "roll",
            "AM NAME": "am_name",
            "RCM NAME": "rcm_name",
            "ZCM NAME": "zcm_name",
            "Customer Name": "customer_name",
            "Contact Number": "contact_number",
            "EMI": "emi",
            "State": "state",
            "Area": "area",
            "Dealer Name": "dealer_name",
            "Asset": "asset",
            "Registration no": "registration_no",
            "Repo Status": "repo_status",
            "Repo Intimation Date": "repo_intimation_date",
            "Settlement Done or not": "settlement_done",
            "Reciept Date": "receipt_date",
            "Deposition Date": "deposition_date",
            "Payment Amt.": "payment_amt",
        }

        # Rename columns
        df = df.rename(columns=column_mapping)

        # Ensure required columns exist
        if "customer_name" not in df.columns or "contact_number" not in df.columns:
            raise HTTPException(
                status_code=400,
                detail="Excel must contain 'Customer Name' and 'Contact Number' columns",
            )

        # Clean and prepare data
        df = df.fillna("")  # Replace NaN with empty string
        df["contact_number"] = df["contact_number"].astype(str).str.strip()
        df["customer_name"] = df["customer_name"].astype(str).str.strip()

        # Filter out rows with missing required fields
        df = df[(df["customer_name"] != "") & (df["contact_number"] != "")]

        if len(df) == 0:
            raise HTTPException(
                status_code=400, detail="No valid customer data found in Excel"
            )

        # Use raw SQL with explicit ::text casts to avoid SQLAlchemy ORM executemany
        # type-inference bugs where psycopg2 misidentifies string columns as integer.
        from sqlalchemy import text as sa_text

        UPSERT_SQL = sa_text("""
            INSERT INTO customer_data (
                agreement_no, branch, zone, product,
                bkt_grp_may, bkt_grp_june,
                ncm_name, agency_code, agency_name, roll,
                am_name, rcm_name, zcm_name,
                customer_name, contact_number,
                emi, state, area, dealer_name, asset,
                registration_no, repo_status, repo_intimation_date,
                settlement_done, receipt_date, deposition_date, payment_amt
            ) VALUES (
                cast(:agreement_no as varchar), cast(:branch as varchar),
                cast(:zone as varchar), cast(:product as varchar),
                cast(:bkt_grp_may as varchar), cast(:bkt_grp_june as varchar),
                cast(:ncm_name as varchar), cast(:agency_code as varchar),
                cast(:agency_name as varchar), cast(:roll as varchar),
                cast(:am_name as varchar), cast(:rcm_name as varchar),
                cast(:zcm_name as varchar),
                cast(:customer_name as varchar), cast(:contact_number as varchar),
                :emi,
                cast(:state as varchar), cast(:area as varchar),
                cast(:dealer_name as varchar), cast(:asset as varchar),
                cast(:registration_no as varchar), cast(:repo_status as varchar),
                :repo_intimation_date,
                cast(:settlement_done as varchar), :receipt_date,
                :deposition_date, :payment_amt
            )
            ON CONFLICT (contact_number) DO UPDATE SET
                agreement_no         = EXCLUDED.agreement_no,
                branch               = EXCLUDED.branch,
                zone                 = EXCLUDED.zone,
                product              = EXCLUDED.product,
                bkt_grp_may          = EXCLUDED.bkt_grp_may,
                bkt_grp_june         = EXCLUDED.bkt_grp_june,
                ncm_name             = EXCLUDED.ncm_name,
                agency_code          = EXCLUDED.agency_code,
                agency_name          = EXCLUDED.agency_name,
                roll                 = EXCLUDED.roll,
                am_name              = EXCLUDED.am_name,
                rcm_name             = EXCLUDED.rcm_name,
                zcm_name             = EXCLUDED.zcm_name,
                customer_name        = EXCLUDED.customer_name,
                emi                  = EXCLUDED.emi,
                state                = EXCLUDED.state,
                area                 = EXCLUDED.area,
                dealer_name          = EXCLUDED.dealer_name,
                asset                = EXCLUDED.asset,
                registration_no      = EXCLUDED.registration_no,
                repo_status          = EXCLUDED.repo_status,
                repo_intimation_date = EXCLUDED.repo_intimation_date,
                settlement_done      = EXCLUDED.settlement_done,
                receipt_date         = EXCLUDED.receipt_date,
                deposition_date      = EXCLUDED.deposition_date,
                payment_amt          = EXCLUDED.payment_amt,
                updated_at           = now()
            RETURNING (xmax = 0) AS was_inserted
        """)

        inserted_count = 0
        updated_count = 0

        try:
            for _, row in df.iterrows():
                params = {
                    "agreement_no":          _to_str(row.get("agreement_no")),
                    "branch":                _to_str(row.get("branch")),
                    "zone":                  _to_str(row.get("zone")),
                    "product":               _to_str(row.get("product")),
                    "bkt_grp_may":           _to_bkt_str(row.get("bkt_grp_may")),
                    "bkt_grp_june":          _to_bkt_str(row.get("bkt_grp_june")),
                    "ncm_name":              _to_str(row.get("ncm_name")),
                    "agency_code":           _to_str(row.get("agency_code")),
                    "agency_name":           _to_str(row.get("agency_name")),
                    "roll":                  _to_str(row.get("roll")),
                    "am_name":               _to_str(row.get("am_name")),
                    "rcm_name":              _to_str(row.get("rcm_name")),
                    "zcm_name":              _to_str(row.get("zcm_name")),
                    "customer_name":         str(row["customer_name"]).strip(),
                    "contact_number":        str(row["contact_number"]).strip(),
                    "emi":                   _to_numeric(row.get("emi")),
                    "state":                 _to_str(row.get("state")),
                    "area":                  _to_str(row.get("area")),
                    "dealer_name":           _to_str(row.get("dealer_name")),
                    "asset":                 _to_str(row.get("asset")),
                    "registration_no":       _to_str(row.get("registration_no")),
                    "repo_status":           _to_str(row.get("repo_status")),
                    "repo_intimation_date":  _to_date(row.get("repo_intimation_date")),
                    "settlement_done":       _to_str(row.get("settlement_done")),
                    "receipt_date":          _to_date(row.get("receipt_date")),
                    "deposition_date":       _to_date(row.get("deposition_date")),
                    "payment_amt":           _to_numeric(row.get("payment_amt")),
                }

                result = db.execute(UPSERT_SQL, params)
                was_inserted = result.scalar()
                if was_inserted:
                    inserted_count += 1
                else:
                    updated_count += 1

            db.commit()
        except Exception as e:
            db.rollback()
            raise

        return {
            "success": True,
            "message": f"Uploaded {len(df)} rows: {inserted_count} inserted, {updated_count} updated",
            "inserted": inserted_count,
            "updated": updated_count,
            "total": len(df),
        }

    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="Excel file is empty")
    except Exception as e:
        logger.error(f"Error uploading Excel: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing Excel: {str(e)}")


def _to_str(value):
    """Convert value to a stripped string, return None if blank/NaN."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s if s and s.lower() != "nan" else None


def _to_numeric(value):
    """Convert value to numeric, return None if invalid."""
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value):
    """Convert value to int if possible, otherwise return None."""
    if pd.isna(value) or value == "" or value is None:
        return None
    try:
        v = float(value)
        return int(v)
    except (ValueError, TypeError):
        return None


def _to_bkt_str(value):
    """Convert bucket-group value to a clean string for VARCHAR storage.

    Numeric values like 1.0 are stored as '1'; text codes like 'X-FC' are
    stored as-is. Returns None for blank/NaN values.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if s == "" or s.lower() == "nan":
        return None
    # Clean up float representation: '1.0' -> '1'
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
        return s
    except (ValueError, TypeError):
        return s


def _to_date(value):
    """Convert value to date, return None if invalid."""
    if pd.isna(value) or value == "" or value is None:
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
async def list_customers(
    skip: int = 0, limit: int = 100, db: Session = Depends(get_db)
):
    """
    List all customers with pagination.
    """
    from db.utils import model_to_dict

    customers = (
        db.query(CustomerData)
        .order_by(CustomerData.uploaded_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

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
    Route 2: Trigger the SmartFlo outbound call to connect customer to the agent.
    Call this AFTER /api/calls/context so the agent is already in the room.

    Flow:
    1. Initiate SmartFlo click-to-call to customer's phone
    2. SmartFlo calls customer → customer answers → WebSocket connects to bridge
    3. Bridge joins the pre-created LiveKit room 'call-{phone_number}'
    4. Customer audio flows to agent (already waiting in room)
    """
    logger.info(
        f"[Route 2] Triggering SmartFlo call to {request.customer_name} at {request.phone_number}"
    )

    try:
        # Update call context status to 'calling'
        existing_context = (
            db.query(ActiveCallContext)
            .filter(ActiveCallContext.phone_number == request.phone_number)
            .first()
        )

        if existing_context:
            existing_context.call_status = "calling"
            existing_context.updated_at = datetime.now()
        else:
            # If Route 1 wasn't called first, create context now
            logger.warning(
                f"No existing context for {request.phone_number} - Route 1 may not have been called"
            )
            existing_context = ActiveCallContext(
                phone_number=request.phone_number,
                customer_name=request.customer_name,
                customer_id=request.customer_id,
                call_status="calling",
                call_id=f"call-{request.phone_number}",
            )
            db.add(existing_context)

        db.commit()

        # Initiate call via SmartFlo API
        smartflo_client = get_smartflo_client()
        call_result = await smartflo_client.initiate_call(
            to_number=request.phone_number,
            custom_params={
                "customer_name": request.customer_name,
                "customer_id": request.customer_id,
            },
        )

        if not call_result["success"]:
            logger.error(f"SmartFlo call failed: {call_result.get('error')}")
            existing_context.call_status = "failed"
            db.commit()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initiate call: {call_result.get('message', 'Unknown error')}",
            )

        call_sid = call_result.get("call_sid")
        logger.info(f"SmartFlo call initiated. call_sid={call_sid}")

        return {
            "success": True,
            "message": f"Call initiated to {request.customer_name}",
            "customer_id": request.customer_id,
            "phone_number": request.phone_number,
            "customer_name": request.customer_name,
            "call_sid": call_sid,
            "call_status": call_result.get("status"),
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error triggering call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error triggering call: {str(e)}")


@router.post("/calls/context")
async def update_call_context(
    request: CallContextRequest, db: Session = Depends(get_db)
):
    """
    Route 1: Store customer name and create LiveKit room with agent dispatch.
    Call this BEFORE triggering the SmartFlo call so the agent is ready.

    Flow:
    1. Store customer context (name + phone) in DB
    2. Create a LiveKit room named 'call-{phone_number}'
    3. Dispatch the agent to that room with customer metadata
    """
    logger.info(
        f"[Route 1] Setting up agent for {request.customer_name} ({request.phone_number})"
    )

    try:
        # Step 1: Store/update call context in DB
        existing_context = (
            db.query(ActiveCallContext)
            .filter(ActiveCallContext.phone_number == request.phone_number)
            .first()
        )

        phone_normalized = normalize_phone(request.phone_number)
        room_name = f"call-{phone_normalized}"
        logger.info(
            f"[Route 1] Normalized phone: {request.phone_number} → {phone_normalized}, room: {room_name}"
        )

        if existing_context:
            existing_context.customer_name = request.customer_name
            if request.customer_id is not None:
                existing_context.customer_id = request.customer_id
            existing_context.call_status = "agent_ready"
            existing_context.call_id = room_name
            existing_context.updated_at = datetime.now()
            logger.info(f"Updated call context for {request.phone_number}")
        else:
            new_context = ActiveCallContext(
                phone_number=request.phone_number,
                customer_name=request.customer_name,
                customer_id=request.customer_id,
                call_status="agent_ready",
                call_id=room_name,
            )
            db.add(new_context)
            logger.info(f"Created new call context for {request.phone_number}")

        # Also store in call_metadata for agent DB lookup
        existing_metadata = (
            db.query(CallMetadata).filter(CallMetadata.call_id == room_name).first()
        )
        if existing_metadata:
            existing_metadata.customer_phone = request.phone_number
            existing_metadata.customer_name = request.customer_name
        else:
            db.add(
                CallMetadata(
                    call_id=room_name,
                    customer_phone=request.phone_number,
                    customer_name=request.customer_name,
                )
            )

        db.commit()

        # Step 2: Create LiveKit room and dispatch agent
        from livekit import api as livekit_api

        lk_url = os.getenv("LIVEKIT_URL", "")
        lk_api_key = os.getenv("LIVEKIT_API_KEY", "")
        lk_api_secret = os.getenv("LIVEKIT_API_SECRET", "")

        # Convert wss:// to https:// for API calls
        api_url = lk_url.replace("wss://", "https://")

        room_metadata = json.dumps(
            {
                "customer_phone": phone_normalized,
                "customer_name": request.customer_name,
                "customer_id": request.customer_id,
            }
        )

        async with livekit_api.LiveKitAPI(
            url=api_url,
            api_key=lk_api_key,
            api_secret=lk_api_secret,
        ) as lk:
            # Delete existing room first to avoid stale agent issue
            # (create_room is idempotent and won't re-dispatch agent to existing room)
            try:
                await lk.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
                logger.info(f"Deleted existing room '{room_name}' before re-creating")
            except Exception:
                pass  # Room didn't exist, that's fine

            # Create fresh room with agent dispatch
            await lk.room.create_room(
                livekit_api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=300,  # 5 min timeout if empty
                    metadata=room_metadata,
                    agents=[
                        livekit_api.RoomAgentDispatch(
                            agent_name="LTFS_SurveyAgent-Soma",
                            metadata=room_metadata,
                        )
                    ],
                )
            )
            logger.info(f"Created LiveKit room '{room_name}' and dispatched agent")

        return {
            "success": True,
            "message": f"Agent dispatched for {request.customer_name}",
            "phone_number": request.phone_number,
            "customer_name": request.customer_name,
            "customer_id": request.customer_id,
            "room_name": room_name,
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error in Route 1 (context + dispatch): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error setting up agent: {str(e)}")
