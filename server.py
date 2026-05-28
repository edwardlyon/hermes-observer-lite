from __future__ import annotations

import argparse
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_HOME = Path(r"E:\OneDrive\CodeX-workspace\tmp\hermes-home-qwen")
HERE = Path(__file__).resolve().parent
WEB_ROOT = HERE / "web"

LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?:\[(?P<session>[^\]]+)\]\s+)?"
    r"(?P<actor>[^:]+):\s+"
    r"(?P<message>.*)$"
)

API_RE = re.compile(
    r"API call #(?P<num>\d+): model=(?P<model>\S+) provider=(?P<provider>\S+).*?"
    r"latency=(?P<latency>[\d.]+s)"
)
TOOL_RE = re.compile(r"tool (?P<tool>[\w.-]+) completed \((?P<latency>[\d.]+s), (?P<size>[\d,]+) chars\)")
TURN_RE = re.compile(r"Turn ended: .*?api_calls=(?P<api>\d+/\d+).*?tool_turns=(?P<tools>\d+).*?response_len=(?P<len>\d+)")
WORKER_RE = re.compile(r"(worker-[A-Za-z0-9_.-]+|sub[- ]?agent[\s:_-]*(?:[A-Za-z0-9_.-]+|[一二三]号?)|proc_[0-9a-f]+)", re.I)
RESPONSE_READY_RE = re.compile(r"response=(?P<len>[\d,]+) chars", re.I)
SENDING_RE = re.compile(r"Sending response \((?P<len>[\d,]+) chars\) to (?P<to>\S+)", re.I)
MSG_RE = re.compile(r"msg=(?P<quote>['\"])(?P<msg>.*?)(?P=quote)")


def classify_space(actor: str, message: str, level: str) -> str:
    text = f"{actor} {message}".lower()
    if "kanban dispatcher" in text or "worker-" in text or "sub agent" in text or "sub-agent" in text:
        return "dashboard"
    if "web_search" in text or "web_extract" in text or "browser" in text:
        return "web"
    if "terminal" in text or "shell" in text or "command" in text:
        return "terminal"
    if "file" in text or "document" in text or "image_cache" in text:
        return "explorer"
    if "memory" in text or "state.db" in text or "session" in text:
        return "memory"
    if "tool_executor" in text:
        return "dashboard"
    if "openai client" in text or "conversation_loop" in text:
        return "model"
    if "gateway" in text or "weixin" in text or "kanban" in text or "cron" in text:
        return "dashboard"
    if "notion" in text:
        return "web"
    if level in {"WARNING", "ERROR"}:
        return "dashboard"
    return "dashboard"


def tool_node(tool_name: str, message: str) -> str:
    text = f"{tool_name} {message}".lower()
    if "memory" in text:
        return "n-memory"
    if "web" in text or "browser" in text or "search" in text or "extract" in text:
        return "n-web"
    if "terminal" in text or "shell" in text or "command" in text:
        return "n-terminal"
    if any(token in text for token in ["file", "read", "write", "edit", "notion", "docx", "xlsx"]):
        return "n-file"
    return "n-tool-action"


def space_node(space: str) -> str:
    return {
        "dashboard": "s-kanban",
        "explorer": "s-sessions",
        "web": "s-cache",
        "terminal": "s-state",
        "model": "s-sessions",
        "memory": "s-state",
        "artifact": "s-sessions",
    }.get(space, "s-kanban")


