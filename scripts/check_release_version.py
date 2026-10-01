"""Validate that every release-facing version source agrees."""

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import cast

_TAG_PATTERN = re.compile(r"v(?P<version>\d+\.\d+\.\d+)")


class VersionError(ValueError):
    """Raised when release version sources disagree."""


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VersionError(f"{path.as_posix()} must contain a JSON object")
    return cast(dict[str, object], value)


def _load_toml(path: Path) -> dict[str, object]:
    return cast(
        dict[str, object], tomllib.loads(path.read_text(encoding="utf-8"))
    )


def _object_dict(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise VersionError(f"missing version field: {field}")
    return cast(dict[str, object], value)


def _string_at(data: dict[str, object], *keys: str) -> str:
    value: object = data
    for key in keys:
        mapping = _object_dict(value, ".".join(keys))
        if key not in mapping:
            raise VersionError(f"missing version field: {'.'.join(keys)}")
        value = mapping[key]
    if not isinstance(value, str) or not value:
        raise VersionError(f"invalid version field: {'.'.join(keys)}")
    return value


def _version_sources(root: Path) -> dict[str, str]:
    cargo_lock = _load_toml(root / "frontend/src-tauri/Cargo.lock")
    packages = cargo_lock.get("package")
    if not isinstance(packages, list):
        raise VersionError("frontend/src-tauri/Cargo.lock has no packages")
    package_items = cast(list[object], packages)
    cargo_package = next(
        (
            cast(dict[str, object], item)
            for item in package_items
            if isinstance(item, dict)
            and cast(dict[str, object], item).get("name") == "nyx"
        ),
        None,
    )
    if not isinstance(cargo_package, dict):
        raise VersionError("frontend/src-tauri/Cargo.lock has no nyx package")

    return {
        "pyproject.toml": _string_at(
            _load_toml(root / "pyproject.toml"), "project", "version"
        ),
        "frontend/package.json": _string_at(
            _load_json(root / "frontend/package.json"), "version"
        ),
        "frontend/package-lock.json": _string_at(
            _load_json(root / "frontend/package-lock.json"), "version"
        ),
        "frontend/src-tauri/tauri.conf.json": _string_at(
            _load_json(root / "frontend/src-tauri/tauri.conf.json"), "version"
        ),
        "frontend/src-tauri/Cargo.toml": _string_at(
            _load_toml(root / "frontend/src-tauri/Cargo.toml"),
            "package",
            "version",
        ),
        "frontend/src-tauri/Cargo.lock": _string_at(cargo_package, "version"),
    }


def validate_versions(root: Path, tag: str | None = None) -> str:
    """Return the common project version or raise on source/tag drift."""
    versions = _version_sources(root)
    expected = versions["pyproject.toml"]
    mismatches = [
        f"{path}: {version}"
        for path, version in versions.items()
        if version != expected
    ]
    if mismatches:
        raise VersionError(
            f"version sources do not match pyproject.toml ({expected}): "
            + ", ".join(mismatches)
        )
    if tag is not None:
        match = _TAG_PATTERN.fullmatch(tag)
        if match is None:
            raise VersionError(f"tag {tag} must use vX.Y.Z")
        if match.group("version") != expected:
            raise VersionError(f"tag {tag} does not match {expected}")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        version = validate_versions(args.root.resolve(), args.tag)
    except (
        OSError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        VersionError,
    ) as error:
        print(f"release version check failed: {error}")
        return 1
    print(f"release version: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
