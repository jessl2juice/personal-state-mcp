from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .adapters.libre_linkup import LibreLinkUpAdapter
from .adapters.google_health import (
    GoogleHealthAdapter,
    GoogleHealthClient,
    authorization_url,
    complete_authorization,
    read_oauth_client,
)
from .collector import Collector
from .config import load_config
from .secrets import (
    delete_libre_password,
    delete_watch_access_credentials,
    delete_watch_secrets,
    delete_google_health_credentials,
    get_google_health_credentials,
    set_libre_password,
)
from .storage import StateStore
from .history_import import inspect_history_export, parse_google_fit_takeout, parse_libreview_csv


def _build_collector(store: StateStore, config):
    adapters = [LibreLinkUpAdapter(config)]
    if config.google_health_enabled:
        adapters.append(GoogleHealthAdapter(config))
    return Collector(
        store=store,
        adapters=adapters,
        min_poll_interval_seconds=config.min_poll_interval_seconds,
    )


def cmd_status(args) -> int:
    config = load_config()
    store = StateStore(config.db_path)
    latest = store.latest_glucose()
    payload = {
        "db_path": str(config.db_path),
        "host_id": config.host_id,
        "allowed_hosts": list(config.allowed_hosts),
        "latest_glucose": latest.public_dict() if latest else None,
        "last_collector_run": store.last_collector_run(),
        "opportunistic_refresh_enabled": config.opportunistic_refresh_enabled,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_collect_once(args) -> int:
    config = load_config()
    store = StateStore(config.db_path)
    collector = _build_collector(store, config)
    print(json.dumps(collector.collect_once(), indent=2, sort_keys=True))
    return 0


def cmd_collect_loop(args) -> int:
    config = load_config()
    store = StateStore(config.db_path)
    collector = _build_collector(store, config)
    collector.collect_loop()
    return 0


def cmd_set_libre_password(args) -> int:
    set_libre_password(args.email)
    print("Stored LibreLinkUp follower password in the OS keychain.")
    return 0


def cmd_delete_credentials(args) -> int:
    if not args.yes:
        raise SystemExit("Refusing to delete credentials without --yes.")
    config = load_config()
    deleted: list[str] = []
    if config.libre_email:
        delete_libre_password(config.libre_email, missing_ok=True)
        deleted.append("librelinkup")
    if config.watch_device_id:
        delete_watch_secrets(config.watch_device_id, missing_ok=True)
        delete_watch_access_credentials(config.watch_device_id, missing_ok=True)
        deleted.append("watch")
    if get_google_health_credentials() is not None:
        delete_google_health_credentials(missing_ok=True)
        deleted.append("google_health")
    print(json.dumps({"credentials_deleted": deleted}, indent=2))
    return 0


def cmd_export_history(args) -> int:
    config = load_config()
    store = StateStore(config.db_path)
    count = store.export_history_jsonl(Path(args.output))
    print(json.dumps({"output": args.output, "readings_exported": count}, indent=2))
    return 0


def cmd_delete_history(args) -> int:
    if not args.yes:
        raise SystemExit("Refusing to delete history without --yes.")
    config = load_config()
    store = StateStore(config.db_path)
    before = None
    if args.before:
        before = datetime.fromisoformat(args.before.replace("Z", "+00:00")).astimezone(timezone.utc)
    count = store.delete_history(before=before)
    print(json.dumps({"readings_deleted": count}, indent=2))
    return 0


def cmd_prune_history(args) -> int:
    config = load_config()
    retention_days = args.days if args.days is not None else config.retention_days
    if retention_days is None:
        raise SystemExit("No retention days configured. Pass --days or set PERSONAL_STATE_MCP_RETENTION_DAYS.")
    store = StateStore(config.db_path)
    count = store.prune_retention(retention_days)
    print(json.dumps({"retention_days": retention_days, "readings_deleted": count}, indent=2))
    return 0


def cmd_inspect_history_export(args) -> int:
    preview = inspect_history_export(Path(args.path))
    print(json.dumps(preview.public_dict(), indent=2, sort_keys=True))
    return 0


def cmd_import_libreview(args) -> int:
    config = load_config()
    timezone_name = args.timezone or config.source_timezone
    if not timezone_name:
        raise SystemExit("A source timezone is required. Pass --timezone or configure PERSONAL_STATE_MCP_SOURCE_TIMEZONE.")
    readings, preview = parse_libreview_csv(Path(args.path), source_timezone=timezone_name)
    payload = preview.public_dict()
    payload["dry_run"] = bool(args.dry_run)
    if args.dry_run:
        payload["inserted"] = 0
        payload["duplicates"] = 0
    else:
        store = StateStore(config.db_path)
        backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = config.db_path.with_name(f"{config.db_path.name}.pre-history-import-{backup_stamp}.bak")
        store.backup_to(backup_path)
        result = store.import_glucose_readings(readings)
        payload.update(result)
        payload["database"] = str(config.db_path)
        payload["backup"] = str(backup_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_import_google_fit(args) -> int:
    config = load_config()
    observations, preview = parse_google_fit_takeout(Path(args.path))
    payload = preview.public_dict()
    payload["dry_run"] = bool(args.dry_run)
    if args.dry_run:
        payload["inserted"] = 0
        payload["duplicates"] = 0
    else:
        store = StateStore(config.db_path)
        backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = config.db_path.with_name(f"{config.db_path.name}.pre-google-fit-import-{backup_stamp}.bak")
        store.backup_to(backup_path)
        payload.update(store.import_health_observations(observations))
        payload["database"] = str(config.db_path)
        payload["backup"] = str(backup_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_google_health_connect(args) -> int:
    client = read_oauth_client(Path(args.credentials))
    if not args.code:
        print(json.dumps({"authorization_required": True, "authorization_url": authorization_url(client)}, indent=2))
        return 0
    print(json.dumps(complete_authorization(client, args.code), indent=2, sort_keys=True))
    return 0


def cmd_google_health_status(args) -> int:
    credentials = get_google_health_credentials()
    payload: dict[str, object] = {"connected": credentials is not None}
    if credentials:
        payload["scopes"] = credentials.get("scopes", "").split()
        try:
            devices = GoogleHealthClient.from_keyring().paired_devices()
            payload["paired_devices"] = [
                {
                    "device_version": item.get("deviceVersion"),
                    "device_type": item.get("deviceType"),
                    "battery_status": item.get("batteryStatus"),
                    "battery_level": item.get("batteryLevel"),
                    "last_sync_time": item.get("lastSyncTime"),
                }
                for item in devices
            ]
        except Exception as exc:
            payload["connection_error"] = str(exc)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _persist_google_health_result(store: StateStore, result) -> dict[str, object]:
    stored = store.import_health_observations(result.observations)
    return {
        "adapter": result.adapter,
        "status": result.status,
        "observations_seen": len(result.observations),
        "observations_inserted": stored["inserted"],
        "duplicates": stored["duplicates"],
        "errors": [error.public_dict() for error in result.errors],
        "metadata": result.metadata,
    }


def cmd_google_health_sync(args) -> int:
    config = load_config()
    store = StateStore(config.db_path)
    adapter = GoogleHealthAdapter(config)
    end = datetime.now(timezone.utc)
    result = adapter.collect_range(end - timedelta(hours=max(1, args.hours)), end)
    print(json.dumps(_persist_google_health_result(store, result), indent=2, sort_keys=True))
    return 0 if result.status != "error" else 1


def cmd_google_health_backfill(args) -> int:
    config = load_config()
    store = StateStore(config.db_path)
    adapter = GoogleHealthAdapter(config)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, args.days))
    cursor = start
    totals = {"windows": 0, "observations_seen": 0, "observations_inserted": 0, "duplicates": 0, "errors": []}
    while cursor < end:
        window_end = min(cursor + timedelta(days=14), end)
        payload = _persist_google_health_result(store, adapter.collect_range(cursor, window_end))
        totals["windows"] += 1
        totals["observations_seen"] += int(payload["observations_seen"])
        totals["observations_inserted"] += int(payload["observations_inserted"])
        totals["duplicates"] += int(payload["duplicates"])
        totals["errors"].extend(payload["errors"])
        cursor = window_end
    totals["database"] = str(config.db_path)
    totals["requested_days"] = args.days
    print(json.dumps(totals, indent=2, sort_keys=True))
    return 0 if not totals["errors"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal State MCP local utilities.")
    sub = parser.add_subparsers(required=True)

    status = sub.add_parser("status", help="Show local status without contacting vendors.")
    status.set_defaults(func=cmd_status)

    collect_once = sub.add_parser("collect-once", help="Fetch and persist one batch from configured adapters.")
    collect_once.set_defaults(func=cmd_collect_once)

    collect_loop = sub.add_parser("collect-loop", help="Run the local collector loop.")
    collect_loop.set_defaults(func=cmd_collect_loop)

    password = sub.add_parser("set-libre-password", help="Store a LibreLinkUp follower password in the OS keychain.")
    password.add_argument("email")
    password.set_defaults(func=cmd_set_libre_password)

    delete_credentials = sub.add_parser(
        "delete-credentials",
        help="Delete configured LibreLinkUp, watch, and Google Health credentials from the OS keychain.",
    )
    delete_credentials.add_argument("--yes", action="store_true")
    delete_credentials.set_defaults(func=cmd_delete_credentials)

    export = sub.add_parser("export-history", help="Export glucose history to JSONL.")
    export.add_argument("output")
    export.set_defaults(func=cmd_export_history)

    delete = sub.add_parser("delete-history", help="Delete glucose history.")
    delete.add_argument("--before", help="Delete readings before this ISO timestamp. Omit to delete all.")
    delete.add_argument("--yes", action="store_true")
    delete.set_defaults(func=cmd_delete_history)

    prune = sub.add_parser("prune-history", help="Delete readings older than a retention window.")
    prune.add_argument("--days", type=int)
    prune.set_defaults(func=cmd_prune_history)

    inspect_export = sub.add_parser(
        "inspect-history-export",
        help="Identify a LibreView, Google Fit Takeout, or Samsung Health export without importing it.",
    )
    inspect_export.add_argument("path")
    inspect_export.set_defaults(func=cmd_inspect_history_export)

    libre_import = sub.add_parser(
        "import-libreview",
        help="Import and deduplicate an official LibreView glucose-history CSV.",
    )
    libre_import.add_argument("path")
    libre_import.add_argument("--timezone", help="Timezone used by the device timestamps, such as America/Los_Angeles.")
    libre_import.add_argument("--dry-run", action="store_true", help="Parse and report without changing the database.")
    libre_import.set_defaults(func=cmd_import_libreview)

    google_fit_import = sub.add_parser(
        "import-google-fit",
        help="Import and deduplicate a Google Fit Takeout ZIP or extracted directory.",
    )
    google_fit_import.add_argument("path")
    google_fit_import.add_argument("--dry-run", action="store_true", help="Parse and report without changing the database.")
    google_fit_import.set_defaults(func=cmd_import_google_fit)

    google_connect = sub.add_parser(
        "google-health-connect",
        help="Connect a Google Health account using a downloaded OAuth client JSON file.",
    )
    google_connect.add_argument("credentials")
    google_connect.add_argument("--code", help="Authorization code or complete redirected URL returned by Google.")
    google_connect.set_defaults(func=cmd_google_health_connect)

    google_status = sub.add_parser("google-health-status", help="Show Google Health connection and paired-device status.")
    google_status.set_defaults(func=cmd_google_health_status)

    google_sync = sub.add_parser("google-health-sync", help="Import recent Fitbit and Google wearable observations.")
    google_sync.add_argument("--hours", type=int, default=36)
    google_sync.set_defaults(func=cmd_google_health_sync)

    google_backfill = sub.add_parser("google-health-backfill", help="Import historical Fitbit and Google wearable observations.")
    google_backfill.add_argument("--days", type=int, default=365)
    google_backfill.set_defaults(func=cmd_google_health_backfill)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