def parse_line(line: str) -> dict[str, Any] | None:
    match = LINE_RE.match(line.strip())
    if not match:
        return None

    item = match.groupdict()
    level = item["level"]
    actor = item["actor"].strip()
    message = item["message"].strip()
    session = item.get("session") or ""
    event_type = "done"
    node = "n-session"
    title = actor
    tech = False
    api = ""
    latency = ""
    artifact_type = ""
    worker_id = ""

    if level == "WARNING":
        event_type = "warn"
    if level == "ERROR":
        event_type = "fail"

    lower_message = message.lower()
    lower_actor = actor.lower()
    worker_match = WORKER_RE.search(f"{actor} {message}")
    if worker_match:
        worker_id = worker_match.group(1).strip()

    if "kanban dispatcher" in lower_message:
        node = "n-worker"
        title = f"SubAgent: {worker_id}" if worker_id else "SubAgent / Worker"
        tech = False
        event_type = "run"
    elif "process " in lower_message and "injecting agent notification" in lower_message:
        node = "n-worker"
        title = f"Worker result: {worker_id}" if worker_id else "Worker result"
        tech = False
        event_type = "done"
    elif (
        "worker-" in lower_message or "sub agent" in lower_message or "sub-agent" in lower_message
    ) and any(token in lower_actor for token in ["tool", "delegate", "kanban", "worker"]):
        node = "n-worker"
        title = f"SubAgent: {worker_id}" if worker_id else "SubAgent / Worker"
        tech = False
        event_type = "run"
    elif "gateway.platforms" in actor or "gateway.run" in actor:
        node = "n-input" if "inbound" in message.lower() or "sending response" in lower_message else "n-fail" if event_type == "fail" else "n-session"
        title = "Gateway / Channel"
        send_match = SENDING_RE.search(message)
        if send_match:
            title = "Gateway send"
            message = f"发往 Gateway / Weixin：{send_match.group('len')} chars → {send_match.group('to')}"
    elif "conversation_loop" in actor:
        node = "n-model"
        title = "Conversation Loop"
        api_match = API_RE.search(message)
        turn_match = TURN_RE.search(message)
        if api_match:
            api = f"{api_match.group('num')} / ?"
            latency = api_match.group("latency")
            title = f"API call #{api_match.group('num')}"
        elif turn_match:
            api = turn_match.group("api")
            title = "Turn ended"
    elif "tool_executor" in actor:
        tech = True
        tool_match = TOOL_RE.search(message)
        tool_name = tool_match.group("tool") if tool_match else ""
        node = tool_node(tool_name, message)
        title = f"Tool: {tool_name}" if tool_name else "Tool Executor"
        if tool_name and ("agent" in tool_name or "worker" in tool_name or "delegate" in tool_name):
            node = "n-worker"
            title = f"SubAgent: {tool_name}"
            worker_id = worker_id or tool_name
            tech = False
        if "returned error" in message.lower():
            event_type = "warn" if level != "ERROR" else "fail"
        if tool_match:
            latency = tool_match.group("latency")
    elif actor == "run_agent":
        node = "n-model"
        title = "Model Client"
        tech = True
    elif "memory_monitor" in actor:
        node = "s-state"
        title = "Memory Monitor"
    elif "terminal_tool" in actor:
        node = "n-terminal"
        title = "Terminal Tool"
        tech = True

    if level == "WARNING":
        node = "n-alert"
        title = "Warning"
        event_type = "warn"

    if "rate limited" in message.lower():
        node = "n-fail"
        event_type = "fail" if level == "ERROR" else "warn"
        title = "Rate Limit"

    if "response ready" in lower_message:
        node = "n-response"
        title = "Response ready"
        artifact_type = "response"
        response_match = RESPONSE_READY_RE.search(message)
        if response_match:
            message = f"回复已生成：{response_match.group('len')} chars，等待 Gateway 发送。"
    elif "turn ended" in lower_message:
        node = "n-response"
        artifact_type = "response"
        turn_match = TURN_RE.search(message)
        if turn_match:
            message = f"文本响应完成：{turn_match.group('len')} chars，API {turn_match.group('api')}。"
    elif any(word in lower_message for word in ["notion page", ".docx", ".xlsx", "pull request", "artifact", "saved to", "written to"]):
        node = "n-artifact"
        artifact_type = "deliverable"

    space = classify_space(actor, message, level)
    if node.startswith("s-"):
        target_space = node
    else:
        target_space = space_node(space)

    return {
        "time": item["time"],
        "session": session,
        "actor": actor,
        "kind": "log",
        "node": node,
        "space": space,
        "spaceNode": target_space,
        "type": event_type if event_type != "done" or node != "n-model" else "run",
        "artifactType": artifact_type,
        "title": title,
        "body": message[:240],
        "api": api,
        "lat": latency,
        "tech": tech,
        "workerId": worker_id,
        "raw": line.rstrip("\n"),
    }


def read_recent_events(log_path: Path, limit: int) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-limit * 4 :]
    events = [event for line in lines if (event := parse_line(line))]
    return events[-limit:]


def read_all_events(log_path: Path, max_lines: int = 20000) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-max_lines:]
    return [event for line in lines if (event := parse_line(line))]


def session_index(log_path: Path, limit: int = 30, offset: int = 0) -> dict[str, Any]:
    sessions: dict[str, dict[str, Any]] = {}
    for event in read_all_events(log_path):
        sid = event.get("session")
        if not sid:
            continue
        rec = sessions.setdefault(sid, {
            "session": sid,
            "first": event["time"],
            "last": event["time"],
            "events": 0,
            "tools": 0,
            "api": "",
            "ended": False,
            "warnings": 0,
            "failures": 0,
        })
        rec["last"] = event["time"]
        rec["events"] += 1
        if event.get("tech"):
            rec["tools"] += 1
        if event.get("type") == "warn":
            rec["warnings"] += 1
        if event.get("type") == "fail":
            rec["failures"] += 1
        if event.get("title") == "Turn ended":
            rec["ended"] = True
            if event.get("api"):
                rec["api"] = event["api"]
    rows = [rec for rec in sessions.values() if rec["ended"] and rec["events"] >= 3]
    rows.sort(key=lambda x: x["last"], reverse=True)
    total = len(rows)
    return {
        "items": rows[offset : offset + limit],
        "limit": limit,
        "offset": offset,
        "total": total,
        "hasMore": offset + limit < total,
    }


