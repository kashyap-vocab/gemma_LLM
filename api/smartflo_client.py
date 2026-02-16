"""
Smartflo API Client for initiating outbound calls.
"""
import os
import logging
from typing import Optional, Dict, Any
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class SmartfloClient:
    """
    Client for interacting with Smartflo API to initiate and manage calls.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        from_number: Optional[str] = None,
        agent_number: Optional[str] = None
    ):
        """
        Initialize Smartflo API client.

        Args:
            api_key: Smartflo API key (defaults to env SMARTFLO_API_KEY)
            api_url: Smartflo API base URL (defaults to env SMARTFLO_API_URL)
            from_number: Default outbound caller ID (defaults to env SMARTFLO_FROM_NUMBER)
            agent_number: Smartflo agent ID (defaults to env SMARTFLO_AGENT_NUMBER)
        """
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

        Args:
            to_number: Destination phone number (customer number)
            from_number: Caller ID shown to the customer (defaults to configured from_number)
            custom_params: Additional parameters to pass to Smartflo
            webhook_url: Webhook URL for call events
            agent_number: Smartflo agent ID (defaults to configured agent_number)
            call_timeout: Call duration limit in seconds (default: 300)

        Returns:
            dict: {
                "success": bool,
                "call_sid": str,
                "status": str,
                "message": str,
                "error": str (if failed)
            }
        """
        try:
            caller_id = from_number or self.from_number
            agent = agent_number or self.agent_number

            # Check if using click_to_call_support endpoint (api_key in body)
            # or regular click_to_call endpoint (Bearer token in header)
            use_support_api = "click_to_call_support" in self.api_url

            # Validate required parameters based on API type
            if not caller_id:
                return {
                    "success": False,
                    "call_sid": None,
                    "status": "failed",
                    "message": "No caller_id configured",
                    "error": "SMARTFLO_FROM_NUMBER not set in environment"
                }

            if use_support_api:
                # Click-to-Call Support API: api_key in body, no agent_number
                payload = {
                    "api_key": self.api_key,
                    "customer_number": to_number,
                    "async": 1,
                    "caller_id": caller_id,
                    "call_timeout": call_timeout
                }
                # Don't use Authorization header for support API
                request_headers = {"Content-Type": "application/json"}
                logger.info(f"Using Click-to-Call Support API: customer={to_number}, caller_id={caller_id}")
            else:
                # Regular Click-to-Call API: Bearer token in header, requires agent_number
                if not agent:
                    return {
                        "success": False,
                        "call_sid": None,
                        "status": "failed",
                        "message": "No agent_number configured",
                        "error": "SMARTFLO_AGENT_NUMBER required for regular click_to_call endpoint"
                    }
                payload = {
                    "agent_number": agent,
                    "destination_number": to_number,
                    "caller_id": caller_id,
                    "async": 1,
                    "call_timeout": call_timeout
                }
                request_headers = self.headers
                logger.info(f"Using regular Click-to-Call API: agent={agent}, destination={to_number}, caller_id={caller_id}")

            # Add custom parameters if provided
            if custom_params:
                if "custom_identifier" in custom_params:
                    payload["custom_identifier"] = custom_params["custom_identifier"]

            logger.debug(f"Smartflo payload: {payload}")

            # Make API request to Smartflo Click-to-Call endpoint
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=request_headers
                )

                response.raise_for_status()
                data = response.json()

                logger.info(f"Smartflo API response: {data}")

                # Extract call information from response
                # Smartflo may return different field names, check common ones
                call_sid = data.get("call_sid") or data.get("callSid") or data.get("sid") or data.get("id")
                status = data.get("status", "initiated")
                success = data.get("success", True)

                if not success:
                    error_msg = data.get("message", "Unknown error from Smartflo")
                    logger.error(f"Smartflo API returned failure: {error_msg}")
                    return {
                        "success": False,
                        "call_sid": None,
                        "status": "failed",
                        "message": error_msg,
                        "error": error_msg
                    }

                logger.info(f"Smartflo call initiated successfully. Call SID: {call_sid}")

                return {
                    "success": True,
                    "call_sid": call_sid,
                    "status": status,
                    "message": f"Call initiated to {to_number}",
                    "response_data": data
                }

        except httpx.HTTPStatusError as e:
            error_msg = f"Smartflo API error: {e.response.status_code} - {e.response.text}"
            logger.error(error_msg)
            return {
                "success": False,
                "call_sid": None,
                "status": "failed",
                "message": "Failed to initiate call",
                "error": error_msg
            }

        except httpx.RequestError as e:
            error_msg = f"Network error calling Smartflo API: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "call_sid": None,
                "status": "failed",
                "message": "Network error",
                "error": error_msg
            }

        except Exception as e:
            error_msg = f"Unexpected error initiating call: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "call_sid": None,
                "status": "failed",
                "message": "Unexpected error",
                "error": error_msg
            }

    async def get_call_status(self, call_sid: str) -> Dict[str, Any]:
        """
        Get the status of a call by its SID.

        Args:
            call_sid: Smartflo call SID

        Returns:
            dict: Call status information
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.api_url}/calls/{call_sid}",
                    headers=self.headers
                )
                response.raise_for_status()
                return {
                    "success": True,
                    "data": response.json()
                }

        except Exception as e:
            logger.error(f"Error fetching call status: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    async def hangup_call(self, call_sid: str) -> Dict[str, Any]:
        """
        Terminate an active call.

        Args:
            call_sid: Smartflo call SID

        Returns:
            dict: Hangup status
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.api_url}/calls/{call_sid}/hangup",
                    headers=self.headers
                )
                response.raise_for_status()
                return {
                    "success": True,
                    "message": "Call terminated"
                }

        except Exception as e:
            logger.error(f"Error hanging up call: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
_smartflo_client = None


def get_smartflo_client() -> SmartfloClient:
    """
    Get or create a singleton Smartflo client instance.

    Returns:
        SmartfloClient: Configured Smartflo client
    """
    global _smartflo_client
    if _smartflo_client is None:
        _smartflo_client = SmartfloClient()
    return _smartflo_client
