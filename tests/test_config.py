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


# --- Default user / single-user mode ---------------------------------------


def test_default_user_id_default_value():
    settings = config.Settings(database_url="sqlite:///x")
    assert settings.default_user_id == "00000000-0000-0000-0000-000000000001"


def test_single_user_mode_default_value():
    settings = config.Settings(database_url="sqlite:///x")
    assert settings.single_user_mode is False


def test_load_config_default_user_id_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x")
    monkeypatch.setenv("DEFAULT_USER_ID", "abc123")

    settings = config.load_config()

    assert settings.default_user_id == "abc123"


def test_load_config_single_user_mode_true_variants(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x")

    for value in ("1", "true", "TRUE", "True", "yes", "on", "YES", "ON"):
        monkeypatch.setenv("SINGLE_USER_MODE", value)
        assert config.load_config().single_user_mode is True, f"failed for {value!r}"

    for value in ("0", "false", "False", "no", "off", "", "random"):
        monkeypatch.setenv("SINGLE_USER_MODE", value)
        assert config.load_config().single_user_mode is False, f"failed for {value!r}"
