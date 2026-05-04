# app/api/v1/router.py
"""
API v1 master router.
All sub-routers are registered here with their prefixes.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, dashboard, documents, health

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(dashboard.router)