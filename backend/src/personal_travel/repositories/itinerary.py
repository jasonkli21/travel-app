from sqlalchemy.orm import Session

from personal_travel.models.itinerary import ItineraryItem


class SqlAlchemyItineraryItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, item: ItineraryItem) -> ItineraryItem:
        self._session.add(item)
        return item

    def delete(self, item: ItineraryItem) -> None:
        self._session.delete(item)
