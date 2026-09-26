# 版本更新提醒

[English](version-check.md) · 简体中文

`/autoresearch init` 在最初的 `bootstrap paths` 检查中显示版本提醒，比较本地
Git HEAD 与当前跟踪的远端分支最新提交。仅靠包版本号（目前为 `0.0.1`）无法区分
源码更新。检查结果用于提醒，不阻止初始化。

## 手动与离线检查

在 Hypoloom 控制环境中执行：

```bash
python -B -m hypoloom.cli version-check
python -B -m hypoloom.cli version-check --json
python -B -m hypoloom.cli version-check --remote origin --branch dev
python -B -m hypoloom.cli version-check --offline
```

默认检查当前安装的 Hypoloom 源码所在仓库，不随命令执行目录变化；
`--repository /absolute/checkout` 可明确选择其他仓库。默认目标是当前分支配置的
upstream 远端与分支；没有 upstream 时使用 `origin` 和当前分支名。fork 和名称
不同的跟踪分支按实际 Git 配置处理。因此“最新”仅针对该分支，不代表所有分支、
官方仓库或最新正式发布版。用 `--remote` 指定已有 Git remote 名称，`--branch`
指定目标分支；分离 HEAD 必须明确分支。输出不包含仓库 URL 或原始认证错误。

整个检查默认限时 5 秒，`--timeout 10` 可调整，最多 30 秒。Git 使用已有认证，
不弹出登录流程；超时会结束所属 Git 及辅助进程组。init 的路径检查对应使用
`bootstrap paths --offline` 或 `bootstrap paths --version-timeout 10`；
用户要求离线 init 时，交互入口会传递离线参数。离线仍显示本地版本，不访问远端。

## 如何理解结果

| 状态 | 含义 |
| --- | --- |
| `up_to_date` | 本地 HEAD 与检查时目标远端分支的最新提交一致。 |
| `behind` | 已有本地历史足以证明远端包含更新提交。 |
| `ahead` | 已有本地历史足以证明本地在远端基础上有额外提交。 |
| `diverged` | 完整本地历史证明两端互不包含，分支已分叉。 |
| `remote_differs` | 远端提交不同，但已有本地历史无法证明先后关系；需要审阅更新。 |
| `offline` | 用户明确跳过远端检查，是否最新未知。 |
| `unavailable` | 缺少 Git/远端/分支、分离 HEAD 未指定目标、超时或网络/认证失败，无法检查。 |

未提交修改和未跟踪文件单独提示。即使 `up_to_date`，也只表示 HEAD 一致，
不代表整个工作目录一致。部分克隆将工作区修改状态标为未知，并跳过提交先后判断，
避免旧 Git 隐式下载缺失对象。浅克隆历史不足时不判断为分叉。没有 Git 仓库的
wheel/源码归档只显示已安装包版本，无法确认远端源码是否最新；不会误用外层无关仓库。

JSON 包含两端提交、比较目标、检查时间与原因。`update_available` 仅在确认
`behind` 时为 `true`；`up_to_date`/`ahead` 为 `false`，未知或分叉时为 `null`。
这不是正式发布或兼容性判定。所有提醒结果的退出码均为 0，参数非法才报错；
脚本应检查 `status`，不能把退出码 0 当作“已经最新”。

## 检查范围与更新

检查通过 [Git ls-remote](https://git-scm.com/docs/git-ls-remote) 读取远端引用，
不执行 fetch/pull、不刷新本地远端跟踪引用、不写索引、不保存版本缓存、不安装代码，
也不修改 Profile 或运行。因此远端更新尚未下载到本地时，通常显示
`remote_differs`，不会猜测为 `behind`。初始化展示一次；Host 调度、普通状态页面和
Issue 导出不做远端版本检查。Issue 环境信息只复用本地版本读取器。

手动更新前先审阅目标分支、妥善保存本地改动，再按自己的 Git 流程操作。
已有科研运行保留其记录的运行时；更新源码不等于迁移或恢复已有运行。
本功能不增加自动重启、迁移或定时通知服务。
