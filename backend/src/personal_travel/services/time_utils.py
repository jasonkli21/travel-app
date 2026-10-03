from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_travel.services.errors import DomainError


def get_zoneinfo(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise DomainError(
            "invalid_timezone",
            "timezone must be a valid IANA timezone name.",
            details={"timezone": value},
        ) from exc


def parse_local_time(value: str | None, *, field_name: str) -> time | None:
    if value is None:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise DomainError(
            "invalid_local_time",
            f"{field_name} must use HH:MM local time.",
            details={"field": field_name},
        ) from exc


def resolve_local_datetime(
    day_date: date, local_time: time, zone: ZoneInfo, *, field_name: str
) -> datetime:
    naive = datetime.combine(day_date, local_time)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip == naive:
            candidates.append(candidate)

    if not candidates:
        raise DomainError(
            "invalid_local_time",
            f"{field_name} falls in a daylight-saving-time gap for the trip timezone.",
            details={"field": field_name, "date": day_date.isoformat()},
        )
    if len(candidates) == 2 and candidates[0].utcoffset() != candidates[1].utcoffset():
        raise DomainError(
            "ambiguous_local_time",
            f"{field_name} is ambiguous because of a daylight-saving-time transition.",
            details={"field": field_name, "date": day_date.isoformat()},
        )
    return candidates[0]


def local_datetime_for_item(
    day_date: date,
    start_value: str | None,
    end_value: str | None,
    timezone_name: str,
) -> tuple[datetime | None, datetime | None]:
    zone = get_zoneinfo(timezone_name)
    start_time = parse_local_time(start_value, field_name="start_time")
    end_time = parse_local_time(end_value, field_name="end_time")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise DomainError(
            "invalid_time_range",
            "start_time must be earlier than or equal to end_time on the same trip day.",
        )
    start = (
        resolve_local_datetime(day_date, start_time, zone, field_name="start_time")
        if start_time is not None
        else None
    )
    end = (
        resolve_local_datetime(day_date, end_time, zone, field_name="end_time")
        if end_time is not None
        else None
    )
    return start, end


def as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_time_string(value: datetime | None, timezone_name: str) -> str | None:
    if value is None:
        return None
    zone = get_zoneinfo(timezone_name)
    return as_aware_utc(value).astimezone(zone).strftime("%H:%M")
