import azure.functions as func
import nest_asyncio
from fastapi import FastAPI
from app.api.v1.router import api_router

nest_asyncio.apply()
app = FastAPI(title="Udyog Sarathi API")
app.include_router(api_router)

# The Azure Function wrapper
app = func.AsgiFunctionApp(app=app, http_auth_level=func.AuthLevel.ANONYMOUS)