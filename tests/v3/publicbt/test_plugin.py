# SPDX-License-Identifier: GPL-3.0-only
"""
验证公开 BT 搜索映射、超时和宿主扩展入口。
"""

import asyncio
import importlib.util
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture(autouse=True)
def host_timezone(monkeypatch):
    """
    将宿主本地时区固定为东京，结束后恢复进程时区。
    """
    try:
        with monkeypatch.context() as scoped:
            scoped.setenv("TZ", "Asia/Tokyo")
            time.tzset()
            yield
    finally:
        time.tzset()


@pytest.fixture(name="module")
def sdk_module(monkeypatch, tmp_path):
    """
    逐例隔离宿主 SDK 替身，测试结束后恢复模块表。
    """
    class PluginBase:
        def get_data_path(self):
            return tmp_path / "plugin-data"

        def update_config(self, config):
            self.saved_config = config
            return True

    exports = {
        "app": {},
        "app.sdk": {},
        "app.sdk.plugin": {"_PluginBase": PluginBase},
        "app.sdk.media": {"TorrentInfo": SimpleNamespace},
        "app.sdk.logging": {"logger": Mock()},
        "app.sdk.network": {"AsyncRequestUtils": Mock()},
        "app.sdk.events": {
            "Event": SimpleNamespace,
            "eventmanager": SimpleNamespace(register=lambda _event: lambda handler: handler),
        },
        "app.schemas": {},
        "app.schemas.types": {
            "ChainEventType": SimpleNamespace(ResourceDownload="resource.download"),
        },
    }
    for name, symbols in exports.items():
        stub = ModuleType(name)
        stub.__dict__.update(symbols)
        monkeypatch.setitem(sys.modules, name, stub)
    path = Path(__file__).resolve().parents[3] / "plugins.v3/publicbt/__init__.py"
    spec = importlib.util.spec_from_file_location("publicbt_under_test", path)
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, loaded)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture(name="plugin")
def configured_plugin(module):
    """
    用无真实凭据的有效配置初始化插件。
    """
    instance = module.PublicBT()
    instance.init_plugin({
        "enabled": True,
        "prowlarr_url": "http://prowlarr.invalid:9696/base/",
        "api_key": "test-key",
        "indexer_ids": "1,2,3",
        "downloader": "qb-public",
        "min_seeders": 1,
        "download_path": "/downloads/bt",
    })
    return instance


def response(module, rows, status=200):
    """
    替换异步 HTTP 边界并返回可校验关闭行为的响应。
    """
    result = Mock(status_code=status)
    result.json.return_value = rows
    result.aclose = AsyncMock()
    catalog = Mock(status_code=200)
    catalog.json.return_value = [
        {"id": identifier, "privacy": "public", "protocol": "torrent", "enable": True}
        for identifier in (1, 2, 3)
    ]
    catalog.aclose = AsyncMock()
    async def get_response(url, **kwargs):
        return catalog if url.endswith("/api/v1/indexer") else result
    module.AsyncRequestUtils.return_value.get_res = AsyncMock(side_effect=get_response)
    return result


def release(**changes):
    """
    构造覆盖 Prowlarr 必需字段的固定结果。
    """
    row = {
        "protocol": "torrent", "indexerId": 2, "indexer": "YTS",
        "title": "Example.2026.1080p", "size": 1024,
        "downloadUrl": "http://prowlarr.invalid/download?apikey=test-key",
        "magnetUrl": "magnet:?xt=urn:btih:test",
        "infoUrl": "https://example.invalid/details",
        "seeders": 12, "leechers": 3, "grabs": 10,
        "publishDate": "2026-11-01T14:30:00Z",
    }
    row.update(changes)
    return row


