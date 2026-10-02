"""只在有界内存中解析技能归档，不落地解包、不访问外部引用、不执行脚本。"""

import base64
import binascii
import gzip
import hashlib
import io
import json
import posixpath
import re
import stat
import tarfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from creativity_service.core.primitives import ServiceError, canonical_json, digest
from creativity_service.modules.skills.schemas import SkillFile, SkillSettings

MAX_ARCHIVE = 8 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_FILE = 1024 * 1024
MAX_FILES = 128
PORTABLE = ".platform/skill.json"
TEXT_TYPES = {".md": "text/markdown", ".txt": "text/plain", ".json": "application/json"}
SCRIPT_SUFFIXES = {".py", ".js", ".ts", ".sh", ".bash", ".ps1", ".rb", ".exe", ".bat"}


def invalid(message: str) -> ServiceError:
    return ServiceError("SKILL_FORMAT_INVALID", message, 422)


def safe_path(value: str) -> str:
    if (
        not value
        or len(value) > 1024
        or value.startswith(("/", "\\"))
        or "\\" in value
        or ":" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or any(p in {"", ".", ".."} for p in value.split("/"))
        or unicodedata.normalize("NFC", value) != value
        or "%" in value
    ):
        raise invalid("包内路径不合法")
    return value


class MetadataLoader(yaml.SafeLoader):
    """兼容常见 YAML 元数据，拒绝别名、合并键与重复字段。"""


def metadata_mapping(loader: MetadataLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result or key == "<<":
            raise invalid("技能元数据有重复或非法字段")
        result[key] = loader.construct_object(value_node)
    return result


MetadataLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, metadata_mapping)


def parse_entry(data: bytes) -> tuple[dict[str, Any], str]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise invalid("SKILL.md 必须使用 UTF-8 编码") from exc
    if "\x00" in text:
        raise invalid("技能入口不能包含空字节")
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise invalid("SKILL.md 缺少 YAML 元数据区")
    end = lines.index("---", 1)
    front = "\n".join(lines[1:end])
    if len(front.encode()) > 32768:
        raise invalid("技能元数据超过 32 KiB")
    try:
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(front)):
            raise invalid("技能元数据不能使用 YAML 引用")
        metadata = yaml.load(front, Loader=MetadataLoader)
        canonical_json(metadata)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise invalid("技能元数据格式不正确，只允许 JSON 兼容值") from exc
    if not isinstance(metadata, dict):
        raise invalid("技能元数据必须是字段对象")
    for field, limit in (("name", 128), ("description", 4000)):
        if (
            not isinstance(metadata.get(field), str)
            or not 1 <= len(metadata[field].strip()) <= limit
        ):
            raise invalid("技能名称或描述缺失、格式不正确")
    protected = {
        "channel_id",
        "resource_id",
        "version_id",
        "required_tool_versions",
        "allowed_agents",
        "credential_ref",
        "credential_id",
        "api_key",
        "access_token",
        "password",
        "secret",
        "authorization",
    }

    def check_metadata(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower().replace("-", "_") in protected:
                    raise invalid("入口元数据不能携带渠道身份、凭据或平台授权")
                check_metadata(item)
        elif isinstance(value, list):
            for item in value:
                check_metadata(item)

    check_metadata(metadata)
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise invalid("技能指令不能为空")
    return metadata, body


def entry(name: str, description: str, instructions: str) -> bytes:
    return (
        "---\n"
        + json.dumps({"name": name, "description": description}, ensure_ascii=False)
        + "\n---\n\n"
        + instructions
        + "\n"
    ).encode()


def check_references(path: str, text: str, paths: set[str]) -> None:
    candidates = re.findall(r"\]\(\s*<?([^\s)>]+)", text)
    candidates += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*<?([^\s>]+)", text)
    candidates += re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", text, re.I)
    candidates += re.findall(r"`((?:\.\./|/|[A-Za-z]:\\)[^`\n]+)`", text)
    if path.endswith(".json"):

        def refs(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"$ref", "$dynamicRef"} and isinstance(item, str):
                        candidates.append(item)
                    else:
                        refs(item)
            elif isinstance(value, list):
                for item in value:
                    refs(item)

        try:
            refs(json.loads(text))
        except (ValueError, RecursionError) as exc:
            raise invalid(f"JSON 资料格式错误：{path}") from exc
    for candidate in candidates:
        candidate = unquote(candidate)
        url = urlsplit(candidate)
        if url.scheme or url.netloc or candidate.startswith(("/", "\\")) or "\\" in candidate:
            raise invalid(f"文件引用超出技能包：{path}")
        if not url.path:
            continue
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), url.path))
        if resolved == ".." or resolved.startswith("../") or resolved not in paths:
            raise invalid(f"引用文件不在包内：{path}")


@dataclass(frozen=True)
class Package:
    files: dict[str, bytes]
    manifest: tuple[SkillFile, ...]
    metadata: dict[str, Any]
    instructions: str
    settings: SkillSettings

    @property
    def package_hash(self) -> str:
        return digest([f.model_dump(mode="json") for f in self.manifest])

    def archive(self, *, portable: bool = False) -> bytes:
        files = dict(self.files)
        if portable:
            # 授权范围与源版本标识不进入可移植格式，目标渠道按名称和版本重新解析。
            settings = self.settings.model_dump(mode="json", exclude={"allowed_agents"})
            files[PORTABLE] = canonical_json({"format": "skill-package-v1", "settings": settings})
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, data in sorted(files.items()):
                info = zipfile.ZipInfo(path, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, data)
        return out.getvalue()


