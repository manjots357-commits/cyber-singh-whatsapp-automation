"""
Test isolation. These tests must NEVER touch your real database or your real Meta account,
so the environment is pinned BEFORE any project module is imported (python-dotenv does not
override variables that are already set, so your .env cannot leak into the tests).
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="wa_tests_")
os.environ.update({
    "WA_DB_PATH": os.path.join(_TMP, "session.db"),
    "WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": "", "WHATSAPP_BUSINESS_ACCOUNT_ID": "",
    "SAFE_MODE": "true", "SEND_DELAY_SECONDS": "0", "MAX_RETRIES": "2",
    "META_APP_SECRET": "test-app-secret", "WEBHOOK_VERIFY_TOKEN": "verify-me",
    "WEBHOOK_ALLOW_UNSIGNED": "false", "DASHBOARD_PASSWORD": "", "OWNER_WHATSAPP_NUMBER": "",
    "DAILY_RECIPIENT_LIMIT": "250", "DEFAULT_COUNTRY_CODE": "91",
    # every setting a test depends on is pinned, so nothing in the real .env can change test results
    "META_GRAPH_VERSION": "v26.0", "DEFAULT_TEMPLATE_LANGUAGE": "pa", "FAILURE_CIRCUIT_BREAKER": "5",
    "DASHBOARD_USER": "admin",
})

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    import config
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    # always start every test in safe mode, no credentials
    monkeypatch.setattr(config, "FORCE_SAFE_MODE", True)
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "")
    monkeypatch.setattr(config, "WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "SEND_DELAY_SECONDS", 0)
    database.init_db()
    yield


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = b"x"
        self.text = str(payload)

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload


def _live_setup(monkeypatch, authorize: bool):
    import config
    import database
    import whatsapp_service
    monkeypatch.setattr(config, "FORCE_SAFE_MODE", False)
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "TEST_TOKEN")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(config, "WHATSAPP_BUSINESS_ACCOUNT_ID", "9876543210")
    monkeypatch.setattr(whatsapp_service.time, "sleep", lambda *_: None)
    if authorize:
        database.set_state("live_authorized_at", database.now_iso())

    class Net:
        calls = []
        responder = staticmethod(lambda url, json: FakeResponse(200, {"messages": [{"id": "wamid.%d" % len(Net.calls)}]}))

    def fake_post(url, headers=None, json=None, timeout=None):
        Net.calls.append({"url": url, "json": json, "headers": headers})
        return Net.responder(url, json)

    Net.calls = []
    monkeypatch.setattr(whatsapp_service.requests, "post", fake_post)
    return Net


@pytest.fixture
def live_mode(monkeypatch):
    """LIVE mode as the owner would have it after authorising: fake credentials, HTTP captured, never sent."""
    return _live_setup(monkeypatch, authorize=True)


@pytest.fixture
def live_creds_only(monkeypatch):
    """Credentials present and SAFE_MODE=false in .env, but live sending NOT authorised yet."""
    return _live_setup(monkeypatch, authorize=False)
