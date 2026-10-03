// Only the fields used by the bounded server projection invalidate research.
export function researchContextKey(trip, dayId) {
  const day = trip.days.find((candidate) => candidate.id === dayId);
  return JSON.stringify([
    trip.id, trip.start_date, trip.end_date, trip.timezone,
    day?.id, day?.date, day?.title,
    day?.items.map((item) => [
      item.id, item.sort_order, item.status, item.title, item.start_time, item.place?.name,
    ]),
  ]);
}