def validate_files(files: dict[str, bytes], settings: SkillSettings | None = None) -> Package:
    if not files or len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL:
        raise invalid("技能包文件数或解压大小超限")
    seen: set[str] = set()
    for path, data in files.items():
        safe_path(path)
        if path.casefold() in seen:
            raise invalid("技能包存在重复路径")
        seen.add(path.casefold())
        if len(data) > MAX_FILE:
            raise invalid("单个文件超过 1 MiB")
    actual = dict(files)
    portable = actual.pop(PORTABLE, None)
    if portable is not None:
        try:
            value = json.loads(portable)
            if set(value) != {"format", "settings"} or value["format"] != "skill-package-v1":
                raise ValueError("格式不符")
            portable_settings = SkillSettings.model_validate(value["settings"])
            if portable_settings.allowed_agents:
                raise invalid("导入包不能携带来源渠道授权")
            if settings is None:
                settings = portable_settings
        except (ValueError, TypeError) as exc:
            raise invalid("技能包平台元数据不合法") from exc
    if "SKILL.md" not in actual:
        raise invalid("技能包缺少根目录 SKILL.md")
    meta, body = parse_entry(actual["SKILL.md"])
    manifest = []
    for path, data in sorted(actual.items()):
        suffix = PurePosixPath(path).suffix.lower()
        content_type = TEXT_TYPES.get(suffix, "application/octet-stream")
        reason = None
        if suffix not in TEXT_TYPES:
            reason = "脚本执行未启用" if suffix in SCRIPT_SUFFIXES else "暂不支持加载此文件格式"
        else:
            try:
                text = data.decode("utf-8-sig")
                if "\x00" in text:
                    raise ValueError("文本包含空字节")
            except (ValueError, UnicodeDecodeError):
                reason = "文件不是有效的 UTF-8 文本"
            else:
                check_references(path, text, set(actual))
        manifest.append(
            SkillFile(
                relative_path=path,
                content_type=content_type,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                loadable=reason is None,
                unavailable_reason=reason,
            )
        )
    return Package(actual, tuple(manifest), meta, body, settings or SkillSettings())


def decode_archive(value: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise invalid("归档编码不合法") from exc
    if len(data) > MAX_ARCHIVE:
        raise invalid("压缩包超过 8 MiB")
    return data


def unpack(data: bytes, settings: SkillSettings | None = None) -> Package:
    if not data or len(data) > MAX_ARCHIVE:
        raise invalid("压缩包为空或超过 8 MiB")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    count = 0

    def register(path: str, size: int, directory: bool) -> None:
        nonlocal total, count
        path = safe_path(path)
        count += 1
        if path.casefold() in seen or count > MAX_FILES:
            raise invalid("归档包含重复路径或文件数超限")
        seen.add(path.casefold())
        if not directory:
            total += size
        if size < 0 or size > MAX_FILE or total > MAX_TOTAL:
            raise invalid("技能包解压大小超限")

    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for item in archive.infolist():
                    mode = item.external_attr >> 16
                    if (
                        stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or item.flag_bits & 1
                    ):
                        raise invalid("归档不能包含符号链接、特殊文件或加密文件")
                    register(
                        item.filename.rstrip("/") if item.is_dir() else item.filename,
                        item.file_size,
                        item.is_dir(),
                    )
                for item in archive.infolist():
                    if not item.is_dir():
                        with archive.open(item) as stream:
                            content = stream.read(MAX_FILE + 1)
                        if len(content) != item.file_size:
                            raise invalid("归档文件大小与清单不符")
                        files[item.filename] = content
        else:
            if data.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as gzip_stream:
                    data = gzip_stream.read(MAX_TOTAL + MAX_FILES * 2048 + 1)
            if len(data) > MAX_TOTAL + MAX_FILES * 2048:
                raise invalid("归档解压大小超限")
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar_archive:
                for member in tar_archive:
                    if not (member.isfile() or member.isdir()):
                        raise invalid("归档不能包含链接或特殊文件")
                    register(
                        member.name.rstrip("/") if member.isdir() else member.name,
                        member.size,
                        member.isdir(),
                    )
                    if member.isfile():
                        tar_stream = tar_archive.extractfile(member)
                        if tar_stream is None:
                            raise invalid("归档文件不可读")
                        with tar_stream:
                            content = tar_stream.read(MAX_FILE + 1)
                        if len(content) != member.size:
                            raise invalid("归档文件大小与清单不符")
                        files[member.name] = content
        # 文件与目录重名的前缀同样拒绝，避免不同解包器产生歧义。
        for path in files:
            if any(
                parent.as_posix().casefold() in {p.casefold() for p in files}
                for parent in PurePosixPath(path).parents
                if str(parent) != "."
            ):
                raise invalid("归档文件与目录路径冲突")
        return validate_files(files, settings)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, RuntimeError) as exc:
        raise invalid("技能归档损坏或格式不受支持") from exc


def check_export(package: Package) -> None:
    secret = re.compile(
        rb"(?i)(?:api[_-]?key|access[_-]?token|secret|password|credential|authorization)"
        rb"[\"']?\s*[:=]\s*[^\s,}\]]+|-----BEGIN .*PRIVATE KEY-----|sk-[a-zA-Z0-9]{16,}"
    )
    for path, data in package.files.items():
        if PurePosixPath(path).name.lower() in {".env", "credentials", "id_rsa"} or secret.search(
            data
        ):
            raise ServiceError("SKILL_EXPORT_SENSITIVE", "包内可能包含凭据，请移除后再导出", 422)
