"""
Smartflo API Client for initiating outbound calls.
Updated to support model-driven identifiers (agreement_no).
"""
import os
import logging
from typing import Optional, Dict, Any
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class SmartfloClient:
    # ... __init__ remains exactly the same ...
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        from_number: Optional[str] = None,
        agent_number: Optional[str] = None
    ):
        self.api_key = api_key or os.getenv("SMARTFLO_API_KEY")
        self.api_url = api_url or os.getenv("SMARTFLO_API_URL", "https://api-smartflo.tatateleservices.com/v1/click_to_call")
        self.from_number = from_number or os.getenv("SMARTFLO_FROM_NUMBER")
        self.agent_number = agent_number or os.getenv("SMARTFLO_AGENT_NUMBER")

        if not self.api_key:
            logger.warning("SMARTFLO_API_KEY not found in environment variables")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def initiate_call(
        self,
        to_number: str,
        from_number: Optional[str] = None,
        custom_params: Optional[Dict[str, Any]] = None,
        webhook_url: Optional[str] = None,
        agent_number: Optional[str] = None,
        call_timeout: Optional[int] = 300
    ) -> Dict[str, Any]:
        """
        Initiate an outbound call via Smartflo Click-to-Call API.
        """
        try:
            caller_id = from_number or self.from_number
            agent = agent_number or self.agent_number
            use_support_api = "click_to_call_support" in self.api_url

            if not caller_id:
                return {
                    "success": False,
                    "call_sid": None,
                    "status": "failed",
                    "message": "No caller_id configured",
                    "error": "SMARTFLO_FROM_NUMBER not set in environment"
                }

            if use_support_api:
                payload = {
                    "api_key": self.api_key,
                    "customer_number": to_number,
                    "async": 1,
                    "caller_id": caller_id,
                    "call_timeout": call_timeout
                }
                request_headers = {"Content-Type": "application/json"}
            else:
                if not agent:
                    return {
                        "success": False,
                        "call_sid": None,
                        "status": "failed",
                        "message": "No agent_number configured",
                        "error": "SMARTFLO_AGENT_NUMBER required"
                    }
                payload = {
                    "agent_number": agent,
                    "destination_number": to_number,
                    "caller_id": caller_id,
                    "async": 1,
                    "call_timeout": call_timeout
                }
                request_headers = self.headers

            # ── MODEL DEPENDENCY UPDATE ───────────────────────────────────────
            # Ensure agreement_no is passed as the custom_identifier so the 
            # webhook can link back to the correct Customer/CallMetadata record.
            if custom_params:
                # If custom_identifier is explicitly provided, use it. 
                # Otherwise, fallback to agreement_no.
                cid = custom_params.get("custom_identifier") or custom_params.get("agreement_no")
                if cid:
                    payload["custom_identifier"] = str(cid)
            # ──────────────────────────────────────────────────────────────────

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=request_headers
                )

                response.raise_for_status()
                data = response.json()

                call_sid = data.get("call_sid") or data.get("callSid") or data.get("sid") or data.get("id")
                status = data.get("status", "initiated")
                success = data.get("success", True)

                if not success:
                    error_msg = data.get("message", "Unknown error from Smartflo")
                    return {
                        "success": False,
                        "call_sid": None,
                        "status": "failed",
                        "message": error_msg,
                        "error": error_msg
                    }

                return {
                    "success": True,
                    "call_sid": call_sid,
                    "status": status,
                    "message": f"Call initiated to {to_number}",
                    "response_data": data
                }

        except Exception as e:
            logger.error(f"Smartflo initiation failed: {str(e)}", exc_info=True)
            return {"success": False, "status": "failed", "error": str(e)}

    # ... get_call_status and hangup_call remain exactly as they were ...
    async def get_call_status(self, call_sid: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.api_url}/calls/{call_sid}",
                    headers=self.headers
                )
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def hangup_call(self, call_sid: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.api_url}/calls/{call_sid}/hangup",
                    headers=self.headers
                )
                response.raise_for_status()
                return {"success": True, "message": "Call terminated"}
        except Exception as e:
            return {"success": False, "error": str(e)}

# Singleton instance logic remains the same
_smartflo_client = None

def get_smartflo_client() -> SmartfloClient:
    global _smartflo_client
    if _smartflo_client is None:
        _smartflo_client = SmartfloClient()
    return _smartflo_client