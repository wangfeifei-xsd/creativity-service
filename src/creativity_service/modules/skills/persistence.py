"""技能包写入的事务内原子能力，供手工编辑和智能协助共同使用。"""

from dataclasses import dataclass, field
from typing import Any

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import UnitOfWork
from creativity_service.core.database.inserts import InsertBatch
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import digest, new_id
from creativity_service.modules.resources.configuration import configuration_values
from creativity_service.modules.skills.packages import Package
from creativity_service.modules.skills.repositories import repository
from creativity_service.modules.skills.schemas import SkillDefinition


@dataclass
class PackageWrite:
    skill_id: str
    version_id: str
    package: Package
    package_url: str
    old_files: list[dict[str, Any]]
    audit_id: str = field(default_factory=lambda: new_id("audit"))
    file_ids: dict[str, str] = field(init=False)
    file_links: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.file_ids = {f.relative_path: new_id("skill_file") for f in self.package.manifest}
        self.file_links = {path: new_id("source") for path in self.file_ids}

    @property
    def link_id(self) -> str:
        return digest(["skill-version", self.version_id])

    def keys(self, scope: Scope) -> list[ResourceKey]:
        return [
            record_key(scope.channel_id, table, identifier)
            for table, identifiers in (
                ("skills", [self.skill_id]),
                ("resource_versions", [self.version_id]),
                ("audit_events", [self.audit_id]),
                ("source_links", [self.link_id, *self.file_links.values()]),
                ("skill_files", [*self.file_ids.values(), *[f["id"] for f in self.old_files]]),
            )
            for identifier in identifiers
        ]

    async def save_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        dependencies: list[str],
        current: dict[str, Any] | None,
    ) -> None:
        """调用方已复核权限、修订、依赖与包格式；此处只写当前配置及文件清单。"""
        definition = SkillDefinition(
            **self.package.settings.model_dump(),
            package_hash=self.package.package_hash,
            metadata=self.package.metadata,
            files=self.package.manifest,
            package_url=self.package_url,
            required_tool_versions=tuple(dependencies),
        )
        values = configuration_values(
            "skill",
            self.skill_id,
            definition.model_dump(mode="json", exclude_none=True),
            dependencies,
            {},
            context.principal_id,
        )
        repo = repository("resource_versions", context.scope)
        links: list[tuple[str, ContentRef, ContentRef, str | None]] = []
        if current:
            await repo.change(
                uow,
                self.version_id,
                current["revision"],
                {
                    k: values[k]
                    for k in (
                        "content",
                        "content_digest",
                        "dependencies",
                        "dependencies_digest",
                        "output_schema",
                    )
                },
            )
        else:
            await repo.add(uow, self.version_id, values)
            links.append(
                (
                    self.link_id,
                    ContentRef("skill", self.skill_id),
                    ContentRef("version", self.version_id),
                    None,
                )
            )
        for old_file in self.old_files:
            await repository("skill_files", context.scope).remove(uow, old_file["id"])
        pending = InsertBatch(uow)
        await repository("skill_files", context.scope).add_many(
            uow,
            {
                self.file_ids[f.relative_path]: {
                    **f.model_dump(),
                    "version_id": self.version_id,
                    "artifact_id": None,
                    "package_url": self.package_url,
                }
                for f in self.package.manifest
            },
            pending=pending,
        )
        links.extend(
            (
                self.file_links[path],
                ContentRef("version", self.version_id),
                ContentRef("skill_file", identifier),
                None,
            )
            for path, identifier in self.file_ids.items()
        )
        await DeletionGuard(context.scope).link_many(uow, links, pending=pending)
        await append_audit(
            uow,
            context,
            self.audit_id,
            "skill.package.save",
            "skill",
            self.skill_id,
            {"version_id": self.version_id, "content_digest": self.package.package_hash},
            pending=pending,
        )
        await pending.flush()
