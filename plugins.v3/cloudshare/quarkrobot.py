# SPDX-License-Identifier: GPL-3.0-only
"""Query cloud-search bots through a dedicated Telegram user session."""

import asyncio
import json
import os
import re
import stat
import time
from urllib.parse import parse_qs, urlsplit

from .discovery import Candidate, DiscoveryError


LINK = re.compile(r"(?<![\w/])https://pan\.quark\.cn/s/[A-Za-z0-9]{1,64}(?![\w.-])(?:\?[^\s<>\"']*)?")
ALIYUN_LINK = re.compile(r"(?<![\w/])https://(?:www\.)?(?:alipan|aliyundrive)\.com/s/[A-Za-z0-9]{1,64}(?![\w.-])(?:\?[^\s<>\"']*)?")
ACCESS_CODE = re.compile(r"(?:提取码|提取碼|访问码|密碼|密码)\s*[:：]?\s*([A-Za-z0-9]{4,8})(?![A-Za-z0-9])")
BOTS = {"quark": "QuarkRobot", "aliyun": "AliYunPanBot"}


def inline_candidates(results, keyword, *, provider="quark"):
    if provider not in BOTS:
        raise ValueError("Unsupported Telegram cloud provider")
    candidate_type = Candidate
    if provider == "aliyun":
        from .aliyun import AliyunCandidate
        candidate_type = AliyunCandidate
    pattern = ALIYUN_LINK if provider == "aliyun" else LINK
    candidates = {}
    for wrapper in results:
        result = wrapper.result
        message = getattr(result, "send_message", None)
        sources = [getattr(result, "url", None), getattr(message, "message", None)]
        sources.extend(getattr(entity, "url", None) for entity in
                       (getattr(message, "entities", None) or ()))
        for row in getattr(getattr(message, "reply_markup", None), "rows", ()):
            sources.extend(getattr(button, "url", None) for button in row.buttons)
        title = " ".join((getattr(result, "title", None) or keyword).split())[:256]
        codes = set(ACCESS_CODE.findall(getattr(message, "message", None) or ""))
        text_code = next(iter(codes)) if len(codes) == 1 else ""
        for source in filter(None, sources):
            for match in pattern.finditer(source):
                url = urlsplit(match[0])
                share = url.path.rsplit("/", 1)[-1]
                query = parse_qs(url.query)
                code = query.get("pwd", query.get("password", [text_code]))[0]
                if share not in candidates or (code and not candidates[share].passcode):
                    candidates[share] = candidate_type(share, title, code)
    return list(candidates.values())


class TelegramSource:
    def __init__(self, path, *, bots=None, providers=None, proxy_url=None):
        self.path = path
        self.retry_after = 0
        self.bots = bots
        self.providers = set(providers) if providers is not None else set(BOTS)
        self.proxy_url = proxy_url

    def for_provider(self, provider):
        if provider not in BOTS:
            raise ValueError("Unsupported Telegram cloud provider")

        async def search(keyword):
            return await self._search(keyword, provider)
        return search

    def _config(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 8192:
                raise ValueError("Invalid Telegram credential file")
            return json.loads(stream.read(8193))

    async def __call__(self, keyword):
        return await self._search(keyword, "quark")

    async def _search(self, keyword, provider):
        if provider not in self.providers:
            return []
        if time.monotonic() < self.retry_after:
            raise DiscoveryError("Telegram search is cooling down")
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            config = self._config()
            bot = (self.bots or config.get("bots") or BOTS).get(provider)
            bot = bot.strip().lstrip("@") if isinstance(bot, str) else bot
            if not isinstance(bot, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", bot):
                raise ValueError("Invalid Telegram bot username")
            if not config["session"]:
                raise ValueError("Missing Telegram session")
            proxy = config.get("proxy")
            if self.proxy_url is not None:
                proxy = None
                if self.proxy_url:
                    parsed = urlsplit(self.proxy_url)
                    if (parsed.scheme not in {"http", "socks5"} or not parsed.hostname or not parsed.port
                            or parsed.username or parsed.password or parsed.query or parsed.fragment
                            or parsed.path not in {"", "/"}):
                        raise ValueError("Invalid Telegram proxy")
                    proxy = {"proxy_type": parsed.scheme, "addr": parsed.hostname, "port": parsed.port}
            client = TelegramClient(
                StringSession(config["session"]), int(config["api_id"]), config["api_hash"],
                proxy=proxy, receive_updates=False, connection_retries=0,
                request_retries=0, flood_sleep_threshold=0, auto_reconnect=False,
            )
            try:
                async with asyncio.timeout(6):
                    await client.connect()
                    results = await client.inline_query(bot, keyword, entity="me")
                return inline_candidates(results, keyword, provider=provider)
            finally:
                await client.disconnect()
        except Exception as error:
            if type(error).__name__ == "FloodWaitError":
                self.retry_after = time.monotonic() + max(1, int(error.seconds))
            raise DiscoveryError(f"Telegram search unavailable ({type(error).__name__})") from None
