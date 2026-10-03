"""按三渠道清单独立启动受控 MCP，业务数据及契约只来自测试配置。"""

import argparse
import copy
import json
import threading
from pathlib import Path

from examples.mcp.server import serve


def main():
    scenarios = json.loads(Path(__file__).with_name("scenarios.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=[s["code"] for s in scenarios], required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--identity-file", type=Path, required=True)
    args = parser.parse_args()
    scenario = next(s for s in scenarios if s["code"] == args.scenario)
    identity = json.loads(args.identity_file.read_text())
    with serve(scenario["profile"], args.port) as source:
        source.input_schema = copy.deepcopy(scenario.get("input_schema", source.input_schema))
        source.data = copy.deepcopy(scenario["data"])
        source.scope, source.permissions = identity["scope"], identity["permissions"]
        print(f"受控 MCP 已启动：{source.endpoint}，配置：{scenario['name']}", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
