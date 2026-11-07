# CloudShare Mac 辅助程序

[English](README.md) · [CloudShare 使用说明](../plugins.v3/cloudshare/README.zh-CN.md)

一个 Swift 后台程序同时处理夸克和阿里云盘的派发清单，调用官方客户端将文件直接写入 NAS。
网盘文件不经过 qBittorrent 或 Transmission 下载。

## 编译

在安装了 Xcode 命令行工具的 Mac 上，于仓库根目录运行：

```sh
bash deploy/build_dispatch.sh
```

输出为 `dist/CloudShare Dispatch.app`。这一步只构建本地应用，不安装、不启动，也不自动授予权限。

## 配置

先在 Finder 挂载 NAS 共享，并将官方客户端默认下载目录指向共享暂存目录。
辅助程序使用以下环境变量：

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CLOUDSHARE_MAC_STAGING` | `/Volumes/Downloads/cloudshare-staging` | Mac 看到的共享暂存目录 |
| `CLOUDSHARE_SMB_MOUNT` | `/Volumes/Downloads` | 预期的网络共享挂载根目录 |
| `CLOUDSHARE_SMB_URL` | 不设置 | 可选的 SMB 重连地址，不能包含账号密码 |

这些默认值只是通用示例。辅助程序会验证暂存目录确实位于网络卷，防止共享未挂载时误写 Mac 本地目录。

手动启动示例：

```sh
export CLOUDSHARE_MAC_STAGING=/Volumes/Downloads/cloudshare-staging
export CLOUDSHARE_SMB_MOUNT=/Volumes/Downloads
"dist/CloudShare Dispatch.app/Contents/MacOS/quark-dispatch" service
```

在 macOS「隐私与安全性 → 辅助功能」中授权本地构建的应用，授权后重新启动。
更新未正式签名的可执行文件后，系统可能要求重新确认权限。

需要登录后常驻时，可根据安装位置自行配置 LaunchAgent，使用应用绝对路径和上面的环境变量。
仓库不附带个人 LaunchAgent，也不读取 Finder 保存的 SMB 密码。

程序每 30 秒检查一次清单，保存任务领取状态以避免重复派发。
日志位于 `~/Library/Logs/CloudShare/`。夸克操作会等待桌面空闲且未锁定。
客户端退出恢复和阿里云盘确认弹窗的限制，详见 CloudShare 使用说明。
