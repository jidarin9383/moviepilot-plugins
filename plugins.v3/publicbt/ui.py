# SPDX-License-Identifier: GPL-3.0-only
"""Roomy native configuration and an explicit-row activity page."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .activity import clean_text


DEFAULTS = {"enabled": False, "prowlarr_url": "", "api_key": "", "indexer_ids": "",
            "downloader": "qb-public", "download_path": "", "min_seeders": 1,
            "result_limit": 100, "history_enabled": True, "history_limit": 1000}


def node(name, *, text=None, content=None, **props):
    result = {"component": name}
    if props:
        result["props"] = props
    if text is not None:
        result["text"] = text
    if content is not None:
        result["content"] = content
    return result


def field(model, label, *, switch=False, wide=False, hint="", **props):
    props.update(model=model, label=label, color="primary")
    if switch:
        props.update(inset=True, **{"hide-details": True, "class": "my-1"})
    else:
        props.update(variant="outlined", density="comfortable", **{"hide-details": "auto"})
        if hint:
            props.update(hint=hint, **{"persistent-hint": True})
    return node("VCol", cols=12, md=12 if wide else 6, content=[
        node("VSwitch" if switch else "VTextField", **props)])


def section(title, subtitle, content):
    return node("VCard", variant="outlined", rounded="lg", **{"class": "mb-6"}, content=[
        node("VCardTitle", text=title, **{"class": "pt-5 px-6 text-h6"}),
        node("VCardSubtitle", text=subtitle, **{"class": "px-6 text-wrap"}),
        node("VCardText", **{"class": "pa-6"}, content=[node("VRow", content=content)])])


def settings_form(*, key_ready=False):
    return [node("VForm", content=[
        section("Prowlarr 连接", "聚合公开索引站，搜索结果进入 MoviePilot 原生资源列表。", [
            field("enabled", "启用公开 BT 搜索", switch=True, wide=True),
            field("prowlarr_url", "Prowlarr 地址", wide=True, hint="填写 NAS 可访问的完整地址，支持部署路径前缀。"),
            field("api_key", "API Key（留空保留已保存的 Key）", wide=True, type="password", autocomplete="off",
                  hint="Key 已私密保存；需要替换时输入新值。" if key_ready else "首次配置请填写 Prowlarr API Key。"),
        ]),
        section("搜索规则", "搜索最多等待 20 秒；单个来源失败不会阻断其他来源。", [
            field("indexer_ids", "公开索引站 ID", wide=True,
                  hint="用英文逗号分隔；留空搜索 Prowlarr 中全部已配置索引站。请仅添加公开站。"),
            field("min_seeders", "最低做种数", type="number", min=0,
                  hint="做种数低于此值的结果不显示。"),
            field("result_limit", "单次最多返回结果", type="number", min=1, max=200,
                  hint="范围 1–200，默认 100 条。"),
        ]),
        section("下载路由", "使用公开 BT 专用下载器和目录，整理规则由 MoviePilot 管理。", [
            field("downloader", "独立下载器名称", wide=True, hint="必须与 MoviePilot 中配置的名称完全一致，例如 qb-public。"),
            field("download_path", "BT 下载目录", wide=True,
                  hint="使用 NAS 绝对路径，并先在 MoviePilot 目录设置中配置。留空沿用宿主目录。"),
            node("VCol", cols=12, content=[node("VAlert", variant="tonal", type="info",
                text="手动下载时请选择公开 BT 专用下载器。你明确选择的其他下载器和目录会被保留；未明确选择且无法可靠路由时会阻止提交。")]),
        ]),
        section("使用记录", "记录搜索结果、连接测试和下载交接，默认保留最近 1,000 条。", [
            field("history_enabled", "记录使用过程", switch=True),
            field("history_limit", "最多保留记录数", type="number", min=1, max=5000,
                  hint="最多 5,000 条；关闭记录不会删除已有记录。"),
        ]),
    ])], dict(DEFAULTS)


DETAILS = {"no_response": "Prowlarr 未响应", "invalid_response": "响应格式不正确",
           "missing_downloader": "请明确选择公开 BT 专用下载器", "dedicated_path": "已准备专用目录，等待宿主提交",
           "manual_path": "保留手动目录，等待宿主提交", "host_path": "使用宿主目录，等待宿主提交",
           "manual_downloader": "已保留手动选择的其他下载器"}
STATUS = {"done": "完成", "empty": "无结果", "error": "失败", "prepared": "准备交接",
          "blocked": "已阻止提交", "override": "保留手动选择"}


def action(text, path, params=None, **props):
    return {**node("VBtn", text=text, size="small", variant="tonal", **props),
            "events": {"click": {"api": "plugin/PublicBT/" + path, "method": "GET", "params": params or {}}}}


def activity_page(settings, config, history, connection, validation_error, page=1):
    events = history.recent() if history is not None else []
    pages = max(1, (len(events) + 19) // 20)
    page = max(1, min(pages, int(page)))
    state = "运行中" if config is not None else "配置有误" if validation_error else "已停用"
    stats = [("插件状态", state), ("独立下载器", config.downloader if config else "—"),
             ("最低做种数", str(config.min_seeders) if config else "—"), ("保留记录", str(len(events)))]
    cards = [node("VCol", cols=12, sm=6, md=3, content=[node("VCard", variant="tonal", rounded="lg",
        **{"class": "pa-5 h-100"}, content=[
            node("div", text=title, **{"class": "text-body-2 text-medium-emphasis mb-3"}),
            node("div", text=value, **{"class": "text-h6"})])]) for title, value in stats]
    health = "尚未测试 Prowlarr 连接。"
    color = "info"
    if connection:
        timestamp = datetime.fromisoformat(connection["time"]).astimezone(ZoneInfo("Asia/Taipei")).strftime("%m-%d %H:%M:%S")
        health = (f"Prowlarr 连接与认证正常 · v{connection['version']} · {timestamp}" if connection["ok"] else
                  f"Prowlarr 连接测试失败 · {DETAILS.get(connection['detail'], connection['detail'])} · {timestamp}")
        color = "success" if connection["ok"] else "warning"
    columns = [("time", "时间"), ("kind", "类型"), ("title", "关键词 / 资源"), ("status", "结果"), ("detail", "说明")]
    body = []
    for event in events[(page - 1) * 20:page * 20]:
        local = datetime.fromisoformat(event["time"]).astimezone(ZoneInfo("Asia/Taipei"))
        detail = (f"{event['count']} 条可用结果" if event["kind"] == "search" and event["status"] != "error" else
                  DETAILS.get(event["detail"], event["detail"]))
        row = {"time": local.strftime("%m-%d %H:%M:%S"), "kind": {"search": "搜索", "route": "下载交接", "connection": "连接测试"}.get(event["kind"], "操作"),
               "title": event["title"], "status": STATUS.get(event["status"], event["status"]), "detail": detail or "—"}
        cells = []
        for key, _ in columns:
            if key == "status":
                tint = "warning" if event["status"] in {"blocked", "error"} else "success" if event["status"] == "done" else "primary"
                cells.append(node("td", content=[node("VChip", text=row[key], size="small", color=tint, variant="tonal", **{"class": "text-no-wrap"})]))
            else:
                cells.append(node("td", text=clean_text(row[key]), style=("min-width:180px;max-width:320px;overflow-wrap:anywhere;white-space:normal;"
                    if key in {"title", "detail"} else "white-space:nowrap;")))
        body.append(node("tr", content=cells))
    if not body:
        body = [node("tr", content=[node("td", text="暂无使用记录，搜索或测试连接后会显示。", colspan=5,
                                             **{"class": "text-center text-medium-emphasis py-8"})])]
    return [node("VContainer", fluid=True, **{"class": "pa-1"}, content=[
        node("div", text="公开 BT · 搜索与交接", **{"class": "text-h5 font-weight-bold mb-2"}),
        node("div", text="Prowlarr 搜索 → 独立下载器 → NAS 下载目录 → MoviePilot 整理", **{"class": "text-body-2 text-medium-emphasis mb-6"}),
        node("VRow", content=cards),
        *([node("VAlert", text=validation_error, type="warning", variant="tonal", **{"class": "mt-5"})] if validation_error else []),
        node("VCard", variant="outlined", rounded="lg", **{"class": "mt-6 pa-5"}, content=[
            node("VAlert", text=health, variant="tonal", type=color),
            node("div", text=(f"服务地址：{config.url} · 索引站：{', '.join(map(str, config.indexer_ids)) or '全部已配置索引站'}" if config else "请在设置中启用插件并保存有效配置。"),
                 **{"class": "text-body-2 text-medium-emphasis mt-4 text-wrap"}),
            node("div", text="连接测试仅检查服务与认证，站点可用性以实际搜索为准。", **{"class": "text-caption text-medium-emphasis mt-2"}),
            node("div", **{"class": "mt-4"}, content=[action("测试连接", "test_connection", disabled=config is None)]),
        ]),
        node("VAlert", variant="tonal", type="info", **{"class": "mt-5"},
             text="下载交接记录只表示已准备或阻止提交，不代表下载已开始或已完成。实际下载进度请查看 MoviePilot 下载管理。"),
        *([node("VAlert", variant="tonal", type="warning", text="使用记录暂时无法读写，请检查 NAS 可用空间和目录权限。", **{"class": "mt-4"})]
          if history is None or history.failed else []),
        node("VCard", variant="outlined", rounded="lg", **{"class": "mt-6"}, content=[
            node("VCardTitle", text="使用记录", **{"class": "pt-5 px-6"}),
            node("VCardSubtitle", text=f"记录{'已开启' if settings.get('history_enabled', True) else '已暂停'} · 时区 UTC+08:00 · 最多保留 {settings.get('history_limit', 1000)} 条",
                 **{"class": "px-6 text-wrap"}),
            node("VCardText", **{"class": "pa-4 overflow-x-auto"}, content=[node("VTable", density="comfortable", height=min(520, 58 * (len(body) + 1)),
                **{"fixed-header": True}, content=[node("thead", content=[node("tr", content=[node("th", text=label, **{"class": "text-no-wrap"}) for _, label in columns])]),
                                                node("tbody", content=body)])]),
        ]),
        node("div", **{"class": "d-flex align-center justify-end flex-wrap ga-3 mt-4"}, content=[
            action("刷新", "history_page", {"page": page}),
            node("span", text=f"共 {len(events)} 条 · 第 {page} / {pages} 页", **{"class": "text-body-2 text-medium-emphasis"}),
            action("上一页", "history_page", {"page": page - 1}, disabled=page <= 1),
            action("下一页", "history_page", {"page": page + 1}, disabled=page >= pages),
        ]),
    ])]