def events_for_session(log_path: Path, session_id: str) -> list[dict[str, Any]]:
    if not session_id:
        return []
    all_events = read_all_events(log_path)
    events = [event for event in all_events if event.get("session") == session_id]
    if events and any("delegate_task" in event.get("body", "") for event in events):
        start = events[0]["time"]
        end = events[-1]["time"]
        by_session: dict[str, list[dict[str, Any]]] = {}
        for event in all_events:
            sid = event.get("session")
            if not sid or sid == session_id:
                continue
            if start <= event["time"] <= end:
                by_session.setdefault(sid, []).append(event)
        child_events: list[dict[str, Any]] = []
        for sid, child in by_session.items():
            first = child[0]
            body = first.get("body", "")
            if not any(token in body.lower() for token in ["子agent", "subagent", "sub agent"]):
                continue
            worker_id = sid
            if "一号" in body:
                worker_id = "SubAgent 一号"
            elif "二号" in body:
                worker_id = "SubAgent 二号"
            elif "三号" in body:
                worker_id = "SubAgent 三号"
            child_events.append({
                **first,
                "session": session_id,
                "node": "n-worker",
                "space": "dashboard",
                "spaceNode": "s-kanban",
                "type": "run",
                "title": worker_id,
                "workerId": worker_id,
                "tech": False,
            })
        if child_events:
            events = sorted(events + child_events, key=lambda event: event["time"])
    # Keep replay readable: system heartbeat-like events are not useful inside a session replay.
    return events[:300]


class ObserverHandler(BaseHTTPRequestHandler):
    hermes_home: Path = DEFAULT_HOME
    log_name: str = "agent.log"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def log_path(self) -> Path:
        return self.hermes_home / "logs" / self.log_name

    def send_json(self, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == "/" or path.startswith("/index.html"):
            return self.serve_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
        if path.startswith("/favicon.ico"):
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith("/api/status"):
            return self.send_json({
                "hermesHome": str(self.hermes_home),
                "logPath": str(self.log_path),
                "exists": self.log_path.exists(),
                "size": self.log_path.stat().st_size if self.log_path.exists() else 0,
            })
        if path.startswith("/api/recent"):
            return self.send_json(read_recent_events(self.log_path, 120))
        if path.startswith("/api/sessions"):
            limit = int((params.get("limit") or ["30"])[0])
            offset = int((params.get("offset") or ["0"])[0])
            limit = max(1, min(limit, 200))
            offset = max(0, offset)
            return self.send_json(session_index(self.log_path, limit=limit, offset=offset))
        if path.startswith("/api/session"):
            session_id = (params.get("id") or [""])[0]
            return self.send_json(events_for_session(self.log_path, session_id))
        if path.startswith("/events"):
            return self.stream_events()
        self.send_error(404)

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def emit(event: dict[str, Any]) -> None:
            data = json.dumps(event, ensure_ascii=False)
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

        for event in read_recent_events(self.log_path, 40):
            emit(event)

        pos = self.log_path.stat().st_size if self.log_path.exists() else 0
        while True:
            try:
                if not self.log_path.exists():
                    time.sleep(1)
                    continue
                size = self.log_path.stat().st_size
                if size < pos:
                    pos = 0
                with self.log_path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    for line in f:
                        pos = f.tell()
                        event = parse_line(line)
                        if event:
                            emit(event)
                time.sleep(0.6)
            except (BrokenPipeError, ConnectionResetError):
                break


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Observer Lite")
    parser.add_argument("--home", default=os.environ.get("HERMES_HOME", str(DEFAULT_HOME)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--log", default="agent.log")
    args = parser.parse_args()

    ObserverHandler.hermes_home = Path(args.home)
    ObserverHandler.log_name = args.log
    server = ThreadingHTTPServer((args.host, args.port), ObserverHandler)
    print(f"Hermes Observer Lite: http://{args.host}:{args.port}")
    print(f"Reading: {ObserverHandler.hermes_home / 'logs' / args.log}")
    server.serve_forever()


if __name__ == "__main__":
    main()
