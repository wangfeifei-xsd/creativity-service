"""导出配置与执行交接契约，不将内部冻结结构绑定到公开请求。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.agents.schemas import (
    AgentDefinition,
    AgentValidation,
    FrozenExecutionSpec,
)
from creativity_service.modules.releases.ports import EvaluationEvidence


def main() -> None:
    parser = argparse.ArgumentParser(description="导出智能体契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/agents.json"),
        schema_bundle(
            (AgentDefinition, AgentValidation, FrozenExecutionSpec, EvaluationEvidence),
            mode="validation",
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
