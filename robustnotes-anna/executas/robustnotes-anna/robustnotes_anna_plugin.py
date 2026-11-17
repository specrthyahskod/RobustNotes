"""Small deterministic text utilities bundled with the RobustNotes Anna app."""

import json
import re
import sys

TOOL_NAME = "tool-dev-robustnotes-anna"
MAX_TEXT_LENGTH = 12_000

MANIFEST = {
    "name": TOOL_NAME,
    "display_name": "RobustNotes Note Insights",
    "version": "0.1.1",
    "description": "Analyze a note locally for word count, estimated reading time, and Markdown headings.",
    "tools": [
        {
            "name": "analyze_note",
            "description": "Return word count, approximate reading time, and Markdown headings for a note.",
            "parameters": [
                {
                    "name": "text",
                    "type": "string",
                    "description": f"The note text to analyze, up to {MAX_TEXT_LENGTH:,} characters.",
                    "required": True,
                }
            ],
            "timeout": 10,
        }
    ],
    "credentials": [],
    "host_capabilities": [],
    "runtime": {"type": "binary"},
}


def invoke(method: str, args: dict) -> dict:
    if method != "analyze_note":
        return {"success": False, "error": f"unknown method: {method}"}
    text = args.get("text", "")
    if not isinstance(text, str):
        return {"success": False, "error": "text must be a string"}
    if len(text) > MAX_TEXT_LENGTH:
        return {"success": False, "error": "text exceeds the 12,000 character limit"}

    words = re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE)
    headings = [line.lstrip("#").strip() for line in text.splitlines() if re.match(r"^\s{0,3}#{1,6}\s+", line)]
    return {
        "success": True,
        "data": {
            "words": len(words),
            "reading_minutes": max(1, (len(words) + 219) // 220),
            "headings": headings[:20],
        },
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        try:
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            method = request.get("method")
            if method == "describe":
                result = MANIFEST
            elif method == "health":
                result = {"status": "ready"}
            elif method == "invoke":
                params = request.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                result = invoke(params.get("tool", ""), params.get("arguments", {}))
            else:
                raise ValueError(f"unknown rpc: {method}")
            response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        except Exception as error:  # Keep the stdio process alive and return a protocol error.
            response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601, "message": str(error)}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
