"""模型与运行时的独立交接契约生成器。"""

import argparse
import json
from pathlib import Path

from creativity_service.integrations.models.contracts import ModelEvent, ModelRequest
from creativity_service.modules.models.schemas import DebugExecution, FrozenModel, TestCompletion

MODELS = (FrozenModel, DebugExecution, TestCompletion, ModelRequest, ModelEvent)


def export(check: bool = False) -> None:
    target = Path(__file__).resolve().parents[4] / "contracts/models"
    for model in MODELS:
        content = (
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        path = target / f"{model.__name__}.schema.json"
        if check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"模型交接契约过期：{path.name}")
        else:
            target.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出模型交接契约")
    parser.add_argument("--check", action="store_true")
    export(parser.parse_args().check)


if __name__ == "__main__":
    main()
