"""Minimal synchronous MCP stdio client used by the prediction runner."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class MCPError(RuntimeError):
    """Raised when the required MCP feature tool cannot be used."""


class MCPFeatureClient:
    def __init__(self, command: str, args: list[str], timeout: int = 30, cwd: str | Path | None = None):
        self.command = command
        self.args = args
        self.timeout = timeout
        self.cwd = str(cwd) if cwd else None
        self.process: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr_lines: list[str] = []
        self.next_id = 1

    def __enter__(self):
        try:
            self.process = subprocess.Popen(
                [self.command, *self.args],
                cwd=self.cwd,
                env=os.environ.copy(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise MCPError(f"mcp_start_failed:{type(exc).__name__}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            initialized = self.request("initialize", {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "predict-advanced", "version": "1.0"},
            })
            if not isinstance(initialized, dict) or not initialized.get("protocolVersion"):
                raise MCPError("mcp_invalid_initialize_response")
            self.notify("notifications/initialized", {})
            tools = self.request("tools/list", {})
            names = {item.get("name") for item in tools.get("tools", []) if isinstance(item, dict)}
            if "analyze_stock_features" not in names:
                raise MCPError("mcp_required_tool_missing")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, traceback):
        if self.process is None:
            return
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def _read_stdout(self):
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                message = json.loads(line)
                if isinstance(message, dict):
                    self.messages.put(message)
            except ValueError:
                self.messages.put({"_invalid": True})

    def _read_stderr(self):
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            if len(self.stderr_lines) < 20:
                self.stderr_lines.append(line.rstrip()[:500])

    def _send(self, message: dict[str, Any]):
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            raise MCPError("mcp_process_not_running")
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"mcp_write_failed:{type(exc).__name__}") from exc

    def notify(self, method: str, params: dict[str, Any]):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any]):
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"mcp_timeout:{method}")
            if self.process and self.process.poll() is not None and self.messages.empty():
                raise MCPError(f"mcp_process_exited:{self.process.returncode}")
            try:
                message = self.messages.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if message.get("_invalid"):
                raise MCPError("mcp_invalid_json_message")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
                raise MCPError(f"mcp_rpc_error:{method}:{code}")
            return message.get("result")

    def analyze(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.monotonic()
        result = self.request("tools/call", {"name": "analyze_stock_features", "arguments": arguments})
        if not isinstance(result, dict) or result.get("isError"):
            raise MCPError("mcp_tool_call_failed")
        payload = result.get("structuredContent")
        if not isinstance(payload, dict):
            payload = None
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        candidate = json.loads(item.get("text", ""))
                    except ValueError:
                        continue
                    if isinstance(candidate, dict):
                        payload = candidate
                        break
        if not isinstance(payload, dict):
            raise MCPError("mcp_tool_invalid_payload")
        audit = {"server": "predict-stock-features", "tool": "analyze_stock_features",
                 "transport": "stdio", "latency_ms": round((time.monotonic() - started) * 1000),
                 "status": "ok"}
        return payload, audit
