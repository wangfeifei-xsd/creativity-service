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


def tree(root, paths):
    files = {}
    for name in paths:
        path = root / name
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in candidates:
            if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc":
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
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
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
                    text("""SELECT tablename, indexname, indexdef FROM pg_indexes
                    WHERE schemaname=:schema ORDER BY tablename, indexname"""),
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
                    text("""SELECT t.relname AS table_name, c.conname,
                pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c
                JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname=:schema ORDER BY t.relname, c.conname"""),
                    {"schema": env.schema},
                )
            )
            .mappings()
            .all()
        )
    structure = {
        "columns": [dict(row) for row in columns],
        "indexes": [
            {**dict(row), "indexdef": row["indexdef"].replace(env.schema, "ISOLATED_SCHEMA")}
            for row in indexes
        ],
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
