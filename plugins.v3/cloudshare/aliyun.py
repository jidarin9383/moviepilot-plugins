# SPDX-License-Identifier: GPL-3.0-only
"""Discover playable files in public Alipan shares."""

import asyncio
from collections import deque
from dataclasses import dataclass
import re

from .discovery import Candidate, DiscoveryError, SearchResult, _request
from .selection import ShareFile, select_files

API = "https://api.aliyundrive.com"
LINK = re.compile(r"https://(?:www\.)?(?:alipan|aliyundrive)\.com/s/([A-Za-z0-9]{1,64})(?:[/?#].*)?")
HEADERS = {"Referer": "https://www.aliyundrive.com/", "Origin": "https://www.aliyundrive.com",
           "x-canary": "client=web,app=share,version=v2.3.1"}


@dataclass(frozen=True)
class AliyunCandidate(Candidate):
    @property
    def url(self):
        return f"https://www.alipan.com/s/{self.share_id}"


class AliyunDiscovery:
    def __init__(self, pansou_url, request=_request, *, telegram=None):
        self.search_url = pansou_url.rstrip("/") + "/api/search"
        self.request = request
        self.telegram = telegram

    async def candidates(self, keyword):
        if self.telegram is None:
            return await self._pansou(keyword)
        pansou, telegram = await asyncio.gather(
            self._pansou(keyword), self.telegram(keyword), return_exceptions=True)
        if isinstance(pansou, DiscoveryError) and isinstance(telegram, DiscoveryError):
            raise pansou
        for response in (pansou, telegram):
            if isinstance(response, BaseException) and not isinstance(response, DiscoveryError):
                raise response
        combined = {x.share_id: x for x in pansou} if isinstance(pansou, list) else {}
        extra = []
        for item in telegram if isinstance(telegram, list) else ():
            previous = combined.get(item.share_id)
            if previous is None:
                extra.append(item)
            elif not previous.passcode and item.passcode:
                combined[item.share_id] = AliyunCandidate(previous.share_id, previous.title, item.passcode)
        extra = extra[:3]
        return list(combined.values())[:10 - len(extra)] + extra

    async def _pansou(self, keyword):
        payload = await self.request(self.search_url, {"kw": keyword, "res": "merge",
                                                       "cloud_types": "aliyun"})
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            raise DiscoveryError("PanSou Aliyun search failed")
        candidates = {}
        for row in (data.get("merged_by_type") or {}).get("aliyun") or ():
            match = LINK.fullmatch(str(row.get("url") or ""))
            if match:
                title = " ".join(str(row.get("note") or row.get("title") or keyword).split())
                candidates.setdefault(match[1], AliyunCandidate(match[1], title, str(row.get("password") or "")))
            if len(candidates) >= 10:
                break
        return list(candidates.values())

    async def resolve(self, candidate, episodes=None):
        share = {"share_id": candidate.share_id}
        payload = await self.request(API + "/v2/share_link/get_share_token", {}, headers=HEADERS,
                                     body={**share, "share_pwd": candidate.passcode})
        token = payload.get("share_token")
        if not token:
            raise DiscoveryError("Aliyun share unavailable")
        headers = {**HEADERS, "x-share-token": token}
        pending = deque([("root", "", 0)])
        seen, files = {"root"}, []
        while pending:
            parent, prefix, depth = pending.popleft()
            marker = ""
            markers = set()
            while True:
                page = await self.request(API + "/adrive/v2/file/list_by_share", {}, headers=headers,
                                          body={**share, "parent_file_id": parent, "limit": 100,
                                                "marker": marker})
                items = page.get("items")
                if not isinstance(items, list):
                    raise DiscoveryError("Invalid Aliyun file list")
                for item in items:
                    fid, name = item.get("file_id"), item.get("name")
                    if not isinstance(fid, str) or fid in seen or len(seen) >= 2000:
                        raise DiscoveryError("Invalid or oversized Aliyun directory")
                    seen.add(fid)
                    if not isinstance(name, str) or name in {"", ".", ".."} or any(c in name for c in "/\\:\x00"):
                        continue
                    path = prefix + name
                    if item.get("type") == "folder" and depth < 8:
                        pending.append((fid, path + "/", depth + 1))
                    elif item.get("type") == "file" and type(item.get("size")) is int and item["size"] > 0:
                        files.append(ShareFile(fid, path, item["size"], parent))
                marker = page.get("next_marker")
                if not marker:
                    break
                if not isinstance(marker, str) or marker in markers or len(markers) >= 20:
                    raise DiscoveryError("Invalid Aliyun page marker")
                markers.add(marker)
        return SearchResult(candidate, tuple(select_files(files, episodes)))

    async def search(self, keyword):
        candidates = await self.candidates(keyword)
        slots = asyncio.Semaphore(4)

        async def verified(candidate):
            async with slots:
                try:
                    result = await self.resolve(candidate)
                    return result if result.files else None
                except DiscoveryError:
                    return None

        tasks = [asyncio.create_task(verified(candidate)) for candidate in candidates]
        if not tasks:
            return []
        try:
            done, _ = await asyncio.wait(tasks, timeout=7)
            return [task.result() for task in tasks if task in done and task.result() is not None]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
