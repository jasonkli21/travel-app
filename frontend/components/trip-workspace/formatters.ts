export function formatDate(value: string): string {
  // Trip dates are date-only values. Format UTC components so zones such as
  // UTC+14 cannot display a trip day as the following local calendar date.
  return new Intl.DateTimeFormat("en", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

export function formatDuration(seconds: number): string {
  const totalMinutes = Math.ceil(Math.max(0, seconds) / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours} hr ${minutes} min` : `${minutes} min`;
}

export function formatDistance(meters: number): string {
  return meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`;
}

export function formatAvailableGap(seconds: number): string {
  return seconds < 0
    ? `overlapping by ${formatDuration(-seconds)}`
    : `${formatDuration(seconds)} available`;
}
