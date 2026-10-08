"""从技能源码按需生成可导入 ZIP，产物默认写入本地忽略目录。"""

import argparse
from pathlib import Path

from creativity_service.modules.skills.packages import validate_files

ROOT = Path(__file__).resolve().parent
NAMES = ("text-brief", "archive-answer")


def build_package(name: str) -> bytes:
    if name not in NAMES:
        raise ValueError(f"未知技能示例：{name}")
    source = ROOT / name
    files = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    return validate_files(files).archive(portable=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成示例技能包")
    parser.add_argument("--output", type=Path, default=Path(".local/examples/skills"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        (args.output / f"{name}.zip").write_bytes(build_package(name))


if __name__ == "__main__":
    main()
