# MoviePilot 插件：PublicBT 与 CloudShare

[English](README.en.md) · [安装与市场收录说明](docs/MARKETPLACE.zh-CN.md)

为 MoviePilot V3 提供两条资源获取渠道：公开 BT，以及夸克、阿里云盘分享。
两个插件放在同一个仓库中，但可以分别安装、独立配置和启停，互不依赖。

| 插件 | 能做什么 | 当前状态 |
|---|---|---|
| **公开 BT 搜索（PublicBT）** | 通过 Prowlarr 搜索公开 BT 资源，交给独立下载器，记录搜索与下载交接过程 | 已在 MoviePilot 3.2.4 验证 |
| **CloudShare** | 汇集 PanSou、Telegram Bot 的网盘结果，转存后由官方 Mac 客户端下载到 NAS，再交给 MoviePilot 整理 | 实验性，需配置 Mac 客户端 |

当前两个插件均为 **0.2.0**，声明支持 **MoviePilot `>=3.2.4,<3.3.0`**。
本仓库是第三方插件源，目前未进入官方默认仓库清单。

## 安装

1. 在 MoviePilot 的插件市场仓库设置中，追加本仓库地址：

   ```text
   https://github.com/jidarin9383/moviepilot-plugins
   ```

2. 保存并刷新插件市场，搜索「公开 BT 搜索」或「CloudShare」。
3. 安装需要的插件，再按对应使用说明配置。

如果通过 `PLUGIN_MARKET` 配置，请保留已有仓库，在末尾用英文逗号追加本地址。
MoviePilot 从仓库的 `main` 分支读取插件索引。添加仓库不等于完成插件依赖、账号或下载器配置。

## PublicBT：公开 BT 搜索

- 接入 Prowlarr，在 MoviePilot 原生搜索结果中展示资源。
- 指定独立下载器和 NAS 下载目录，保留用户明确选择的下载器与目录。
- 配置最低做种数、索引站和结果数量。
- 提供连接测试、状态概览和可翻页的使用记录。
- API Key 私密保存，修改其他设置时留空即可保留。

下载由你配置的 qBittorrent、Transmission 等下载器完成，整理规则由 MoviePilot 管理。
插件中的「准备交接」只表示提交前的路由准备，不代表下载已经开始或完成。

→ [PublicBT 中文使用说明](plugins.v3/publicbt/README.zh-CN.md)

## CloudShare：网盘资源与 NAS 入库

- 搜索夸克、阿里云盘分享，合并 PanSou 和 Telegram 内联搜索结果。
- 默认支持 `@QuarkRobot`、`@AliYunPanBot`，可分别开关和设置。
- 转存到接收账号后，通过官方 Mac 客户端下载到 NAS 的 SMB 暂存目录。
- 根据 NAS 文件完整性和 MoviePilot 媒体身份继续整理；不确定的文件保留待确认。
- 提供任务状态、NAS 下载进度和持久使用记录。

**这不是只装在 NAS 上就能完整运行的网盘下载器。** 当前桌面方案需要 Mac 保持唤醒并登录、SMB 已挂载、官方客户端和辅助功能权限已配置。夸克需先运行；客户端中途退出后的完整自动恢复、部分阿里云盘确认弹窗仍有兼容性限制。

→ [CloudShare 中文使用说明](plugins.v3/cloudshare/README.zh-CN.md) · [Mac 辅助程序配置](deploy/README.zh-CN.md)

## 使用记录与隐私

两个插件分别保存记录，默认保留最近 1,000 条，可调整为 1–5,000 条。
记录关闭后，已有记录仍保留。运行数据写入各插件的数据目录，不写入源码目录。

仓库不包含账号、Cookie、API Key、Telegram 会话、个人部署记录、运行数据库或媒体文件。
提交问题时请使用脱敏日志和虚构数据，参见 [安全说明](SECURITY.md)。

## 开发与许可证

源码、测试和 CI 均已公开；本地测试方法见 [英文开发说明](README.en.md#development)。
普通测试不需要真实账号或提交下载，依赖 MoviePilot 宿主的测试会在宿主外明确跳过。

使用 **GPL-3.0-only** 许可证，详见 [LICENSE](LICENSE) 和 [第三方组件说明](THIRD_PARTY.md)。
