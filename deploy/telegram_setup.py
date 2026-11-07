# SPDX-License-Identifier: GPL-3.0-only
"""Create a private NAS Telegram search session through an interactive TTY."""

import argparse
import asyncio
from getpass import getpass
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from urllib.parse import urlsplit


def normalize_phone(value):
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\s()\-]", "", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", value):
        raise ValueError("Use + followed by country code and phone number; do not omit the country code.")
    return value


async def request_login_code(client, phone, migration_errors):
    try:
        return await asyncio.wait_for(client.send_code_request(phone), 20)
    except migration_errors:
        # Telethon has already switched DC before raising the last call error.
        print("Telegram routed this account to another data center; completing that request once.", flush=True)
        return await asyncio.wait_for(client.send_code_request(phone), 20)


def save_new_config(path, config):
    """Publish a mode-0600 configuration without replacing an existing login."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".telegram-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(config, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


async def setup(args):
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError, PhoneMigrateError, NetworkMigrateError, UserMigrateError
    from telethon.sessions import StringSession

    api_id = getpass("Telegram APP ID (hidden): ").strip()
    api_hash = getpass("Telegram API hash (hidden): ").strip()
    if not api_id.isdigit() or int(api_id) <= 0 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise ValueError("Invalid API credentials")
    parsed = urlsplit(args.proxy) if args.proxy else None
    proxy = None
    if parsed:
        if (parsed.scheme not in {"http", "socks5"} or not parsed.hostname or not parsed.port
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("Invalid proxy")
        proxy = {"proxy_type": parsed.scheme, "addr": parsed.hostname, "port": parsed.port}
    client = TelegramClient(
        StringSession(), int(api_id), api_hash, proxy=proxy, receive_updates=False,
        request_retries=0, connection_retries=0, flood_sleep_threshold=0,
        auto_reconnect=False, raise_last_call_error=True)
    try:
        await asyncio.wait_for(client.connect(), 15)
        while True:
            try:
                phone = normalize_phone(getpass("Telegram phone, include +country code (hidden): "))
                break
            except ValueError:
                print("Invalid phone format. Include + and the country code (China: +86). "
                      "Spaces and hyphens are allowed. Please enter it again.", flush=True)
        print("Phone format accepted. Requesting a Telegram login code...", flush=True)
        await request_login_code(client, phone, (PhoneMigrateError, NetworkMigrateError, UserMigrateError))
        code = getpass("Telegram login code (hidden): ").strip()
        try:
            await asyncio.wait_for(client.sign_in(phone=phone, code=code), 20)
        except SessionPasswordNeededError:
            password = getpass("Telegram two-step password (hidden): ")
            await asyncio.wait_for(client.sign_in(password=password), 20)
        if not await asyncio.wait_for(client.is_user_authorized(), 10):
            raise RuntimeError("Telegram authorization incomplete")
        save_new_config(args.output, {
            "api_id": int(api_id), "api_hash": api_hash,
            "session": client.session.save(), "proxy": proxy,
            "bots": {"quark": "QuarkRobot", "aliyun": "AliYunPanBot"},
        })
        print("Saved. Telegram search session is configured; no credentials were printed.", flush=True)
        for bot in ("QuarkRobot", "AliYunPanBot"):
            try:
                results = await asyncio.wait_for(client.inline_query(bot, "机器人之梦", entity="me"), 15)
                print(f"@{bot}: {len(results)} inline results.", flush=True)
            except Exception as error:
                print(f"@{bot}: search check failed ({type(error).__name__}); login remains saved.", flush=True)
                if type(error).__name__ == "FloodWaitError":
                    break
    finally:
        await client.disconnect()


def main():
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/config/cloudshare/telegram.json"))
    parser.add_argument("--proxy")
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "Run this setup in your own interactive terminal.\n")
    if args.output.exists() or args.output.is_symlink():
        parser.exit(1, "A Telegram configuration already exists; it was not changed.\n")
    try:
        asyncio.run(setup(args))
    except Exception as error:
        parser.exit(1, f"Setup stopped ({type(error).__name__}); no automatic retry.\n")


if __name__ == "__main__":
    main()
