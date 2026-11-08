# v0.2.1

PublicBT now verifies the Prowlarr indexer catalog before searching. Only enabled public torrent indexers are selected; private and unknown indexer results are rejected, including when the configured indexer list is empty.

CloudShare can initialize Alipan without a Quark cookie. Missing or malformed Quark credentials disable Quark discovery and leave Quark jobs waiting for credentials rather than preventing Alipan initialization.

The Mac dispatcher starts the installed official Quark or Alipan client when it is closed. Quark dispatch verifies that the configured staging directory is on the NAS mount. Existing configurable public-export paths are preserved.

## Upgrade notes

Update the plugins and rebuild/reinstall the Mac dispatcher to receive client startup changes. After replacing the application, macOS may require removing and re-adding the current CloudShare Dispatch application under Privacy & Security → Accessibility. Check authorization in the background service context; a terminal-only permission check is insufficient. Client login and an unlocked Mac session are still required.

## Validation

PublicBT regression tests cover rejection of private/unknown indexers and skipping searches when no public indexers are available. CloudShare unit tests and Swift compilation are checked separately. A production Alipan task completed download to NAS and MoviePilot hard-link import with matching file size and source/destination inode. Emby playback was not verified. Public export retains configurable paths and has not been separately exercised end to end on another installation.

## 中文更新说明

- PublicBT 搜索前校验索引器目录，只允许已启用的公开 BT 索引器，并过滤私有或未知索引器的结果。
- CloudShare 阿里渠道不再依赖夸克 Cookie；夸克凭据缺失或格式错误时，夸克任务等待处理。
- Mac 后台程序支持启动已关闭的官方客户端，派发前检查 NAS 挂载。
- 更新 Mac 应用后可能需要重新添加辅助功能授权；应以后台服务的权限状态为准。
- 已验证一条阿里任务下载到 NAS 并由 MoviePilot 硬链接入库；尚未验证 Emby 播放。
