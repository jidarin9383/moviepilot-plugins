# SPDX-License-Identifier: GPL-3.0-only
"""Native MoviePilot JSON views for CloudShare settings and activity."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .history import clean_text


STATE_NAMES = {
    "pending": "排队中", "running": "正在转存", "saved": "已转存",
    "paused": "已暂停", "waiting": "等待处理", "failed": "执行失败",
    "review": "待人工确认", "completed": "文件就绪", "transferred": "已整理",
    "removed": "已移除", "done": "搜索完成", "empty": "无可用结果", "error": "搜索失败",
}
PROVIDERS = {"quark": "夸克", "aliyun": "阿里云盘"}


def defaults(root):
    return {
        "quark_enabled": True, "aliyun_enabled": True,
        "desktop_enabled": False, "aliyun_auto": False,
        "telegram_enabled": True, "telegram_quark_enabled": True, "telegram_aliyun_enabled": True,
        "telegram_quark_bot": "QuarkRobot", "telegram_aliyun_bot": "AliYunPanBot",
        "telegram_proxy": "",
        "history_enabled": True, "history_limit": 1000,
        "desktop_staging": str(root / "cloudshare-staging"),
        "desktop_movies": str(root / "quark-inbox"), "desktop_tv": str(root / "quark-tv-inbox"),
    }


def component(name, *, text=None, content=None, **props):
    node = {"component": name}
    if props:
        node["props"] = props
    if text is not None:
        node["text"] = text
    if content is not None:
        node["content"] = content
    return node


def field(model, label, *, switch=False, hint="", wide=False, **props):
    props.update(model=model, label=label, color="primary")
    if switch:
        props.update(inset=True, **{"hide-details": True, "class": "my-1"})
    else:
        props.update(variant="outlined", density="comfortable", **{"hide-details": "auto"})
        if hint:
            props.update(hint=hint, **{"persistent-hint": True})
    return component("VCol", cols=12, md=12 if wide else 6, content=[
        component("VSwitch" if switch else "VTextField", **props)])


def section(title, subtitle, fields):
    return component("VCard", variant="outlined", rounded="lg", **{"class": "mb-6"}, content=[
        component("VCardTitle", text=title, **{"class": "pt-5 px-6 text-h6"}),
        component("VCardSubtitle", text=subtitle, **{"class": "px-6 text-wrap"}),
        component("VCardText", **{"class": "pa-6"}, content=[component("VRow", content=fields)]),
    ])


def settings_form(root, *, telegram_ready=False):
    return [component("VForm", content=[
        section("资源渠道", "分别控制新搜索和新任务，已有任务继续处理。", [
            field("quark_enabled", "启用夸克", switch=True),
            field("aliyun_enabled", "启用阿里云盘", switch=True),
        ]),
        section("Telegram 搜索", "与 PanSou 合并结果，去重后验证网盘文件。", [
            component("VCol", cols=12, content=[component("VAlert", variant="tonal",
                type="success" if telegram_ready else "warning",
                text=("专用登录配置已保存，凭据不在页面回显。" if telegram_ready else
                      "尚未配置专用 Telegram 登录，请先完成 NAS 登录向导。"))]),
            field("telegram_enabled", "启用 Telegram 搜索", switch=True, wide=True),
            field("telegram_quark_enabled", "查询夸克机器人", switch=True),
            field("telegram_aliyun_enabled", "查询阿里云盘机器人", switch=True),
            field("telegram_quark_bot", "夸克 Bot 用户名", hint="例如 QuarkRobot，可省略 @。"),
            field("telegram_aliyun_bot", "阿里云盘 Bot 用户名", hint="例如 AliYunPanBot，可省略 @。"),
            field("telegram_proxy", "Telegram 代理", wide=True,
                  hint="使用 NAS 可访问的 HTTP / SOCKS5 地址；留空表示直连，请勿填写带账号密码的地址。"),
        ]),
        section("下载与入库", "官方客户端直接写入 NAS，文件完整后交给 MoviePilot 整理。", [
            field("desktop_enabled", "启用桌面客户端 SMB 入库", switch=True, wide=True),
            field("aliyun_auto", "阿里云盘自动转存并派发", switch=True, wide=True),
            component("VCol", cols=12, content=[component("VAlert", variant="tonal", type="info",
                text="Mac 需保持唤醒并登录，SMB 共享需挂载。转存完成不代表已下载或已入库。")]),
            field("desktop_staging", "NAS 下载暂存目录", wide=True),
            field("desktop_movies", "电影监控目录"),
            field("desktop_tv", "剧集监控目录"),
        ]),
        section("使用记录", "在插件的「查看数据」中浏览。关闭记录不会删除已有记录。", [
            field("history_enabled", "记录搜索和任务过程", switch=True),
            field("history_limit", "最多保留记录数", type="number", min=1, max=5000,
                  hint="默认 1,000 条，最多 5,000 条；超过数量后淘汰最早记录。"),
        ]),
    ])], defaults(root)


def activity_page(settings, history, jobs, progress, *, telegram_ready=False, page=1):
    events = history.recent() if history is not None else []
    total_pages = max(1, (len(events) + 19) // 20)
    page = max(1, min(total_pages, int(page)))
    telegram_label = ("已关闭" if not settings.get("telegram_enabled", True) else
                      "配置已保存" if telegram_ready else "未配置")
    active = sum(job["state"] in {"pending", "running", "saved"} for job in jobs.values())
    attention = sum(job["state"] in {"failed", "waiting", "review"} for job in jobs.values())
    stats = [("资源渠道", " / ".join(PROVIDERS[p] for p in PROVIDERS if settings.get(p + "_enabled", True)) or "全部关闭"),
             ("处理中", str(active)), ("需要关注", str(attention)), ("保留记录", str(len(events)))]
    cards = [component("VCol", cols=12, sm=6, md=3, content=[
        component("VCard", variant="tonal", rounded="lg", **{"class": "pa-5 h-100"}, content=[
            component("div", text=label, **{"class": "text-body-2 text-medium-emphasis mb-3"}),
            component("div", text=value, **{"class": "text-h6"}),
        ])]) for label, value in stats]
    rows = []
    for event in events[(page - 1) * 20:page * 20]:
        local = datetime.fromisoformat(event["time"]).astimezone(ZoneInfo("Asia/Taipei"))
        detail = ("首次启用记录时的状态，原始时间未记录。" if event["kind"] == "snapshot" else
                  f"{event['count']} 个可用结果" if event["kind"] == "search" else
                  event["detail"] or ("等待客户端下载到 NAS" if event["status"] == "saved" else ""))
        rows.append({"time": local.strftime("%m-%d %H:%M:%S"), "provider": PROVIDERS.get(event["provider"], "—"),
                     "kind": {"snapshot": "历史任务快照", "task": "任务", "search": "搜索"}.get(event["kind"], "操作"),
                     "title": event["title"], "state": STATE_NAMES.get(event["status"], event["status"]),
                     "detail": detail})
    tasks = []
    for key, job in jobs.items():
        size = sum(file.get("size", 0) for file in job.get("files", ()))
        received = (progress or {}).get(key, (0, 0, 0))[1]
        percent = (100 if job["state"] in {"completed", "transferred"} else
                   min(99.9, received / size * 100) if size else 0)
        tasks.append({"title": clean_text(job.get("keyword", "")),
                      "provider": PROVIDERS.get(job.get("provider", "quark"), "—"),
                      "state": STATE_NAMES.get(job["state"], job["state"]),
                      "progress": f"{percent:.1f}%", "files": len(job.get("files", ()))})
    def table(title, subtitle, columns, items):
        header = component("thead", content=[component("tr", content=[
            component("th", text=label, **{"class": "text-no-wrap font-weight-medium"})
            for _, label in columns])])
        body = []
        for item in items:
            cells = []
            for key, _ in columns:
                value = str(item.get(key, ""))
                if key == "state":
                    color = ("success" if value in {"已整理", "文件就绪", "搜索完成"} else
                             "warning" if value in {"待人工确认", "等待处理", "执行失败", "搜索失败"} else "primary")
                    cell = component("td", content=[component("VChip", text=value,
                        color=color, size="small", variant="tonal", **{"class": "text-no-wrap"})])
                else:
                    cell = component("td", text=value or "—", style=(
                        "min-width:180px;max-width:300px;overflow-wrap:anywhere;white-space:normal;"
                        if key in {"title", "detail"} else "white-space:nowrap;"))
                cells.append(cell)
            body.append(component("tr", content=cells))
        if not body:
            body = [component("tr", content=[component("td", text="暂无记录", colspan=len(columns),
                                                        **{"class": "text-center text-medium-emphasis py-8"})])]
        return component("VCard", variant="outlined", rounded="lg", **{"class": "mt-6"}, content=[
            component("VCardTitle", text=title, **{"class": "px-6 pt-5"}),
            component("VCardSubtitle", text=subtitle, **{"class": "px-6 text-wrap"}),
            component("VCardText", **{"class": "pa-4 overflow-x-auto"}, content=[
                component("VTable", density="comfortable", height=min(520, 58 * (len(items) + 1)),
                          **{"fixed-header": True}, content=[header, component("tbody", content=body)])]),
        ])
    return [component("VContainer", fluid=True, **{"class": "pa-1"}, content=[
        component("div", text="CloudShare · 网盘资源", **{"class": "text-h5 font-weight-bold mb-2"}),
        component("div", text="搜索 → 转存 → 客户端写入 NAS → MoviePilot 整理",
                  **{"class": "text-body-2 text-medium-emphasis mb-6"}),
        component("VRow", content=cards),
        component("VAlert", variant="tonal", type="info", **{"class": "mt-5"},
                  text=(f"Telegram：{telegram_label} · "
                        f"使用记录：{'开启' if settings.get('history_enabled', True) else '已暂停'}。点击下方「刷新」更新数据。")),
        *([component("VAlert", type="warning", variant="tonal", **{"class": "mt-4"},
                     text="使用记录暂时无法读写，下载流程继续运行。请检查 NAS 可用空间和目录权限。")]
          if history is None or history.failed else []),
        table("当前任务", "下载进度以 NAS 已收到的文件为准，已转存不会被当作下载完成。",
              [("title", "资源"), ("provider", "渠道"), ("state", "状态"), ("progress", "NAS 进度"), ("files", "文件数")], tasks),
        table("使用记录", f"按发生时间倒序 · 时区 UTC+08:00 · 最多保留 {settings.get('history_limit', 1000)} 条",
              [("time", "时间"), ("provider", "渠道"), ("kind", "类型"), ("title", "资源 / 关键词"),
               ("state", "结果"), ("detail", "说明")], rows),
        component("div", **{"class": "d-flex align-center justify-end ga-3 mt-4"}, content=[
            {**component("VBtn", text="刷新", variant="text", size="small", **{"prepend-icon": "mdi-refresh"}),
             "events": {"click": {"api": "plugin/CloudShare/history_page", "method": "GET", "params": {"page": page}}}},
            component("span", text=f"共 {len(events)} 条 · 第 {page} / {total_pages} 页",
                      **{"class": "text-body-2 text-medium-emphasis"}),
            {**component("VBtn", text="上一页", variant="tonal", size="small", disabled=page <= 1),
             "events": {"click": {"api": "plugin/CloudShare/history_page", "method": "GET", "params": {"page": page - 1}}}},
            {**component("VBtn", text="下一页", variant="tonal", size="small", disabled=page >= total_pages),
             "events": {"click": {"api": "plugin/CloudShare/history_page", "method": "GET", "params": {"page": page + 1}}}},
        ]),
    ])]
