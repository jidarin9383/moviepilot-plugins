# SPDX-License-Identifier: GPL-3.0-only
"""
通过 Prowlarr 为 MoviePilot 提供公开 BT 搜索结果。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from app.sdk.logging import logger
from app.sdk.events import Event, eventmanager
from app.sdk.media import TorrentInfo
from app.sdk.network import AsyncRequestUtils
from app.sdk.plugin import _PluginBase
from app.schemas.types import ChainEventType
from .activity import UsageHistory, read_key, write_key
from .ui import DEFAULTS, activity_page, settings_form


SEARCH_TIMEOUT = 20


class PublicBTError(Exception):
    """A fixed diagnostic code without response bodies or request URLs."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SearchConfig:
    """
    保存一次搜索所用的配置快照，凭据不进入对象描述。

    :param url: Prowlarr 基础地址，可包含部署路径前缀。
    :param api_key: 仅用于请求头的 API Key。
    :param indexer_ids: 限定的公开索引站 ID，空元组表示所有已配置索引站。
    :param downloader: 宿主中配置的独立下载器名称。
    :param min_seeders: 返回结果所需的最低做种数。
    :param download_path: 专用下载根目录，空值表示沿用宿主选择。
    """

    url: str
    api_key: str = field(repr=False)
    indexer_ids: tuple[int, ...]
    downloader: str
    min_seeders: int
    download_path: str = ""
    result_limit: int = 100


def nonnegative_integer(value: Any) -> int:
    """
    严格读取非负整数，拒绝布尔值和隐式截断的小数。
    """
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError("Invalid integer")
    return int(text)


def valid_enclosure(value: Any) -> bool:
    """
    确认下载地址使用宿主支持的 HTTP 或磁力协议。
    """
    if not isinstance(value, str):
        return False
    if value.startswith("magnet:?"):
        return True
    parsed = urlsplit(value)
    return bool(parsed.scheme in {"http", "https"} and parsed.hostname
                and not parsed.username and not parsed.password)