def test_sync_entry_maps_fields_and_restricts_request(module, plugin):
    """
    同步入口保留种子文件、宿主日期和专用下载器。
    """
    result = response(module, [release()])
    torrents = plugin.search_torrents(
        site=None, keyword="Example", mtype=SimpleNamespace(value="电影")
    )
    torrent = torrents[0]
    assert torrent.enclosure == release()["downloadUrl"]
    assert torrent.page_url == release()["infoUrl"]
    assert (torrent.size, torrent.seeders, torrent.peers, torrent.grabs) == (1024, 12, 3, 10)
    assert torrent.pubdate == "2026-11-01 23:30:00"
    assert torrent.site_name == "公开BT·YTS"
    assert torrent.site_downloader == "qb-public"
    assert torrent.uploadvolumefactor == torrent.downloadvolumefactor == 1.0
    client = module.AsyncRequestUtils
    assert client.call_args.kwargs["headers"]["X-Api-Key"] == "test-key"
    call = client.return_value.get_res.call_args
    assert call.args[0] == "http://prowlarr.invalid:9696/base/api/v1/search"
    assert call.kwargs["params"]["categories"] == [2000]
    assert call.kwargs["params"]["indexerIds"] == [1, 2, 3]
    assert call.kwargs["allow_redirects"] is False
    result.aclose.assert_awaited_once()


def test_async_entry_filters_bad_rows_and_keeps_valid_rows(module, plugin):
    """
    错误条目不会使同批的有效公开种子消失。
    """
    response(module, [
        release(protocol="usenet"), release(seeders=0), release(seeders=None),
        release(size="not-a-number"), None, release(title=""),
        release(downloadUrl=None, magnetUrl="javascript:bad"),
        release(indexerId=99),
        release(downloadUrl=None, publishDate="invalid"),
    ])
    torrents = asyncio.run(plugin.async_search_torrents(
        site=None, keyword="Example", mtype=SimpleNamespace(value="电视剧")
    ))
    assert len(torrents) == 1
    assert torrents[0].enclosure == release()["magnetUrl"]
    assert torrents[0].pubdate is None
    params = module.AsyncRequestUtils.return_value.get_res.call_args.kwargs["params"]
    assert params["categories"] == [5000]


@pytest.mark.parametrize("page,keyword", [(1, "Example"), (None, " "), (0, None)])
def test_empty_query_or_later_page_skips_network(module, plugin, page, keyword):
    """
    空关键词和后续分页不产生外部请求。
    """
    assert plugin.search_torrents(site=None, keyword=keyword, page=page) == []
    module.AsyncRequestUtils.assert_not_called()


@pytest.mark.parametrize("status", [302, 401, 500])
def test_http_failure_is_isolated_and_closed(module, plugin, status):
    """
    重定向和 HTTP 失败均返回空列表并释放响应。
    """
    result = response(module, [], status=status)
    assert plugin.search_torrents(site=None, keyword="Example") == []
    result.aclose.assert_awaited_once()
    assert "test-key" not in str(module.logger.mock_calls)


@pytest.mark.parametrize("payload", [{"error": "test-key"}, "invalid"])
def test_unexpected_json_shape_returns_empty(module, plugin, payload):
    """
    API 错误对象不能作为搜索结果或日志内容泄漏。
    """
    response(module, payload)
    assert plugin.search_torrents(site=None, keyword="Example") == []
    assert "test-key" not in str(module.logger.mock_calls)


def test_deadline_cancels_request_without_logging_secrets(module, plugin, monkeypatch):
    """
    总超时取消请求，并避免将异常中的凭据写入日志。
    """
    async def slow_request(*_args, **_kwargs):
        """
        模拟无响应的 Prowlarr。
        """
        await asyncio.sleep(10)

    monkeypatch.setattr(module, "SEARCH_TIMEOUT", 0.01)
    module.AsyncRequestUtils.return_value.get_res = slow_request
    assert asyncio.run(plugin.async_search_torrents(site=None, keyword="Example")) == []
    assert "test-key" not in str(module.logger.mock_calls)


