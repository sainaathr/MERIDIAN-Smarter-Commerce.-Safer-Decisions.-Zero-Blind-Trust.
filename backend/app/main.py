from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.health import router as health_router
from app.api.trust_routes import router as trust_router
from app.api.ai_routes import router as ai_router
from app.api.payment_routes import router as payment_router
from app.api.care_routes import router as care_router
from app.api.business_routes import router as business_router
from app.api.group_routes import router as group_router
from app.api.auth_routes import router as auth_router
from app.core.init_db import init_database

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database schemas and synthetic seed data on startup
    await init_database()
    yield

app = FastAPI(
    title="MERIDIAN — Universal Financial Trust Engine & Commerce Platform",
    description="Deterministic permission infrastructure and AI-native commerce coordinator for the PayPal AI Hackathon 2026.",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_URL,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health_router, prefix="/api")
app.include_router(trust_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(payment_router, prefix="/api")
app.include_router(care_router, prefix="/api/care", tags=["Assisted Commerce (CARE)"])
app.include_router(business_router, prefix="/api/business", tags=["Business Financial Autonomy (FLOW)"])
app.include_router(group_router, prefix="/api", tags=["Collective & Shared Purchasing (COMMON)"])
app.include_router(auth_router, prefix="/api", tags=["Authentication"])

@app.get("/")
async def root():
    return {
        "message": "MERIDIAN Platform API Online",
        "tagline": "Commerce without blind trust.",
        "docs": "/docs",
        "health": "/api/health",
        "engine": "Universal Trust Engine Kernel v1.0 active",
    }
