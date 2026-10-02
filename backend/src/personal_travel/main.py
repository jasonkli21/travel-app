from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from personal_travel.api.router import api_router
from personal_travel.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Personal Travel API",
    version="0.1.0",
    description="Authoritative travel-domain API for the personal travel application.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
