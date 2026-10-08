"""模型与运行时的独立交接契约生成器。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.integrations.models.contracts import ModelEvent, ModelRequest
from creativity_service.modules.models.schemas import DebugExecution, FrozenModel, TestCompletion

MODELS = (FrozenModel, DebugExecution, TestCompletion, ModelRequest, ModelEvent)


def export(check: bool = False) -> None:
    write_contract(
        Path("contracts/internal/models.json"),
        schema_bundle(MODELS, mode="validation"),
        check=check,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="导出模型交接契约")
    parser.add_argument("--check", action="store_true")
    export(parser.parse_args().check)


if __name__ == "__main__":
    main()
