import io
import json
import logging
import os
import uuid
from datetime import datetime, date
from typing import List, Optional, Any

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import CallMetadata, Customer
from api.smartflo_client import get_smartflo_client

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class CustomerResponse(BaseModel):
    agreement_no: str
    id: Optional[int] = None
    upload_id: Optional[str] = None
    branch: Optional[str] = None
    zone: Optional[str] = None
    product: Optional[str] = None
    bkt_grp_may: Optional[str] = None
    bkt_grp_june: Optional[str] = None
    ncm_name: Optional[str] = None
    agency_code: Optional[str] = None
    agency_name: Optional[str] = None
    roll: Optional[str] = None
    am_name: Optional[str] = None
    rcm_name: Optional[str] = None
    zcm_name: Optional[str] = None
    customer_name: str
    contact_number: str
    state: Optional[str] = None
    area: Optional[str] = None
    dealer_name: Optional[str] = None
    asset: Optional[str] = None
    registration_no: Optional[str] = None
    repo_status: Optional[str] = None
    repo_intimation_date: Optional[date] = None
    settlement_done: Optional[bool] = None
    receipt_date: Optional[date] = None
    deposition_date: Optional[date] = None
    uploaded_at: datetime
    updated_at: datetime
    # Joined from CallMetadata — defaults to "pending" when no call record exists
    call_status: Optional[str] = "pending"

    model_config = ConfigDict(from_attributes=True)


class CallTriggerRequest(BaseModel):
    agreement_no: str
    phone_number: str
    customer_name: str


