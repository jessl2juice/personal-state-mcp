from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from personal_state_mcp.secrets import (
    get_watch_access_credentials,
    get_watch_device_secret,
    set_watch_access_credentials,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a temporary Android pairing file.")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--replace-access-credential",
        action="store_true",
        help="Prompt for and replace the Cloudflare Access credential in the OS keychain.",
    )
    args = parser.parse_args()

    if not args.endpoint.startswith("https://"):
        raise SystemExit("The endpoint must use HTTPS.")

    if args.replace_access_credential:
        client_id = getpass.getpass("Cloudflare Access client ID: ").strip()
        client_secret = getpass.getpass("Cloudflare Access client secret: ").strip()
        if len(client_id) < 8 or len(client_secret) < 8:
            raise SystemExit("The Access credential is incomplete.")
        set_watch_access_credentials(args.device_id, client_id, client_secret)

    device_secret = get_watch_device_secret(args.device_id)
    access = get_watch_access_credentials(args.device_id)
    if not device_secret:
        raise SystemExit("The watch device secret is missing from the OS keychain.")
    if not access:
        raise SystemExit("The Cloudflare Access credential is missing from the OS keychain.")

    client_id, client_secret = access
    payload = {
        "endpoint": args.endpoint,
        "device_id": args.device_id,
        "device_secret": device_secret,
        "cf_access_client_id": client_id,
        "cf_access_client_secret": client_secret,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(args.output, 0o600)
    except OSError:
        pass
    print(f"Created temporary pairing file: {args.output}")


if __name__ == "__main__":
    main()