def publication_date(value: Any) -> str | None:
    """
    将 Prowlarr 的 ISO 时间转换为宿主使用的本地时间字符串。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class PublicBT(_PluginBase):
    """
    聚合公开 BT 搜索，并将结果路由到配置的独立下载器。
    """

    plugin_name = "公开 BT 搜索"
    plugin_desc = "聚合公开 BT 搜索，管理独立下载路由并记录使用过程。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "0.2.0"
    plugin_label = "资源搜索"
    plugin_author = "Darin Ji"
    plugin_config_prefix = "publicbt_"
    plugin_order = 100
    auth_level = 1

    _config: SearchConfig | None = None
    _history = None
    _settings = None
    _connection_status = None
    _validation_error = ""
    _history_page = 1

    def init_plugin(self, config: dict | None = None) -> None:
        """
        校验本次配置并替换快照，清空配置时释放旧凭据。
        """
        self.stop_service()
        config = config or {}
        self._settings = {key: config.get(key, value) for key, value in DEFAULTS.items() if key != "api_key"}
        self._validation_error = ""
        self._connection_status = None
        self._history = None
        try:
            limit = nonnegative_integer(config.get("history_limit", 1000))
            self._settings["history_limit"] = max(1, min(5000, limit))
        except ValueError:
            self._settings["history_limit"] = 1000
        try:
            self._history = UsageHistory(Path(self.get_data_path()) / "usage.sqlite3",
                                         enabled=bool(config.get("history_enabled", True)),
                                         limit=self._settings["history_limit"])
        except (ValueError, OSError, sqlite3.Error):
            logger.warning("PublicBT usage history is unavailable.")
        key_path = Path(self.get_data_path()) / "prowlarr.key"
        try:
            supplied_key = str(config.get("api_key") or "")
            if "\n" in supplied_key or "\r" in supplied_key or len(supplied_key) > 16384:
                raise ValueError("Invalid key")
            if supplied_key.strip():
                write_key(key_path, supplied_key.strip())
                if self.update_config({**config, "api_key": ""}) is False:
                    raise ValueError("Unable to redact stored key")
        except (ValueError, OSError):
            self._validation_error = "API Key 无法私密保存，请检查输入和插件目录权限。"
            return
        if not config or not config.get("enabled"):
            return
        try:
            url = str(config.get("prowlarr_url") or "").strip().rstrip("/")
            parsed = urlsplit(url)
            api_key = read_key(key_path)
            valid_address = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            has_extras = any((parsed.username, parsed.password, parsed.query, parsed.fragment))
            valid_key = bool(api_key) and "\n" not in api_key and "\r" not in api_key
            if not valid_address or has_extras or not valid_key:
                raise ValueError("Invalid service URL or key")
            ids = config.get("indexer_ids") or ""
            values = ids.split(",") if isinstance(ids, str) else ids
            indexer_ids = tuple(dict.fromkeys(
                nonnegative_integer(value) for value in values if str(value).strip()
            ))
            if 0 in indexer_ids:
                raise ValueError("Invalid indexer ID")
            download_path = str(config.get("download_path") or "").strip()
            path = PurePosixPath(download_path)
            if download_path and (not path.is_absolute() or ".." in path.parts):
                raise ValueError("Invalid download path")
            result_limit = nonnegative_integer(config.get("result_limit", 100))
            if not 1 <= result_limit <= 200:
                raise ValueError("Invalid result limit")
            parsed_config = SearchConfig(
                url=url,
                api_key=api_key,
                indexer_ids=indexer_ids,
                downloader=str(config.get("downloader") or "qb-public").strip() or "qb-public",
                min_seeders=nonnegative_integer(config.get("min_seeders", 1)),
                download_path=download_path,
                result_limit=result_limit,
            )
            self._config = parsed_config
        except (ValueError, TypeError, OSError):
            self._validation_error = "配置未通过校验，请检查 Prowlarr 地址、API Key、索引站 ID、目录和搜索规则。"
            logger.warning("PublicBT 配置无效，请检查地址、API Key、索引站 ID、下载目录和最低做种数。")

    def get_state(self) -> bool:
        """
        返回是否持有有效且启用的配置。
        """
        return self._config is not None

    def get_module(self) -> dict[str, Any]:
        """
        声明宿主的同步与异步资源搜索扩展。
        """
        if not self.get_state():
            return {}
        return {
            "search_torrents": self.search_torrents,
            "async_search_torrents": self.async_search_torrents,
        }

    def search_torrents(
        self, site: dict | None = None, keyword: str = "", mtype: Any = None,
        page: int | None = 0, **kwargs: Any,
    ) -> list[TorrentInfo]:
        """
        在独立事件循环中执行限时搜索，兼容同步和异步调用方。

        :param site: 宿主模块合同的站点参数，本聚合源不依赖此值。
        :param keyword: 搜索关键词，空值返回空列表。
        :param mtype: 宿主媒体类型，未知时检索电影和电视剧分类。
        :param page: 宿主分页参数，仅首个分页参与搜索。
        :param kwargs: 保留宿主未来添加的可选搜索参数。
        :return: 可由宿主下载和整理的种子信息列表。
        """
        keyword = str(keyword or "").strip()
        if not self._config or not keyword or (page or 0) > 0:
            return []
        # 独立线程隔离调用方已有的事件循环，同步合同仍等待本次搜索完成。
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="publicbt") as executor:
            return executor.submit(
                asyncio.run,
                self.async_search_torrents(
                    site=site, keyword=keyword, mtype=mtype, page=page, **kwargs
                ),
            ).result()

    async def async_search_torrents(
        self, site: dict | None = None, keyword: str = "", mtype: Any = None,
        page: int | None = 0, **_kwargs: Any,
    ) -> list[TorrentInfo]:
        """
        在二十秒总预算内搜索，外部失败不阻断其它来源。

        :param site: 宿主模块合同的站点参数，本聚合源不依赖此值。
        :param keyword: 搜索关键词，空值返回空列表。
        :param mtype: 宿主媒体类型，未知时检索电影和电视剧分类。
        :param page: 宿主分页参数，仅首个分页参与搜索。
        :param _kwargs: 忽略不影响公开 BT 搜索的其它宿主参数。
        :return: 搜索结果列表，失败、停用或超时返回空列表。
        """
        del site
        config = self._config
        keyword = str(keyword or "").strip()
        if config is None or not keyword or (page or 0) > 0:
            return []
        media_type = getattr(mtype, "value", mtype)
        categories = {"电影": [2000], "电视剧": [5000]}.get(media_type, [2000, 5000])
        params = {"query": keyword, "type": "search", "categories": categories, "limit": config.result_limit}
        if config.indexer_ids:
            params["indexerIds"] = list(config.indexer_ids)
        try:
            result = await asyncio.wait_for(self._request(config, params), timeout=SEARCH_TIMEOUT)
            if self._config is not config:
                return []
            self._record("search", keyword, "done" if result else "empty", count=len(result))
            return result
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # 仅记录异常类型，原始异常、响应正文及下载地址可能含 API Key。
            logger.warning(f"PublicBT 搜索失败（{type(exc).__name__}），已跳过此来源。")
            if self._config is config:
                self._record("search", keyword, "error", detail=self._error_code(exc))
            return []

    async def _request(self, config: SearchConfig, params: dict) -> list[TorrentInfo]:
        """
        调用宿主 HTTP 工具，禁止将带凭据的请求重定向到其它地址。
        """
        rows = await self._fetch(config, "/api/v1/search", params)
        if not isinstance(rows, list):
            raise PublicBTError("invalid_response")
        torrents = []
        for row in rows:
            try:
                torrent = self._map_result(row, config)
                if torrent is not None:
                    torrents.append(torrent)
            except (ValueError, TypeError, OverflowError):
                continue
            if len(torrents) >= config.result_limit:
                break
        return torrents

    @staticmethod
    async def _fetch(config, path, params):
        client = AsyncRequestUtils(
            headers={"X-Api-Key": config.api_key, "Accept": "application/json"},
            timeout=SEARCH_TIMEOUT, verify=True, trust_env=False,
        )
        response = await client.get_res(
            config.url + path, params=params, allow_redirects=False,
        )
        if response is None:
            raise PublicBTError("no_response")
        try:
            if response.status_code != 200:
                raise PublicBTError(f"http_{response.status_code}")
            return response.json()
        finally:
            await response.aclose()

    @staticmethod
    def _map_result(row: Any, config: SearchConfig) -> TorrentInfo | None:
        """
        丢弃非 BT 和低做种结果，并映射宿主的种子信息字段。
        """
        if not isinstance(row, dict) or row.get("protocol") != "torrent":
            return None
        if config.indexer_ids and row.get("indexerId") not in config.indexer_ids:
            return None
        title = row.get("title")
        enclosure = row.get("downloadUrl") or row.get("magnetUrl")
        seeders = nonnegative_integer(row.get("seeders") or 0)
        size = nonnegative_integer(row.get("size") or 0)
        if not isinstance(title, str) or not title.strip() or not valid_enclosure(enclosure):
            return None
        if seeders < config.min_seeders or size <= 0:
            return None
        return TorrentInfo(
            title=title, enclosure=enclosure, page_url=row.get("infoUrl"), size=size,
            seeders=seeders, peers=nonnegative_integer(row.get("leechers") or 0),
            grabs=nonnegative_integer(row.get("grabs") or 0),
            pubdate=publication_date(row.get("publishDate")),
            site_name=f"公开BT·{row.get('indexer') or 'Prowlarr'}",
            site_downloader=config.downloader,
            uploadvolumefactor=1.0, downloadvolumefactor=1.0,
        )

    @eventmanager.register(ChainEventType.ResourceDownload)
    def on_resource_download(self, event: Event) -> None:
        """
        拦截会误投默认下载器的请求，并为独立下载器补充专用目录。

        :param event: 宿主资源下载事件，用户明确选择的路径保持优先。
        """
        config = self._config
        if config is None:
            return
        data = getattr(event, "event_data", None)
        requested_downloader = getattr(data, "downloader", None)
        context = getattr(data, "context", None)
        torrent = getattr(context, "torrent_info", None)
        source_name = str(getattr(torrent, "site_name", None) or "")
        if (not source_name.startswith("公开BT·")
                or getattr(torrent, "site_downloader", None) not in (None, "", config.downloader)):
            return
        title = getattr(torrent, "title", None) or "公开 BT 资源"
        if requested_downloader and requested_downloader != config.downloader:
            self._record("route", title, "override", detail="manual_downloader")
            return
        # 宿主在事件前已固化站点下载器，事件无法可靠恢复被手动端点清空的值。
        if not requested_downloader and not getattr(torrent, "site_downloader", None):
            data.cancel = True
            data.source = self.__class__.__name__
            data.reason = f"公开 BT 手动下载请明确选择下载器 {config.downloader}。"
            logger.warning(data.reason)
            self._record("route", title, "blocked", detail="missing_downloader")
            return
        if not config.download_path:
            self._record("route", title, "prepared", detail="host_path")
            return
        options = getattr(data, "options", None)
        if not isinstance(options, dict):
            options = {}
            data.options = options
        if str(options.get("save_path") or "").strip():
            self._record("route", title, "prepared", detail="manual_path")
            return
        options["save_path"] = config.download_path
        data.source = self.__class__.__name__
        data.reason = "已准备公开 BT 专用下载目录，由宿主继续校验和提交。"
        self._record("route", title, "prepared", detail="dedicated_path")

    def get_form(self) -> tuple[list[dict], dict]:
        return settings_form(key_ready=(Path(self.get_data_path()) / "prowlarr.key").is_file())

    def get_api(self) -> list[dict]:
        return [{"path": "/history_page", "endpoint": self.set_history_page, "methods": ["GET"],
                 "auth": "bear", "summary": "Refresh PublicBT activity"},
                {"path": "/test_connection", "endpoint": self.test_connection, "methods": ["GET"],
                 "auth": "bear", "summary": "Test Prowlarr connection"}]

    def get_page(self) -> list[dict]:
        return activity_page(self._settings or DEFAULTS, self._config, self._history,
                             self._connection_status, self._validation_error, self._history_page)

    def set_history_page(self, page: int = 1):
        count = len(self._history.recent()) if self._history is not None else 0
        self._history_page = max(1, min(max(1, (count + 19) // 20), page))
        return {"success": True, "message": "", "data": {"page": self._history_page}}

    def _record(self, kind, title, status, **kwargs):
        if self._history is not None:
            self._history.record(kind, title, status, **kwargs)

    @staticmethod
    def _error_code(error):
        return error.code if isinstance(error, PublicBTError) else type(error).__name__

    async def test_connection(self):
        config = self._config
        if config is None:
            return {"success": False, "message": "请先启用插件并保存有效配置。", "data": {}}
        ok, version, detail = False, "", ""
        try:
            payload = await asyncio.wait_for(self._fetch(config, "/api/v1/system/status", {}), timeout=10)
            version = payload.get("version", "") if isinstance(payload, dict) else ""
            if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+[A-Za-z0-9.+_-]{0,60}", version):
                raise PublicBTError("invalid_response")
            ok = True
        except Exception as error:
            detail = self._error_code(error)
        if self._config is not config:
            return {"success": False, "message": "配置已变更，请重新测试。", "data": {}}
        self._connection_status = {"ok": ok, "version": version if ok else "", "detail": detail,
                                   "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._record("connection", "Prowlarr 连接测试", "done" if ok else "error", detail=detail)
        # The action itself completed; connection health is represented in data.
        return {"success": True, "message": "", "data": {"ok": ok, "version": version if ok else ""}}

    def stop_service(self) -> None:
        """
        清空当前配置，正在进行的旧配置搜索会丢弃结果。
        """
        self._config = None