def test_reinitialization_clears_previous_config(module, plugin):
    """
    清空配置后禁用模块扩展，避免沿用旧凭据。
    """
    plugin.init_plugin(None)
    assert plugin.get_state() is False
    assert plugin.get_module() == {}
    assert plugin.search_torrents(site=None, keyword="Example") == []
    module.AsyncRequestUtils.assert_not_called()


@pytest.mark.parametrize("change", [
    {"prowlarr_url": "http://user:password@example.invalid"},
    {"prowlarr_url": "http://example.invalid?apikey=test-key"},
    {"api_key": ""}, {"min_seeders": -1}, {"indexer_ids": "1,bad"},
])
def test_invalid_config_fails_closed(module, change):
    """
    无效地址或过滤配置不退回无限制搜索。
    """
    plugin = module.PublicBT()
    config = {
        "enabled": True, "prowlarr_url": "http://prowlarr.invalid",
        "api_key": "test-key", "min_seeders": 1, "indexer_ids": "1",
    }
    plugin.init_plugin({**config, **change})
    assert plugin.get_state() is False
    assert "test-key" not in str(module.logger.mock_calls)


def test_unknown_type_searches_both_categories_with_no_indexer_restriction(module, plugin):
    """
    未配置索引站时沿用 Prowlarr，未知媒体类型检索电影和电视。
    """
    plugin.init_plugin({
        "enabled": True, "prowlarr_url": "http://prowlarr.invalid",
        "api_key": "test-key", "min_seeders": 0,
    })
    response(module, [release(seeders=0)])
    assert len(plugin.search_torrents(site=None, keyword="Example")) == 1
    params = module.AsyncRequestUtils.return_value.get_res.call_args.kwargs["params"]
    assert params["categories"] == [2000, 5000]
    assert params["indexerIds"] == [1, 2, 3]


def test_sync_entry_can_be_called_from_an_event_loop(module, plugin):
    """
    同步合同在已有事件循环的调用方也能完成。
    """
    response(module, [release()])

    async def invoke():
        """
        模拟在异步调用方中使用同步合同。
        """
        return plugin.search_torrents(site=None, keyword="Example")

    assert len(asyncio.run(invoke())) == 1


def test_reloading_during_search_discards_stale_results(module, plugin):
    """
    停用发生在请求期间时，不再向宿主返回旧配置的结果。
    """
    result = response(module, [release()])

    async def disable_before_response(*_args, **_kwargs):
        """
        在 HTTP 响应完成前模拟插件停用。
        """
        plugin.stop_service()
        return result

    module.AsyncRequestUtils.return_value.get_res = disable_before_response
    assert asyncio.run(plugin.async_search_torrents(site=None, keyword="Example")) == []
    result.aclose.assert_awaited_once()


@pytest.mark.parametrize("value", ["2026-11-01T14:30:00Z", "2026-11-01T09:30:00-05:00"])
def test_publish_time_matches_host_local_clock(module, value):
    """
    不同来源时区的同一时刻都匹配宿主的本地时钟。
    """
    assert module.publication_date(value) == "2026-11-01 23:30:00"


def test_download_event_sets_public_bt_save_path(plugin):
    """
    公开 BT 默认使用专用下载目录，并保留事件中的其它选项。
    """
    data = SimpleNamespace(
        context=SimpleNamespace(torrent_info=SimpleNamespace(
            site_name="公开BT·Nyaa.si", site_downloader="qb-public"
        )),
        options={"save_path": None, "userid": 1},
    )
    plugin.on_resource_download(SimpleNamespace(event_data=data))
    assert data.options == {"save_path": "/downloads/bt", "userid": 1}
    assert data.source == "PublicBT"


@pytest.mark.parametrize("site_name,downloader", [
    ("PrivateTracker", "qb-public"), ("公开BT·Nyaa.si", "another-downloader"),
])
def test_download_event_leaves_other_sources_unchanged(plugin, site_name, downloader):
    """
    事件不会改写其它来源或其它独立下载器的任务。
    """
    data = SimpleNamespace(
        context=SimpleNamespace(torrent_info=SimpleNamespace(
            site_name=site_name, site_downloader=downloader
        )),
        options={"save_path": None},
    )
    plugin.on_resource_download(SimpleNamespace(event_data=data))
    assert data.options == {"save_path": None}
    assert not hasattr(data, "source")


