from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from personal_state_mcp.secrets import (
    SecretError,
    delete_google_health_credentials,
    get_google_health_credentials,
    set_google_health_credentials,
)


def _legacy_payload() -> dict[str, str]:
    return {
        "schema_version": "personal-state-google-health-credentials/v1",
        "client_id": "client",
        "client_secret": "secret",
        "refresh_token": "refresh",
        "redirect_uri": "http://localhost",
        "scopes": "scope-a scope-b",
    }


def test_google_health_credentials_do_not_read_legacy_plaintext_file(monkeypatch, tmp_path) -> None:
    legacy_file = tmp_path / "google-health-credentials.json"
    legacy_file.write_text(json.dumps(_legacy_payload()), encoding="utf-8")
    monkeypatch.setenv("PERSONAL_STATE_GOOGLE_HEALTH_CREDENTIALS_FILE", str(legacy_file))
    monkeypatch.setitem(sys.modules, "keyring", None)

    assert get_google_health_credentials() is None


def test_google_health_credentials_fail_closed_without_keyring(monkeypatch, tmp_path) -> None:
    legacy_file = tmp_path / "google-health-credentials.json"
    monkeypatch.setenv("PERSONAL_STATE_GOOGLE_HEALTH_CREDENTIALS_FILE", str(legacy_file))
    monkeypatch.setitem(sys.modules, "keyring", None)

    with pytest.raises(SecretError, match="OS keychain"):
        set_google_health_credentials(
            client_id="client",
            client_secret="secret",
            refresh_token="refresh",
            redirect_uri="http://localhost",
            scopes="scope-a",
        )

    assert not legacy_file.exists()


def test_google_health_delete_removes_legacy_plaintext_file_without_keyring(monkeypatch, tmp_path) -> None:
    legacy_file = tmp_path / "google-health-credentials.json"
    legacy_file.write_text(json.dumps(_legacy_payload()), encoding="utf-8")
    monkeypatch.setenv("PERSONAL_STATE_GOOGLE_HEALTH_CREDENTIALS_FILE", str(legacy_file))
    monkeypatch.setitem(sys.modules, "keyring", None)

    delete_google_health_credentials(missing_ok=True)

    assert not legacy_file.exists()


def test_google_health_credentials_use_keyring(monkeypatch, tmp_path) -> None:
    store: dict[tuple[str, str], str] = {}

    def set_password(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    def get_password(service: str, username: str) -> str | None:
        return store.get((service, username))

    fake_keyring = SimpleNamespace(set_password=set_password, get_password=get_password)
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setenv("PERSONAL_STATE_GOOGLE_HEALTH_CREDENTIALS_FILE", str(tmp_path / "unused.json"))

    set_google_health_credentials(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        redirect_uri="http://localhost",
        scopes="scope-a scope-b",
    )

    assert get_google_health_credentials() == {
        "client_id": "client",
        "client_secret": "secret",
        "refresh_token": "refresh",
        "redirect_uri": "http://localhost",
        "scopes": "scope-a scope-b",
    }
