export function uniqueComparisonPlaces(trip, savedPlaces, reservations) {
  const places = new Map();
  const addLocatedPlace = (place) => {
    if (place?.latitude !== null && place?.latitude !== undefined
      && place?.longitude !== null && place?.longitude !== undefined) {
      places.set(place.id, place);
    }
  };
  for (const day of trip.days) {
    for (const item of day.items) addLocatedPlace(item.place);
  }
  for (const saved of savedPlaces) addLocatedPlace(saved.place);
  for (const reservation of reservations) {
    if (reservation.status !== "cancelled") addLocatedPlace(reservation.place);
  }
  return [...places.values()].sort((left, right) => left.name.localeCompare(right.name));
}
