"""Interactive configuration generator; never asks for camera Wi-Fi credentials."""

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("config/config.json"))
    parser.add_argument("--data-dir", default="/data")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite existing configuration")
    uid = input("Camera ID (suffix of ZIOTA_…): ").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8,32}", uid):
        raise SystemExit("Invalid camera ID")
    public_ip = input("Server public IPv4 address: ").strip()
    ipaddress.IPv4Address(public_ip)
    public_url = input("HTTPS viewer URL (e.g. https://camera.example.org/cat-camera/): ").strip()
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("HTTPS URL required")
    password = secrets.token_urlsafe(24)
    config = {
        "uid": uid,
        "public_ip": public_ip,
        "public_url": public_url.rstrip("/") + "/",
        "username": "camera",
        "password_sha256": hashlib.sha256(password.encode()).hexdigest(),
        "signaling_port": 18882,
        "stun_port": 18883,
        "rtp_port": 18884,
        "http_port": 18886,
        "http_bind": "127.0.0.1",
        "ingest_bind": "0.0.0.0",
        "data_dir": args.data_dir,
        "storage_limit_gib": 2,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(config, out, indent=2)
    print("Configuration saved:", args.output)
    print("Viewer username: camera\nViewer password:", password)
    print("Keep the password private. It is shown here once and is not stored in plaintext.")


if __name__ == "__main__":
    main()
