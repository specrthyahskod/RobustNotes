"""Small deterministic text utilities bundled with the RobustNotes Anna app."""

import json
import re
import sys

TOOL_NAME = "tool-dev-robustnotes-anna"
MAX_TEXT_LENGTH = 12_000

MANIFEST = {
    "name": TOOL_NAME,
    "version": "0.1.0",
    "tools": [
        {
            "name": "analyze_note",
            "description": "Return word count, approximate reading time, and Markdown headings for a note.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": MAX_TEXT_LENGTH}},
                "required": ["text"],
                "additionalProperties": False,
            },
        }
    ],
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
            method = request.get("method")
            if method == "describe":
                result = MANIFEST
            elif method == "health":
                result = {"status": "ready"}
            elif method == "invoke":
                params = request.get("params", {})
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
