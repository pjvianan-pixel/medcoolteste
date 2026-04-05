from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.patients import router as patients_router
from app.api.professionals import router as professionals_router
from app.api.routes import router
from app.api.specialties import router as specialties_router
from app.api.webhooks import router as webhooks_router

app = FastAPI(
    title="Medcoolteste API",
    description="Telemedicine platform backend",
    version="0.1.0",
)

app.include_router(router)
app.include_router(auth_router)
app.include_router(patients_router)
app.include_router(professionals_router)
app.include_router(admin_router)
app.include_router(specialties_router)
app.include_router(webhooks_router)
