#!/usr/bin/env python3
"""Read-only stock feature MCP server over JSON-RPC stdio."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from predict_advanced.feature_analysis import analyze_stock_features, feature_definitions

PROTOCOL_VERSION = "2025-06-18"
TOOLS = [
    {
        "name": "analyze_stock_features",
        "description": "Analyze one instrument's daily OHLCV strictly before a prediction cutoff.",
        "inputSchema": {
            "type": "object",
            "required": ["records", "prediction_date"],
            "properties": {
                "records": {"type": "array", "items": {"type": "object"}, "maxItems": 5000},
                "prediction_date": {"type": "string"},
                "prediction_cutoff_datetime": {"type": ["string", "null"]},
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "symbol": {"type": ["string", "null"]},
                "trading_dates": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "feature_definitions",
        "description": "Return feature formulas, units, windows and limitations.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def response(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    return message


def handle(message):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return response(message.get("id") if isinstance(message, dict) else None,
                        error={"code": -32600, "message": "Invalid Request"})
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        return response(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "predict-stock-features", "version": "1.0"},
        })
    if method == "tools/list":
        return response(request_id, {"tools": TOOLS})
    if method != "tools/call":
        return response(request_id, error={"code": -32601, "message": "Method not found"})
    params = message.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("arguments", {}), dict):
        return response(request_id, error={"code": -32602, "message": "Invalid params"})
    try:
        if params.get("name") == "analyze_stock_features":
            payload = analyze_stock_features(**params.get("arguments", {}))
        elif params.get("name") == "feature_definitions":
            if params.get("arguments"):
                raise TypeError("feature_definitions takes no arguments")
            payload = feature_definitions()
        else:
            return response(request_id, error={"code": -32602, "message": "Unknown tool"})
        return response(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, allow_nan=False)}],
            "structuredContent": payload,
            "isError": False,
        })
    except (TypeError, ValueError) as exc:
        return response(request_id, {
            "content": [{"type": "text", "text": f"Invalid tool arguments: {type(exc).__name__}"}],
            "isError": True,
        })


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
            outgoing = handle(message)
        except ValueError:
            outgoing = response(None, error={"code": -32700, "message": "Parse error"})
        if outgoing is not None:
            sys.stdout.write(json.dumps(outgoing, ensure_ascii=False, allow_nan=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
