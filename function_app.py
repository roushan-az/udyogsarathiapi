import azure.functions as func
import nest_asyncio
from app.main import app as fastapi_app

# Bridges the FastAPI async loop with Azure Functions
nest_asyncio.apply()

# Azure Functions V4 Wrapper
app = func.AsgiFunctionApp(
    app=fastapi_app,
    http_auth_level=func.AuthLevel.ANONYMOUS
)