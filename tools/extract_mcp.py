#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스키마를 박은 도구 하나짜리 MCP 서버. `tools/docpipe.py --claude-cli` 가 띄운다.

Claude Code CLI 로 부를 때 스키마를 강제하는 자리다. 모델에게 "JSON 으로 주십시오"
하고 부탁하면 울타리를 치거나 설명을 붙이거나 숫자를 문자열로 준다. 도구의 인자로
받으면 그럴 자리가 없다. API 의 tool_choice 와 같은 얼개이고 전달 수단만 MCP 다.

실측으로 확인한 차이다. -p 로만 받으면 awb_gross 가 "10.0" 이라는 문자열로 오고,
이 서버를 거치면 10 이라는 수로 온다. 뒤에서 형을 바꾸다 틀릴 자리가 사라진다.

환경 변수 둘로 움직인다.
  EXTRACT_SCHEMA  도구의 inputSchema 로 쓸 JSON 파일
  EXTRACT_OUT     모델이 부른 인자를 적을 파일
"""
import json
import os
import sys


def main():
    schema_path = os.environ.get("EXTRACT_SCHEMA")
    out_path = os.environ.get("EXTRACT_OUT")
    if not schema_path or not out_path:
        sys.exit("EXTRACT_SCHEMA 와 EXTRACT_OUT 이 있어야 합니다.")
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    tool = {"name": "extract",
            "description": "Return the extracted document fields. Call this exactly once.",
            "inputSchema": schema}

    def send(msg):
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "extract", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [tool]}})
        elif method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            # 임시 이름에 쓰고 옮긴다. 부르는 쪽이 파일이 생기자마자 읽고 CLI 를 끊기 때문에 반쯤 쓴 파일을 보면 안 된다
            tmp = out_path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(args, f, ensure_ascii=False)
            os.replace(tmp, out_path)
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "saved"}]}})
        elif rid is not None:      # 알림에는 답하지 않는다
            send({"jsonrpc": "2.0", "id": rid, "result": {}})


if __name__ == "__main__":
    main()
