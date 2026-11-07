# 安装、仓库收录与官方插件库

以下是不同层级的分发方式。公开 GitHub 仓库本身，不会自动让所有用户的默认市场出现插件。
本仓库当前没有提交官方收录申请。

## 1. 用户手动添加本仓库：现在即可使用

在 MoviePilot 的插件市场仓库设置中追加：

```text
https://github.com/jidarin9383/moviepilot-plugins
```

也可以通过 `PLUGIN_MARKET` 配置，在原有列表后用英文逗号追加。
不要覆盖用户已有的仓库。市场读取 GitHub 仓库的 `main` 分支，
本仓库的 `package.v3.json` 提供两个独立条目。
当前只声明支持 MoviePilot `>=3.2.4,<3.3.0`，不支持 V2。

这条路径不需要先合并到官方插件代码仓库。安装仍需要 GitHub 访问能力，
宿主的 GitHub Token、代理和网络配置按官方说明设置。

## 2. 申请第三方仓库清单收录：由官方维护者审核

官方 Wiki 的 `plugin.md` 包含一个带标记的公开仓库列表。
可向 `jxxghp/MoviePilot-Wiki` 提交 PR，申请将本仓库加入该列表。
维护者是否接受、何时合并，都不能由本仓库自行决定。

当前 V3 构建会读取 Wiki 清单生成默认市场值。即使申请被合并，也应等待包含该清单的后续构建；
用户已有的环境变量或 `/config/app.env` 可以覆盖默认值，不能承诺所有现有实例会自动新增。
这属于仓库收录，不等于两个插件已经成为官方维护的代码。

## 3. 向官方插件代码仓库提交 PR：另一条路径

若希望插件源码直接进入 `jxxghp/MoviePilot-Plugins`，需要按其 V3 规范整理源码、索引、
版本、依赖、文档和测试，再提交 PR，由维护者审核。
当前仓库只是独立的第三方实现，没有完成或声称完成这一步。

对本项目而言，可先通过第三方仓库收集反馈。CloudShare 仍有 Mac 客户端和首次配置限制，
应保留实验性标记；官方代码库提交前还需单独对照最新开发规范检查。

## 官方依据

- [插件与第三方仓库说明](https://wiki.movie-pilot.org/plugin)
- [Wiki 公开仓库列表](https://github.com/jxxghp/MoviePilot-Wiki/blob/main/plugin.md)
- [V3 默认市场值生成方式](https://github.com/jxxghp/MoviePilot/blob/v3/docs/development-setup.md#41-github-发版时生成插件市场默认值)
- [插件仓库与发布指南](https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/Repository_Guide.md)
