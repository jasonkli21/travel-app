export function currentValue(candidate, key, fallback) {
  return Object.hasOwn(candidate.current, key) ? candidate.current[key] : fallback;
}

export function emptyCandidateDraft(candidate) {
  return {
    reservation_type: currentValue(candidate, "reservation_type", candidate.reservation_type),
    provider_name: currentValue(candidate, "provider_name", candidate.provider_name) ?? "",
    confirmation_code: currentValue(candidate, "confirmation_code", candidate.confirmation_code) ?? "",
    starts_at_date: currentValue(candidate, "starts_at_date", candidate.starts_at_date),
    starts_at_time: currentValue(candidate, "starts_at_time", candidate.starts_at_time),
    starts_at_timezone: currentValue(candidate, "starts_at_timezone", candidate.starts_at_timezone) ?? "",
    ends_at_date: currentValue(candidate, "ends_at_date", candidate.ends_at_date),
    ends_at_time: currentValue(candidate, "ends_at_time", candidate.ends_at_time),
    ends_at_timezone: currentValue(candidate, "ends_at_timezone", candidate.ends_at_timezone) ?? "",
    reservation_status: candidate.current.reservation_status ?? "",
    acknowledgedUncertainty: false,
    place_id: "",
    itinerary_item_id: "",
  };
}

export function normalizeReservationType(value) {
  if (value === "rail") return "train";
  if (value === "car") return "car_rental";
  if (["lodging", "flight", "train", "car_rental", "activity", "dining", "other"].includes(value)) {
    return value;
  }
  return null;
}

export function isDefinitiveUploadFailure(status) {
  return Number.isInteger(status) && status >= 400 && status < 500 && status !== 429;
}

export function createEntryError(entry, candidate, draft) {
  if (!entry.reservation_type || !entry.reservation_status || !entry.provider_name) {
    return "Choose a reservation status and type, and enter a provider for every reservation you create.";
  }
  if (candidate.uncertain_fields.length > 0 && !draft.acknowledgedUncertainty) {
    return `Review the uncertain fields for candidate ${entry.candidate_id.slice(-8)} and acknowledge any values you leave blank.`;
  }
  const start = [entry.starts_at_date, entry.starts_at_time, entry.starts_at_timezone];
  const end = [entry.ends_at_date, entry.ends_at_time, entry.ends_at_timezone];
  if (["flight", "train", "car_rental", "lodging"].includes(entry.reservation_type)
    && start.some((value) => !value)) {
    return "Flight, train, car rental, and lodging reservations need a complete start date, time, and timezone.";
  }
  if ((start.some(Boolean) && start.some((value) => !value))
    || (end.some(Boolean) && end.some((value) => !value))) {
    return "Each schedule needs its date, time, and timezone together, or must be left blank.";
  }
  return null;
}
