# CloudShare 网盘资源

[English](README.md) · [返回首页](../../README.md)

**当前为实验性插件。** 支持 MoviePilot `>=3.2.4,<3.3.0`。
资源流程是：搜索分享 → 转存到接收账号 → 官方 Mac 客户端下载到 NAS → MoviePilot 整理。
每个阶段单独判断，转存成功不代表 NAS 已经收到文件。

## 需要准备什么

- 可访问的 PanSou 服务，以及持久、可写的 MoviePilot 配置目录。
- 接收资源的网盘账号；分享方与接收方需要满足对应网盘的转存要求。
- 已登录的官方夸克、阿里云盘 Mac 客户端。
- Mac 能挂载的 NAS SMB 共享，两个客户端都将该共享中的暂存目录设为默认下载位置。
- 编译并授权 [Mac 辅助程序](../../deploy/README.zh-CN.md)。

默认 NAS 根目录为 `/downloads`，PanSou 地址为 `http://pansou:8888`。
可以通过 `CLOUDSHARE_MEDIA_ROOT`、`CLOUDSHARE_PANSOU_URL`、`CLOUDSHARE_DOWNLOAD_DIR`
修改环境地址；它们必须是 MoviePilot 容器实际可访问的地址和绝对路径。

## 设置页

| 分区 | 用途 |
|---|---|
| 资源渠道 | 分别启停夸克和阿里云盘的新搜索、新任务；已有任务保留 |
| Telegram 搜索 | 总开关、分渠道开关、Bot 用户名、HTTP/SOCKS5 代理及登录配置状态 |
| 下载与入库 | SMB 暂存目录、电影和剧集监控目录、阿里云盘自动转存与派发 |
| 使用记录 | 记录开关、保留数量 |

默认机器人为 `@QuarkRobot` 与 `@AliYunPanBot`。
插件使用 Telegram 用户会话的内联查询，不向聊天发送搜索消息。
结果会与 PanSou 合并、去重，并检查网盘文件的可用性。

## 私密登录配置

以下文件只能由你在自己的环境中设置，不要上传到仓库或问题反馈中。
私密文件权限应为 600，所属用户应与 MoviePilot 运行用户一致。

- **夸克**：接收账号的 Cookie 放在 `/config/cloudshare/cookie`。
- **Telegram**：先在宿主环境中安装 `requirements-telegram.txt` 所列依赖，
  再在自己的交互终端运行 `python deploy/telegram_setup.py`。
  按提示输入 APP ID、hash、带国家区号的手机号、验证码及两步验证密码（如有）。
  输入不回显，配置保存在 `/config/cloudshare/telegram.json`，已有登录不会被覆盖。
  NAS 需要代理时可添加 `--proxy` 参数。首次配置完成后重载插件。
- **阿里云盘自动转存**：需要 `aligo==6.2.8` 及依赖位于
  `/config/cloudshare/aliyun/vendor`，并通过 aligo 自身登录流程准备
  `/config/cloudshare/aliyun/cloudshare.json`。这部分需要自行配置，当前没有完整的首次登录界面。

插件不会自动安装这些额外依赖。Telegram 的 Bot、代理与搜索开关可在页面修改，登录凭据不在页面回显。

## 下载与整理

Mac 路径与 NAS 路径必须指向同一个共享目录。例如，NAS 侧的
`/downloads/cloudshare-staging` 可以对应 Mac 的 `/Volumes/Downloads/cloudshare-staging`。
这些是示例，不能直接套用到路径不同的环境。

MoviePilot 会等待文件完整、稳定，再结合下载历史中的媒体身份整理。
不能确定影片身份、影片文件存在歧义或缺少足够集数信息时，文件留待确认，不会只凭文件名强行入库。

「查看数据」展示当前任务、NAS 已收到的进度以及使用记录，支持刷新和翻页。
旧任务只会记为启用记录时的快照，不会虚构原始时间。

## 已知限制

- 当前初始化仍需要夸克接收账号 Cookie，即使之后只开启阿里云盘搜索。
- Mac 必须保持唤醒并登录；夸克派发要求客户端已经运行。
- 客户端下载途中退出后，尚未覆盖全部无人值守恢复情况。
- 部分阿里云盘下载确认弹窗无法通过辅助功能读取；默认路径应保持开启，
  程序不会盲点不匹配的窗口，不能保证所有客户端版本都完全无感。
- Telegram Bot、PanSou 来源和分享链接均可能变化或失效。

请仅下载你有权使用的资源。第三方服务、账号状态和网络速度不由插件保证。
