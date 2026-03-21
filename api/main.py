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

from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
import uvicorn

# Import customer API router
from api.customer_api import router as customer_router

# Import auto-dialer router
from api.auto_dialer import router as auto_dialer_router

# Import Smartflo bridge WebSocket handler
from smartflow_bridge import smartflo_websocket_endpoint
from api.feedback_backfill_scheduler import periodic_feedback_backfill

from db.database import engine, Base
from db.models import Customer, CallMetadata, Conversation, CustomerFeedback  # noqa: F401


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create all tables and sequences on startup (no-op if they already exist)
    Base.metadata.create_all(bind=engine)
    stop_event = asyncio.Event()
    backfill_task = asyncio.create_task(periodic_feedback_backfill(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await asyncio.gather(backfill_task, return_exceptions=True)


# Create main app
app = FastAPI(title="LiveKit Customer Management API", lifespan=lifespan)

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

# Include auto-dialer routes
app.include_router(auto_dialer_router, prefix="/api", tags=["auto-dialer"])

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
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        workers=5,
        log_level="info",
        access_log=False,
        limit_concurrency=50,
        backlog=100,
    )
