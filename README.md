# Codex Session Cleaner

`codex-session-cleaner` 是一个基于 `textual` 的终端界面工具，用来查看本地 Codex session，并把选中的 session 移动到回收目录，而不是直接永久删除。

## 要求

这个项目必须使用 `uv` 来管理 Python 环境和依赖，不要用系统 `python` 或 `pip` 直接安装。

先检查本机是否有 `uv`：

```bash
uv --version
```

如果提示找不到 `uv`，先安装：

- 安装说明：<https://docs.astral.sh/uv/getting-started/installation/>

常见安装方式：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装完成后重新打开终端，或者确认 `uv` 已经进入 `PATH`。

## 启动

进入项目目录：

```bash
cd /path/to/codex-session-cleaner
```

同步依赖：

```bash
uv sync --extra dev
```

启动程序：

```bash
uv run codex-session-cleaner
```

## 数据位置

程序默认读取：

- `~/.codex/sessions`

如果设置了 `CODEX_HOME`，则读取：

- `$CODEX_HOME/sessions`

删除时不会直接永久删除，而是移动到：

- `~/.codex/trash/sessions`

如果设置了 `CODEX_HOME`，则移动到：

- `$CODEX_HOME/trash/sessions`

并且会保留原来的目录层级。

## 使用方式

主界面：

- `Tab`：切换视图 `all -> main -> subagent -> all`
- `j` / `k`：上下移动高亮
- `Up` / `Down`：上下移动高亮
- `Space`：选中或取消当前项
- `a`：全选当前视图
- `u`：取消当前视图全选
- `d`：打开删除确认
- `r`：重新扫描磁盘并刷新列表
- `q`：退出

删除确认界面：

- `y`：确认移动到 trash
- `n`：取消
- `Enter`：确认
- `Escape`：取消
- `j` / `k`：滚动确认内容

## 删除行为

- 成功删除的项会从当前列表直接移除
- 删除失败的项会保留在列表里，并显示错误信息
- 删除确认返回主界面时，不会立刻全量重扫磁盘
- 如果你想强制重新扫描当前 session 状态，按 `r`

## 说明

- 首次启动如果 session 很多，会比较慢，这是因为要扫描并解析本地 `rollout-*.jsonl`
- 之后删除返回主界面会快很多，因为优先走内存增量更新

## 许可证

本项目基于 Apache License 2.0 发布。
详见 [LICENSE](./LICENSE)。
附加归属说明见 [NOTICE](./NOTICE)。
