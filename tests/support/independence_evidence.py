"""23 的固定构建物与实际数据库结构证据，不把测试材料纳入平台源码。"""

import asyncio
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT.parent / "creativity-web"


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_file(path):
    """排除解释器缓存和 Finder 元数据；其余源码及资源必须参与制品核对。"""
    return (
        path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.name != ".DS_Store"
    )


def tree(root, paths):
    files = {}
    for name in paths:
        path = root / name
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in candidates:
            if source_file(item):
                files[item.relative_to(root).as_posix()] = file_digest(item)
    assert files, paths
    return {"sha256": digest(files), "files": files}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def wheel_matches_source():
    with zipfile.ZipFile(ROOT / "dist/creativity_service-0.1.0-py3-none-any.whl") as wheel:
        for path in (ROOT / "src").rglob("*"):
            if source_file(path):
                assert wheel.read(path.relative_to(ROOT / "src").as_posix()) == path.read_bytes()
    return True


async def snapshot(env):
    # 结构查询排除行数据，正常的渠道配置和运行记录不会改变结构摘要。
    async with env.engine.connect() as conn:
        columns = (
            (
                await conn.execute(
                    text("""SELECT table_name, column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns WHERE table_schema=:schema
                    ORDER BY table_name, ordinal_position"""),
                    {"schema": env.schema},
                )
            )
            .mappings()
            .all()
        )
        indexes = (
            (
                await conn.execute(
                    text("""SELECT table_name, index_name, column_name,
                    seq_in_index, non_unique, sub_part
                    FROM information_schema.statistics WHERE table_schema=:schema
                    ORDER BY table_name, index_name, seq_in_index"""),
                    {"schema": env.schema},
                )
            )
            .mappings()
            .all()
        )
        revisions = (
            (await conn.execute(text("SELECT version_num FROM creativity_alembic_version")))
            .scalars()
            .all()
        )
        constraints = (
            (
                await conn.execute(
                    text("""SELECT table_name, constraint_name, constraint_type
                FROM information_schema.table_constraints WHERE table_schema=:schema
                ORDER BY table_name, constraint_name"""),
                    {"schema": env.schema},
                )
            )
            .mappings()
            .all()
        )
    structure = {
        "columns": [dict(row) for row in columns],
        "indexes": [dict(row) for row in indexes],
        "constraints": [dict(row) for row in constraints],
    }
    tables = {row["table_name"] for row in columns}
    assert not any(name.startswith(("matching_", "risk_", "analytics_")) for name in tables)
    app = env.client._transport.app
    # FastAPI 延迟装配的嵌套路由由正式 OpenAPI 展开，不能只检查顶层文档端点。
    app.openapi_schema = None
    schema = app.openapi()
    verbs = {"get", "post", "put", "patch", "delete", "head", "options"}
    routes = sorted(
        (path, sorted(method.upper() for method in operations if method in verbs))
        for path, operations in schema["paths"].items()
    )
    assert not any(
        path.startswith(("/api/v1/matching", "/api/v1/risk", "/api/v1/analysis"))
        for path, _ in routes
    )

    def capture():
        return {
            "wheel_matches_source": wheel_matches_source(),
            "service_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "web_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=WEB, text=True
            ).strip(),
            "service_source": tree(ROOT, ["src", "pyproject.toml", "uv.lock", "deploy"]),
            "web_source": tree(WEB, ["src", "package.json", "pnpm-lock.yaml", "vite.config.ts"]),
            "migrations": tree(ROOT, ["alembic", "alembic.ini"]),
            "service_build": tree(ROOT, ["dist/creativity_service-0.1.0-py3-none-any.whl"]),
            "web_build": tree(WEB, ["dist"]),
            "backend_client": tree(ROOT, ["examples/backend"]),
            "routes": {"sha256": digest(routes), "entries": routes},
            "openapi_sha256": digest(schema),
            "migration_versions": sorted(revisions),
            "database_structure": {"sha256": digest(structure), **structure},
        }

    return await asyncio.to_thread(capture)
