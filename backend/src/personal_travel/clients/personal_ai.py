from dataclasses import dataclass

import httpx

from personal_travel.config import get_settings


class PersonalAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonalAIHealth:
    status: str
    service: str | None = None


class PersonalAIClient:
    """Typed HTTP boundary to personal-ai-system.

    Only a health probe is intentionally implemented in the scaffold. Research,
    extraction, and action contracts should be added only after the corresponding
    personal-ai-system API contract is accepted.
    """

    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None) -> None:
        settings = get_settings()
        self._base_url = str(base_url or settings.personal_ai_base_url).rstrip("/")
        self._timeout = timeout_seconds or settings.personal_ai_timeout_seconds

    async def health(self) -> PersonalAIHealth:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/health")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PersonalAIError("personal-ai-system is unavailable") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise PersonalAIError("personal-ai-system returned invalid health JSON") from exc

        if not isinstance(payload, dict):
            raise PersonalAIError("personal-ai-system returned an invalid health response")

        status = payload.get("status")
        service = payload.get("service")
        if not isinstance(status, str) or (service is not None and not isinstance(service, str)):
            raise PersonalAIError("personal-ai-system returned an invalid health response")

        return PersonalAIHealth(status=status, service=service)
