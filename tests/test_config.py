import personal_deadline_management_agent.config as config


def test_database_url_used_when_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://a:b@h:1/d")
    # DB_* must be ignored when DATABASE_URL is present (P0.4 rule C).
    monkeypatch.setenv("DB_HOST", "ignored-host")

    settings = config.load_config()

    assert settings.database_url == "postgresql+psycopg2://a:b@h:1/d"


def test_fallback_to_database_config(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "db")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "app")
    monkeypatch.setenv("DB_USER", "user")
    monkeypatch.setenv("DB_PASSWORD", "pass")

    settings = config.load_config()

    assert settings.database_url.startswith(
        "postgresql+psycopg2://user:pass@db:5432/app"
    )
