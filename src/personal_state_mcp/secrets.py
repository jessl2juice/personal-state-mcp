from __future__ import annotations

import getpass
import os
import secrets
from uuid import uuid4


SERVICE_NAME = "personal-state-mcp"


class SecretError(RuntimeError):
    pass


def get_libre_password(email: str) -> str | None:
    env_password = os.environ.get("LIBRELINKUP_PASSWORD")
    if env_password:
        return env_password
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring.get_password(SERVICE_NAME, f"librelinkup:{email}")


def set_libre_password(email: str, password: str | None = None) -> None:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise SecretError("The optional keyring package is required to store passwords in the OS keychain.") from exc
    secret = password if password is not None else getpass.getpass("LibreLinkUp follower password: ")
    keyring.set_password(SERVICE_NAME, f"librelinkup:{email}", secret)


def _delete_password(keyring, username: str, *, missing_ok: bool) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, username)
    except Exception:
        if not missing_ok:
            raise


def delete_libre_password(email: str, *, missing_ok: bool = False) -> None:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise SecretError("The optional keyring package is required to delete passwords from the OS keychain.") from exc
    _delete_password(keyring, f"librelinkup:{email}", missing_ok=missing_ok)


def get_watch_device_secret(device_id: str) -> str | None:
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring.get_password(SERVICE_NAME, f"watch-device:{device_id}")


def get_watch_identifier_key() -> str | None:
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring.get_password(SERVICE_NAME, "watch-identifier-key")


def set_watch_secrets(device_id: str, device_secret: str, identifier_key: str) -> None:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise SecretError("The optional keyring package is required to store watch secrets.") from exc
    keyring.set_password(SERVICE_NAME, f"watch-device:{device_id}", device_secret)
    keyring.set_password(SERVICE_NAME, "watch-identifier-key", identifier_key)


def get_or_create_watch_record_identity() -> tuple[str, str]:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise SecretError("The optional keyring package is required to store watch identity secrets.") from exc
    namespace = keyring.get_password(SERVICE_NAME, "watch-record-identity-namespace")
    identity_key = keyring.get_password(SERVICE_NAME, "watch-record-identity-key")
    if namespace and identity_key:
        return namespace, identity_key
    if namespace or identity_key:
        raise SecretError("The stored watch record identity is incomplete; explicit recovery is required.")
    namespace = str(uuid4())
    identity_key = secrets.token_urlsafe(32)
    keyring.set_password(SERVICE_NAME, "watch-record-identity-namespace", namespace)
    keyring.set_password(SERVICE_NAME, "watch-record-identity-key", identity_key)
    return namespace, identity_key


def delete_watch_secrets(device_id: str, *, missing_ok: bool = False) -> None:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise SecretError("The optional keyring package is required to delete watch secrets.") from exc
    _delete_password(keyring, f"watch-device:{device_id}", missing_ok=missing_ok)
    _delete_password(keyring, "watch-identifier-key", missing_ok=missing_ok)
    _delete_password(keyring, "watch-record-identity-namespace", missing_ok=missing_ok)
    _delete_password(keyring, "watch-record-identity-key", missing_ok=missing_ok)


def get_watch_access_credentials(device_id: str) -> tuple[str, str] | None:
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    client_id = keyring.get_password(SERVICE_NAME, f"watch-access-client-id:{device_id}")
    client_secret = keyring.get_password(SERVICE_NAME, f"watch-access-client-secret:{device_id}")
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def set_watch_access_credentials(device_id: str, client_id: str, client_secret: str) -> None:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise SecretError("The optional keyring package is required to store Access credentials.") from exc
    keyring.set_password(SERVICE_NAME, f"watch-access-client-id:{device_id}", client_id)
    keyring.set_password(SERVICE_NAME, f"watch-access-client-secret:{device_id}", client_secret)


def delete_watch_access_credentials(device_id: str, *, missing_ok: bool = False) -> None:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise SecretError("The optional keyring package is required to delete Access credentials.") from exc
    _delete_password(keyring, f"watch-access-client-id:{device_id}", missing_ok=missing_ok)
    _delete_password(keyring, f"watch-access-client-secret:{device_id}", missing_ok=missing_ok)
