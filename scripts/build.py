#!/usr/bin/env python3
"""打包并校验 astrbot_plugin_session_switch。

只依赖标准库。CI 与本地跑同一份逻辑，避免两边校验口径不一致。

用法：
    python scripts/build.py            # 校验 + 打包到 dist/
    python scripts/build.py --check    # 只校验，不打包
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "astrbot_plugin_session_switch"
REQUIRED_METADATA_FIELDS = ("name", "desc", "version", "author")
# 打进 zip 的文件；README/CHANGELOG/LICENSE 一并带上，装完能在插件目录里直接看
PACKAGE_FILES = (
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "logo.png",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
)

errors: list[str] = []


def check_schema() -> dict:
    path = ROOT / "_conf_schema.json"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"_conf_schema.json 解析失败: {exc}")
        return {}
    if not isinstance(schema, dict) or not schema:
        errors.append("_conf_schema.json 必须是非空对象")
        return {}
    for key, spec in schema.items():
        if not isinstance(spec, dict):
            errors.append(f"_conf_schema.json 中 {key} 必须是对象")
            continue
        if "type" not in spec:
            errors.append(f"_conf_schema.json 中 {key} 缺少 type")
        if "description" not in spec:
            errors.append(f"_conf_schema.json 中 {key} 缺少 description")
    print(f"_conf_schema.json OK, {len(schema)} 项")
    return schema


def parse_metadata() -> dict:
    """不依赖 pyyaml 的顶层字段解析，够校验必填字段和版本区间。"""
    text = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    scalars: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z_]\w*):[ \t]*(.*)$", line)
        if match:
            key, value = match.group(1), match.group(2).strip()
            scalars[key] = value.strip('"').strip("'")

    for field in REQUIRED_METADATA_FIELDS:
        if not scalars.get(field):
            errors.append(f"metadata.yaml 缺少必需字段或为空: {field}")

    name = scalars.get("name", "")
    if name and name != PLUGIN_NAME:
        errors.append(f"metadata.yaml 的 name ({name}) 必须与目录名 ({PLUGIN_NAME}) 一致")
    if name and not name.isidentifier():
        errors.append(f"metadata.yaml 的 name ({name}) 必须是合法 Python 标识符")

    spec = scalars.get("astrbot_version", "")
    if spec:
        # PEP 440 子集：">=4.16,<5" / ">=4.16" / "<5"
        parts = [p.strip() for p in spec.split(",") if p.strip()]
        if not parts:
            errors.append(f"astrbot_version 为空区间: {spec!r}")
        for part in parts:
            if not re.fullmatch(r"(>=|<=|==|!=|>|<|~=)\s*\d+(\.\d+)*", part):
                errors.append(f"astrbot_version 不是合法 PEP 440 区间: {part!r}")
    else:
        print("提示: metadata.yaml 未声明 astrbot_version")

    if "repo" not in scalars:
        errors.append("metadata.yaml 缺少 repo，AstrBot 插件市场靠它找仓库")

    if not re.search(r"^support_platforms:", text, re.MULTILINE):
        errors.append("metadata.yaml 缺少 support_platforms")
    else:
        count = len(re.findall(r"^\s{2}- ", text, re.MULTILINE))
        print(f"metadata.yaml OK, support_platforms {count} 项")

    return scalars


def check_readme(schema: dict) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [key for key in schema if f"`{key}`" not in readme]
    if missing:
        errors.append(f"README.md 未说明这些配置项: {', '.join(missing)}")
    else:
        print("README.md 覆盖全部配置项 OK")


def check_files() -> None:
    missing = [name for name in PACKAGE_FILES if not (ROOT / name).exists()]
    if missing:
        errors.append(f"缺少文件: {', '.join(missing)}")
    else:
        print(f"文件清单 OK, {len(PACKAGE_FILES)} 项")


def check_main_py() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    if "__version__" not in source:
        errors.append("main.py 未声明 __version__")
    for hook in ("on_waiting_llm_request", "on_llm_request", "on_decorating_result"):
        if hook not in source:
            errors.append(f"main.py 未引用钩子 {hook}")


def build_zip() -> Path:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / f"{PLUGIN_NAME}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in PACKAGE_FILES:
            # zip 内保留一层同名目录，AstrBot 解压后能直接得到插件目录
            archive.write(ROOT / name, f"{PLUGIN_NAME}/{name}")
    print(f"打包完成: {target} ({target.stat().st_size} bytes)")
    return target


def main() -> int:
    schema = check_schema()
    parse_metadata()
    if schema:
        check_readme(schema)
    check_files()
    check_main_py()

    if errors:
        print("\n=== 校验失败 ===", file=sys.stderr)
        for item in errors:
            print(f" - {item}", file=sys.stderr)
        return 1

    if "--check" not in sys.argv:
        build_zip()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
