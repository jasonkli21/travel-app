from pydantic import BaseModel

from personal_travel.api.schemas.attachments import AttachmentResponse
from personal_travel.api.schemas.reservations import ReservationResponse
from personal_travel.api.schemas.trips import TripDetailResponse


class TravelModeResponse(BaseModel):
    trip: TripDetailResponse
    reservations: list[ReservationResponse]
    attachments: list[AttachmentResponse]
    attachments_available: bool
