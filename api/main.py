from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from api.config import get_settings
from api.database import init_db, close_db
from api.redis_client import close_redis
from api.routes import functions, executions, health, resources
from api.middleware import RequestLoggingMiddleware, MetricsMiddleware, RateLimitMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Flux API...")
    await init_db()
    logger.info("Database initialized")
    logger.info(f"Flux API started on {settings.api_host}:{settings.api_port}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Flux API...")
    await close_db()
    await close_redis()
    logger.info("Flux API shutdown complete")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="A serverless function runtime platform",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure based on environment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom middleware
metrics_middleware = MetricsMiddleware(app)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(functions.router, prefix="/api/v1/functions", tags=["Functions"])
app.include_router(executions.router, prefix="/api/v1/executions", tags=["Executions"])
app.include_router(resources.router, prefix="/api/v1/resources", tags=["Resources"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs"
    }


@app.get("/metrics")
async def get_metrics():
    """Get application metrics."""
    return {
        "app": {
            "name": settings.app_name,
            "version": settings.app_version
        },
        "requests": metrics_middleware.get_metrics() if metrics_middleware else {}
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug
    )