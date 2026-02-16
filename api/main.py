"""
Main API server combining customer management API and Smartflo bridge.
"""
import sys
from pathlib import Path

# Add parent directory to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "smart-flo"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Import customer API router
from api.customer_api import router as customer_router

# Import Smartflo bridge WebSocket handler
from smartflow_bridge import smartflo_websocket_endpoint

# Create main app
app = FastAPI(title="LiveKit Customer Management API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include customer API routes
app.include_router(customer_router, prefix="/api", tags=["customers"])

# Add WebSocket endpoint for Smartflo bridge
app.add_websocket_route("/smartflo/stream", smartflo_websocket_endpoint)

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "LiveKit Customer Management API",
        "version": "1.0",
        "endpoints": {
            "customer_upload": "/api/customers/upload",
            "list_customers": "/api/customers",
            "trigger_call": "/api/calls/trigger",
            "smartflo_ws": "/smartflo/stream"
        }
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