def test_download_event_preserves_explicit_save_path(plugin):
    """
    宿主明确传入的保存路径优先于插件的默认目录。
    """
    data = SimpleNamespace(
        context=SimpleNamespace(torrent_info=SimpleNamespace(
            site_name="公开BT·Nyaa.si", site_downloader="qb-public"
        )),
        options={"save_path": "/chosen/by/user"},
    )
    plugin.on_resource_download(SimpleNamespace(event_data=data))
    assert data.options["save_path"] == "/chosen/by/user"


def test_download_event_preserves_manual_downloader_override(plugin):
    """
    手动选择其它下载器时，不把公开 BT 专用目录强加给该下载器。
    """
    data = SimpleNamespace(
        context=SimpleNamespace(torrent_info=SimpleNamespace(
            site_name="公开BT·Nyaa.si", site_downloader="qb-public"
        )),
        downloader="PT-downloader",
        options={"save_path": None},
    )
    plugin.on_resource_download(SimpleNamespace(event_data=data))
    assert data.options["save_path"] is None


def test_download_event_is_inert_when_disabled(plugin):
    """
    停用插件后保留任务原始目录。
    """
    plugin.stop_service()
    plugin.on_resource_download(SimpleNamespace(event_data=SimpleNamespace()))


def test_relative_download_path_disables_config(module):
    """
    指定下载目录时拒绝相对路径，避免宿主与下载器解释不一致。
    """
    plugin = module.PublicBT()
    plugin.init_plugin({
        "enabled": True, "prowlarr_url": "http://prowlarr.invalid",
        "api_key": "test-key", "download_path": "../bt",
    })
    assert not plugin.get_state()


@pytest.mark.parametrize("save_path", [None, "/chosen/by/user"])
def test_manual_default_cannot_fall_back_to_pt_downloader(plugin, save_path):
    """
    宿主手动端点清空站点下载器时取消任务，避免回落到 PT 下载器。
    """
    torrent = SimpleNamespace(site_name="公开BT·YTS", site_downloader=None)
    data = SimpleNamespace(
        context=SimpleNamespace(torrent_info=torrent), downloader=None,
        options={"save_path": save_path},
    )
    plugin.on_resource_download(SimpleNamespace(event_data=data))
    assert data.cancel is True
    assert "qb-public" in data.reason
    assert data.options["save_path"] == save_path


def test_key_is_private_and_blank_settings_keep_it(module, plugin):
    path = plugin.get_data_path() / "prowlarr.key"
    assert path.read_text() == "test-key"
    assert path.stat().st_mode & 0o777 == 0o600
    assert plugin.saved_config["api_key"] == ""
    plugin.init_plugin({**plugin.saved_config, "min_seeders": 2})
    assert plugin.get_state()
    assert plugin._config.api_key == "test-key"
    assert plugin._config.min_seeders == 2


def test_search_records_outcome_without_sensitive_download_url(module, plugin):
    response(module, [release()])
    assert len(plugin.search_torrents(keyword="Example")) == 1
    row, = plugin._history.recent()
    assert (row["kind"], row["status"], row["count"]) == ("search", "done", 1)
    assert "test-key" not in str(row)
    assert "download?" not in str(row)


def test_history_distinguishes_failed_search_from_zero_results(module, plugin):
    response(module, [], status=401)
    plugin.search_torrents(keyword="Example")
    assert plugin._history.recent()[0]["status"] == "error"
    response(module, [])
    plugin.search_torrents(keyword="Nothing")
    assert plugin._history.recent()[0]["status"] == "empty"


