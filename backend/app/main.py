from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="Medcoolteste API",
    description="Telemedicine platform backend",
    version="0.1.0",
)

app.include_router(router)
