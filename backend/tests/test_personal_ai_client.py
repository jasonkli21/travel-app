from personal_travel.clients.personal_ai import PersonalAIHealth


def test_personal_ai_health_shape() -> None:
    health = PersonalAIHealth(status="ok", service="personal-ai-api")
    assert health.status == "ok"
    assert health.service == "personal-ai-api"