class CallContextRequest(BaseModel):
    phone_number: str
    customer_name: str
    agreement_no: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/customers/upload", response_model=dict)
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """ORM-based Excel upload using db.merge() for upserts.

    Every file upload receives a unique upload_id so records can be
    grouped and tracked by batch.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="File must be Excel (.xlsx or .xls)")

    try:
        file_content = await file.read()
        df = pd.read_excel(io.BytesIO(file_content))
        df.columns = df.columns.str.strip()

        column_mapping = {
            "Agreement No": "agreement_no", "Branch": "branch", "Zone": "zone",
            "Product": "product", "BKT GRP- May": "bkt_grp_may", "BKT GRP - June": "bkt_grp_june",
            "NCM Name": "ncm_name", "Agecy Code": "agency_code", "Agency Name": "agency_name",
            "ROLL": "roll", "AM NAME": "am_name", "RCM NAME": "rcm_name", "ZCM NAME": "zcm_name",
            "Customer Name": "customer_name", "Contact Number": "contact_number",
            "State": "state", "Area": "area", "Dealer Name": "dealer_name",
            "Asset": "asset", "Registration no": "registration_no", "Repo Status": "repo_status",
            "Repo Intimation Date": "repo_intimation_date", "Settlement Done or not": "settlement_done",
            "Reciept Date": "receipt_date", "Deposition Date": "deposition_date",
        }
        df = df.rename(columns=column_mapping)
        df = df.fillna("")

        # One unique upload_id for every record from this file
        batch_upload_id = str(uuid.uuid4())

        processed = 0
        for _, row in df.iterrows():
            customer_obj = Customer(
                agreement_no=str(row["agreement_no"]).strip(),
                upload_id=batch_upload_id,
                branch=_to_str(row.get("branch")),
                zone=_to_str(row.get("zone")),
                product=_to_str(row.get("product")),
                bkt_grp_may=_to_bkt_str(row.get("bkt_grp_may")),
                bkt_grp_june=_to_bkt_str(row.get("bkt_grp_june")),
                ncm_name=_to_str(row.get("ncm_name")),
                agency_code=_to_str(row.get("agency_code")),
                agency_name=_to_str(row.get("agency_name")),
                roll=_to_str(row.get("roll")),
                am_name=_to_str(row.get("am_name")),
                rcm_name=_to_str(row.get("rcm_name")),
                zcm_name=_to_str(row.get("zcm_name")),
                customer_name=str(row["customer_name"]).strip(),
                contact_number=normalize_phone(str(row["contact_number"])),
                state=_to_str(row.get("state")),
                area=_to_str(row.get("area")),
                dealer_name=_to_str(row.get("dealer_name")),
                asset=_to_str(row.get("asset")),
                registration_no=_to_str(row.get("registration_no")),
                repo_status=_to_str(row.get("repo_status")),
                repo_intimation_date=_to_date(row.get("repo_intimation_date")),
                settlement_done=_to_bool(row.get("settlement_done")),
                receipt_date=_to_date(row.get("receipt_date")),
                deposition_date=_to_date(row.get("deposition_date")),
            )
            db.merge(customer_obj)
            processed += 1

        db.commit()
        return {
            "success": True,
            "message": f"Processed {processed} customers",
            "total": processed,
            "upload_id": batch_upload_id,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/customers", response_model=List[CustomerResponse])
async def list_customers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Return customers joined with their latest call_status from CallMetadata."""
    customers = (
        db.query(Customer)
        .order_by(Customer.uploaded_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    if not customers:
        return []

    agreement_nos = [c.agreement_no for c in customers]

    # Fetch the most-recently-updated call record per agreement_no
    latest_calls = (
        db.query(CallMetadata)
        .filter(CallMetadata.agreement_no.in_(agreement_nos))
        .order_by(CallMetadata.updated_at.desc())
        .all()
    )
    status_map: dict = {}
    for call in latest_calls:
        if call.agreement_no not in status_map:
            status_map[call.agreement_no] = call.call_status

    result = []
    for c in customers:
        data = {col.name: getattr(c, col.name) for col in c.__table__.columns}
        data["call_status"] = status_map.get(c.agreement_no, "pending")
        result.append(CustomerResponse.model_validate(data))
    return result


@router.get("/customers/{agreement_no}", response_model=CustomerResponse)
async def get_customer(agreement_no: str, db: Session = Depends(get_db)):
    customer = db.query(Customer).get(agreement_no)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    latest_call = (
        db.query(CallMetadata)
        .filter(CallMetadata.agreement_no == agreement_no)
        .order_by(CallMetadata.updated_at.desc())
        .first()
    )
    data = {col.name: getattr(customer, col.name) for col in customer.__table__.columns}
    data["call_status"] = latest_call.call_status if latest_call else "pending"
    return CustomerResponse.model_validate(data)


@router.get("/calls/status/{agreement_no}")
async def get_call_status(agreement_no: str, db: Session = Depends(get_db)):
    """Lightweight status-only endpoint for frontend polling on manual calls."""
    latest_call = (
        db.query(CallMetadata)
        .filter(CallMetadata.agreement_no == agreement_no)
        .order_by(CallMetadata.updated_at.desc())
        .first()
    )
    return {
        "agreement_no": agreement_no,
        "call_status": latest_call.call_status if latest_call else "pending",
    }


@router.post("/calls/context")
async def update_call_context(request: CallContextRequest, db: Session = Depends(get_db)):
    """
    Step 1 of 2 in the call flow.
    Creates/updates the CallMetadata record and dispatches the LiveKit agent.
    Status is set to 'pending' — it becomes 'active' only after SmartFlo triggers the call.
    """
    phone_normalized = normalize_phone(request.phone_number)
    room_name = f"call-{phone_normalized}"

    agreement_no = request.agreement_no
    if not agreement_no:
        cust = db.query(Customer).filter(Customer.contact_number == phone_normalized).first()
        if cust:
            agreement_no = cust.agreement_no

    # ORM upsert for CallMetadata
    call_meta = db.query(CallMetadata).get(room_name)
    if not call_meta:
        call_meta = CallMetadata(call_id=room_name, call_count=0)
        db.add(call_meta)

    call_meta.agreement_no = agreement_no
    call_meta.phone_number = request.phone_number
    call_meta.call_status = "pending"
    call_meta.call_count += 1
    call_meta.last_call_at = datetime.now()
    call_meta.updated_at = datetime.now()

    db.commit()

    # LiveKit Dispatch
    from livekit import api as livekit_api
    lk_url = os.getenv("LIVEKIT_URL", "").replace("wss://", "https://")
    metadata = json.dumps({
        "customer_phone": phone_normalized,
        "customer_name": request.customer_name,
        "agreement_no": agreement_no,
    })

    async with livekit_api.LiveKitAPI(
        url=lk_url,
        api_key=os.getenv("LIVEKIT_API_KEY", ""),
        api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
    ) as lk:
        try:
            await lk.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
        except Exception:
            pass

        await lk.room.create_room(livekit_api.CreateRoomRequest(
            name=room_name,
            empty_timeout=300,
            metadata=metadata,
            agents=[livekit_api.RoomAgentDispatch(
                agent_name="LTFS_SurveyAgent-Soma",
                metadata=metadata,
            )],
        ))

    return {"success": True, "room_name": room_name, "agreement_no": agreement_no}


@router.post("/calls/trigger")
async def trigger_call(request: CallTriggerRequest, db: Session = Depends(get_db)):
    """
    Step 2 of 2 in the call flow.
    Marks the call as 'active' and initiates the outbound call via SmartFlo.
    DB is always written here — even if SmartFlo fails.
    """
    phone_normalized = normalize_phone(request.phone_number)
    room_name = f"call-{phone_normalized}"

    # Ensure call_metadata exists and update to 'active'
    call_meta = db.query(CallMetadata).get(room_name)
    if not call_meta:
        call_meta = CallMetadata(
            call_id=room_name,
            agreement_no=request.agreement_no,
            phone_number=request.phone_number,
            call_count=1,
        )
        db.add(call_meta)

    call_meta.call_status = "calling"
    call_meta.updated_at = datetime.now()
    db.commit()

    smartflo_client = get_smartflo_client()
    call_result = await smartflo_client.initiate_call(
        to_number=request.phone_number,
        custom_params={"customer_name": request.customer_name, "agreement_no": request.agreement_no},
    )

    if not call_result["success"]:
        # SmartFlo failed — mark as missedcall (call was attempted but did not connect)
        call_meta.call_status = "missedcall"
        db.commit()
        raise HTTPException(status_code=500, detail="SmartFlo initiation failed")

    return {"success": True, "call_sid": call_result.get("call_sid")}


@router.get("/customers/download")
async def download_data(
    start: str = Query(default=""),
    end: str = Query(default=""),
    disposition: str = Query(default="All"),
    table: str = Query(default="customer_feedback"),
    db: Session = Depends(get_db),
):
    """
    Export table data as an Excel file.
    Supports filtering by date range and disposition.
    """
    from db.models import CustomerFeedback, Conversation

    try:
        if table == "customer_feedback":
            query = db.query(CustomerFeedback)
            if disposition and disposition != "All":
                query = query.filter(CustomerFeedback.disposition == disposition)
            if start:
                try:
                    start_dt = datetime.strptime(start, "%Y-%m-%d")
                    query = query.filter(CustomerFeedback.created_at >= start_dt)
                except ValueError:
                    pass
            if end:
                try:
                    end_dt = datetime.strptime(end, "%Y-%m-%d")
                    query = query.filter(CustomerFeedback.created_at <= end_dt)
                except ValueError:
                    pass
            rows = query.all()
            records = [
                {col.name: getattr(r, col.name) for col in r.__table__.columns}
                for r in rows
            ]
        elif table == "conversation":
            query = db.query(Conversation)
            if start:
                try:
                    start_dt = datetime.strptime(start, "%Y-%m-%d")
                    query = query.filter(Conversation.created_at >= start_dt)
                except ValueError:
                    pass
            if end:
                try:
                    end_dt = datetime.strptime(end, "%Y-%m-%d")
                    query = query.filter(Conversation.created_at <= end_dt)
                except ValueError:
                    pass
            rows = query.all()
            records = [
                {col.name: getattr(r, col.name) for col in r.__table__.columns}
                for r in rows
            ]
        else:
            # Default: export customer list
            rows = db.query(Customer).all()
            records = [
                {col.name: getattr(r, col.name) for col in r.__table__.columns}
                for r in rows
            ]

        df = pd.DataFrame(records) if records else pd.DataFrame()

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Data")
        output.seek(0)

        filename = f"{table}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error(f"Download error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Helpers ──────────────────────────────────────────────────────────────────

def normalize_phone(number: str) -> str:
    """Strip +, country code 91, spaces, dashes to get bare 10-digit number."""
    if not number:
        return number
    clean = str(number).strip().replace(" ", "").replace("-", "").lstrip("+")
    if clean.startswith("91") and len(clean) > 10:
        clean = clean[2:]
    return clean


def _to_str(val: Any) -> Optional[str]:
    if pd.isna(val) or str(val).strip().lower() in ("nan", ""):
        return None
    return str(val).strip()


def _to_bool(val: Any) -> Optional[bool]:
    if pd.isna(val) or val == "":
        return None
    s = str(val).strip().lower()
    if s in ("yes", "true", "1", "y", "done"):
        return True
    if s in ("no", "false", "0", "n"):
        return False
    return None


def _to_bkt_str(val: Any) -> Optional[str]:
    s = _to_str(val)
    if not s:
        return None
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else s
    except Exception:
        return s


def _to_date(val: Any) -> Optional[date]:
    if pd.isna(val) or val == "":
        return None
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.date()
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None
