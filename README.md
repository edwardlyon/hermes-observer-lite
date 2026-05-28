# Hermes Observer Lite V0.1

Hermes Observer Lite is a lightweight external plugin for Hermes. It tails Hermes backend logs, turns execution records into structured events, and renders a live observer board for agent tasks, tool calls, alerts, sub-agent branches, spaces, and delivery artifacts.

Hermes Observer Lite 是一个轻量级 Hermes 外部观察插件。它读取 Hermes 后台日志，把 Agent 执行过程转换成结构化事件，并实时渲染任务流、工具调用、告警、SubAgent 分支、后台空间与交付物落位。

## Why / 为什么

Most agent UIs show only input and final output. Hermes Observer Lite tries to make the middle visible:

- user input entering Gateway / Channel
- session and conversation-loop activity
- model calls and API budget
- differentiated tool flows: Web, Terminal, Files, Memory, Other Tool
- warning and failure events as standalone visual signals
- independent SubAgent / worker branches
- response delivery back to Gateway
- background spaces such as dashboard, sessions, cache, state, and artifacts

多数 Agent 界面只展示输入和最终回复。这个插件的目标是把中间过程画出来：

- 用户输入进入 Gateway / Channel
- session 与 conversation loop 执行
- 模型调用与 API budget
- 区分不同工具流向：Web、Terminal、Files、Memory、Other Tool
- 告警和失败作为独立事件出现
- 独立的 SubAgent / worker 分支
- 回复消息回到 Gateway
- dashboard、sessions、cache、state、artifact 等后台空间可视化

## Status / 当前状态

V0.1 is an early prototype. It is intentionally small: one Python server plus one HTML frontend.

V0.1 是早期原型，刻意保持简单：一个 Python server 加一个 HTML 前端。

## Requirements / 环境要求

- Python 3.10+
- A Hermes home directory with `logs/agent.log`
- Modern browser

No external Python package is required.

无需额外 Python 依赖。

## Run / 运行

```powershell
python .\server.py --home "E:\OneDrive\CodeX-workspace\tmp\hermes-home-qwen" --port 8777
```

Open:

```text
http://127.0.0.1:8777
```

打开：

```text
http://127.0.0.1:8777
```

## API

- `GET /` - observer board UI
- `GET /api/status` - configured Hermes home and log status
- `GET /api/recent` - recent parsed events
- `GET /api/sessions?limit=30&offset=0` - paginated historical sessions
- `GET /api/session?id=<session_id>` - replay events for one session
- `GET /events` - Server-Sent Events stream for live log updates

## Event Spaces / 事件空间

Each event is mapped into an execution space:

每条事件会被映射到一个执行空间：

- `dashboard`: gateway, kanban, queues, channel actions
- `explorer`: files, documents, resource actions
- `web`: web search, web extraction, browser-like activity
- `terminal`: terminal and shell execution
- `model`: model and conversation-loop activity
- `memory`: memory, state, and session records
- `artifact`: final response, files, pages, PRs, and other deliverables

## Plugin Metadata / 插件元信息

Codex-style plugin metadata lives at:

Codex 风格插件元信息位于：

```text
.codex-plugin/plugin.json
```

## Notes / 说明

This project does not modify Hermes runtime state. It only reads logs and renders an observer view.

本项目不会修改 Hermes 运行状态，只读取日志并渲染观察视图。

## License

MIT