def test_route_history_is_handoff_not_download_success(plugin):
    data = SimpleNamespace(context=SimpleNamespace(torrent_info=SimpleNamespace(
        site_name="公开BT·YTS", site_downloader="qb-public", title="Example")),
        options={"save_path": None})
    plugin.on_resource_download(SimpleNamespace(event_data=data))
    row, = plugin._history.recent()
    assert (row["kind"], row["status"]) == ("route", "prepared")
    assert "test-key" not in str(row)


def test_connection_test_and_page_actions_use_web_contract(module, plugin):
    result = response(module, {"version": "2.3.0"})
    payload = asyncio.run(plugin.test_connection())
    assert set(payload) == {"success", "message", "data"}
    assert payload["success"] is True
    assert payload["data"]["version"] == "2.3.0"
    call = module.AsyncRequestUtils.return_value.get_res.call_args
    assert call.args[0].endswith("/api/v1/system/status")
    result.aclose.assert_awaited_once()
    for route in plugin.get_api():
        assert route["auth"] == "bear"
        assert not route.get("allow_anonymous")
    assert set(plugin.set_history_page(100)) == {"success", "message", "data"}


def test_result_limit_bounds_returned_results(module, plugin):
    plugin.init_plugin({**plugin.saved_config, "result_limit": 1})
    response(module, [release(), release(title="Other")])
    assert len(plugin.search_torrents(keyword="Example")) == 1
    assert module.AsyncRequestUtils.return_value.get_res.call_args.kwargs["params"]["limit"] == 1


def test_history_is_private_bounded_and_retained_when_disabled(module, plugin):
    history = plugin._history
    history.limit = 2
    for title in ["Old", "Second", "Newest https://example.invalid/?apikey=test-key api_key=secret"]:
        history.record("search", title, "done")
    rows = history.recent()
    assert len(rows) == 2
    assert "test-key" not in str(rows)
    assert "secret" not in str(rows)
    assert history.path.stat().st_mode & 0o777 == 0o600
    plugin.init_plugin({**plugin.saved_config, "history_enabled": False})
    plugin._history.record("search", "Not recorded", "done")
    assert len(plugin._history.recent()) == 2


def test_insecure_private_key_file_is_rejected_without_logging_it(module, plugin):
    (plugin.get_data_path() / "prowlarr.key").chmod(0o644)
    plugin.init_plugin(plugin.saved_config)
    assert not plugin.get_state()
    assert "test-key" not in str(module.logger.mock_calls)


def test_page_has_explicit_native_rows_and_actions_without_secrets(module, plugin):
    response(module, [release()])
    plugin.search_torrents(keyword="Example")

    def nodes(items):
        for item in items:
            yield item
            yield from nodes(item.get("content", []))

    page = plugin.get_page()
    rendered = list(nodes(page))
    assert "test-key" not in str(page)
    assert any(x["component"] == "VTable" for x in rendered)
    assert any(x["component"] == "td" and x.get("text") == "Example" for x in rendered)
    assert any(x.get("text") == "测试连接" and x.get("events") for x in rendered)


def test_private_and_unknown_indexers_cannot_enter_public_download_route(module, plugin):
    plugin._fetch = AsyncMock(side_effect=[
        [{"id": 2, "privacy": "public", "protocol": "torrent", "enable": True},
         {"id": 3, "privacy": "private", "protocol": "torrent", "enable": True}],
        [release(indexerId=3), release(indexerId=99), release(indexerId=2)],
    ])
    results = asyncio.run(plugin.async_search_torrents(keyword="Example"))
    assert len(results) == 1
    assert plugin._fetch.call_args.args[2]["indexerIds"] == [2]


def test_no_public_indexers_does_not_issue_search(module, plugin):
    plugin._fetch = AsyncMock(return_value=[
        {"id": 2, "privacy": "private", "protocol": "torrent", "enable": True},
    ])
    assert asyncio.run(plugin.async_search_torrents(keyword="Example")) == []
    assert plugin._fetch.await_count == 1
