from fastapi import APIRouter

from personal_travel.api.routes import (
    health,
    itinerary,
    location,
    places,
    reservations,
    saved_places,
    trips,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(trips.router, prefix="/v1")
api_router.include_router(itinerary.router, prefix="/v1")
api_router.include_router(places.router, prefix="/v1")
api_router.include_router(reservations.router, prefix="/v1")
api_router.include_router(saved_places.router, prefix="/v1")
api_router.include_router(location.router, prefix="/v1")
