from fastapi import APIRouter

from personal_travel.api.routes import health, itinerary, places, trips

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(trips.router, prefix="/v1")
api_router.include_router(itinerary.router, prefix="/v1")
api_router.include_router(places.router, prefix="/v1")
