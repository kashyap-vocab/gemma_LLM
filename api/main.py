"""
Main API server combining customer management API, Smartflo bridge,
and serves the built React frontend.
"""
import sys
from pathlib import Path

# Add parent directory to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "smart-flo"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
import uvicorn

# Import customer API router
from api.customer_api import router as customer_router

# Import Smartflo bridge WebSocket handler
from smartflow_bridge import smartflo_websocket_endpoint

# Create main app
app = FastAPI(title="LiveKit Customer Management API")

# CORS middleware - allow all origins for global access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include customer API routes
app.include_router(customer_router, prefix="/api", tags=["customers"])

# Add WebSocket endpoint for Smartflo bridge
app.add_websocket_route("/smartflo/stream", smartflo_websocket_endpoint)

# Serve React frontend build
frontend_build = project_root / "frontend" / "build"

if frontend_build.exists():
    # Serve static assets (JS, CSS, images) under /static
    static_dir = frontend_build / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve React frontend - SPA fallback to index.html."""
        file_path = frontend_build / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(frontend_build / "index.html"))
else:
    @app.get("/")
    async def root():
        return {
            "status": "ok",
            "service": "LiveKit Customer Management API",
            "note": "Frontend not built yet. Run: cd frontend && npm run build",
            "endpoints": {
                "customer_upload": "/api/customers/upload",
                "list_customers": "/api/customers",
                "call_context": "/api/calls/context",
                "trigger_call": "/api/calls/trigger",
                "smartflo_ws": "/smartflo/stream"
            }
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
