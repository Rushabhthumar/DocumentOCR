from app.services.network_errors import dig_json_path, is_auth_error, is_transient_network_error
from app.services.retry_utils import retry_with_backoff


def test_detects_getaddrinfo():
    err = OSError(11001, "getaddrinfo failed")
    assert is_transient_network_error(err)


def test_detects_401():
    assert is_auth_error(Exception("401 Unauthorized"))


def test_dig_json_path():
    assert dig_json_path({"data": {"token": "abc"}}, "data.token") == "abc"


def test_retry_succeeds_after_transient(monkeypatch):
    from app.config import Settings

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("getaddrinfo failed")
        return "ok"

    settings = Settings(
        network_retry_max_attempts=5,
        network_retry_base_delay_seconds=0.01,
        network_retry_max_delay_seconds=0.02,
    )
    assert retry_with_backoff(flaky, settings=settings, label="test") == "ok"
    assert calls["n"] == 3
