import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.admin_financial import router as admin_financial_router
from app.api.auth import router as auth_router
from app.api.chat_ws import router as chat_ws_router
from app.api.patients import router as patients_router
from app.api.professionals import router as professionals_router
from app.api.routes import router
from app.api.specialties import router as specialties_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings

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
app.include_router(admin_financial_router)
app.include_router(specialties_router)
app.include_router(webhooks_router)
app.include_router(chat_ws_router)

# Serve generated medical-document PDFs via a static-files mount.
# The directory is created on first use; we ensure it exists at startup so
# FastAPI/Starlette can mount it without errors.
_documents_dir = os.path.abspath(settings.DOCUMENTS_STORAGE_PATH)
os.makedirs(_documents_dir, exist_ok=True)
app.mount(settings.DOCUMENTS_BASE_URL, StaticFiles(directory=_documents_dir), name="documents")
