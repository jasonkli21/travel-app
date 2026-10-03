from typing import Literal

ItemType = Literal["activity", "food", "lodging", "transport", "flight", "note"]
ItemStatus = Literal["tentative", "planned", "booked", "completed", "cancelled"]
ReservationType = Literal["lodging", "flight", "train", "car_rental", "activity", "dining", "other"]
ReservationStatus = Literal["tentative", "confirmed", "cancelled"]
GeoapifyRouteMode = Literal["walk", "drive", "bicycle", "transit"]
ResearchFreshness = Literal["general", "current"]
ResearchState = Literal["pending", "running", "completed", "insufficient", "failed", "expired"]
