"""Main application entry point for VBR Platform."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router, set_services
from app.core.config import settings
from app.db.database import init_db
from app.services.ai_drafter import AIDrafter
from app.services.hosttools import HostToolsClient
from app.services.ntfy import NtfyClient

# Paths — works both locally (backend/app/main.py → ../../frontend)
# and in Docker (/app/app/main.py → ../frontend)
_app_dir = Path(__file__).parent.parent  # backend/ or /app/
FRONTEND_DIR = _app_dir / "frontend"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = _app_dir.parent / "frontend"  # local dev: go up one more
STATIC_DIR = FRONTEND_DIR / "static"
TEMPLATES_DIR = FRONTEND_DIR / "templates"

# Logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global services
hosttools: HostToolsClient | None = None
ntfy: NtfyClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown."""
    global hosttools, ntfy

    logger.info("Starting VBR Platform...")

    # Init database
    await init_db()

    # Init Host Tools client
    if settings.hosttools_auth_token:
        hosttools = HostToolsClient(settings.hosttools_auth_token)
        logger.info("Host Tools API client initialized")
    else:
        logger.warning("HOSTTOOLS_AUTH_TOKEN not set — API calls will fail")

    # Init ntfy client
    ntfy = NtfyClient(settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token)
    if ntfy.configured:
        logger.info("ntfy notifications initialized (%s/%s)", settings.ntfy_url, settings.ntfy_topic)
    else:
        logger.warning("ntfy not configured — notifications disabled")

    # Init AI drafter
    ai_drafter = None
    if settings.gemini_api_key:
        ai_drafter = AIDrafter(settings.gemini_api_key)
        logger.info("AI Drafter initialized (model: gemini-2.0-flash)")
    else:
        logger.warning("GEMINI_API_KEY not set — AI drafts disabled")

    # Wire up services to routes
    set_services(hosttools, ntfy, ai_drafter)

    logger.info("VBR Platform started successfully")
    yield

    # Shutdown
    logger.info("Shutting down VBR Platform...")
    if hosttools:
        await hosttools.close()
    if ntfy:
        await ntfy.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="VBR Platform",
    description="Property management platform for 193 & 195 Vauxhall Bridge Road",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_router, prefix="/api")

# Static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Dashboard
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the owner app."""
    index_path = TEMPLATES_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>VBR Platform</h1><p>Frontend not built yet.</p>")


def main():
    """Run the application."""
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
