"""公共来源类型到存储记录的映射；范围核对与清理图共用同一登记。"""

from sqlalchemy import MetaData, Table

from creativity_service.core.database.tables import metadata

CONTENT_TABLES = {
    "conversation": "conversations",
    "message": "messages",
    "summary": "conversation_summaries",
    "context": "context_snapshots",
    "memory": "memories",
    "run": "runs",
    "artifact": "artifacts",
    "snapshot": "release_snapshots",
    "version": "resource_versions",
    "tool_call": "tool_calls",
    "evidence": "evidence_refs",
    "usage_export": "usage_exports",
    "evaluation_case": "evaluation_cases",
    "evaluation_fixture": "evaluation_fixtures",
    "evaluation_result": "evaluation_results",
    "evaluation": "evaluations",
    "evaluation_dataset_version": "evaluation_dataset_versions",
    "prompt_sample": "prompt_samples",
    "prompt_test": "prompt_tests",
    "model_test": "model_tests",
    "skill_file": "skill_files",
    "skill_test": "skill_tests",
    "agent_candidate": "agent_candidates",
}


CONTENT_MODELS: dict[str, Table] = {}


def register_content_models(source: MetaData) -> None:
    """由应用装配登记模型；公共删除检查不导入业务模块。"""
    for kind, name in CONTENT_TABLES.items():
        if name in source.tables:
            CONTENT_MODELS[kind] = source.tables[name]


register_content_models(metadata)
