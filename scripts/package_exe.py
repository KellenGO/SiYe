"""Validate and archive the PyInstaller Windows distribution."""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path


FORBIDDEN_PARTS = {
    ".git", ".github", ".env", "browser_data", "logs", "node_modules",
    "webui/src", "tests", "__pycache__",
}

# 注意：**不要**把 "data" 加进上面那个集合。这里是按路径分段匹配的，加上去会把依赖自带的
# 合法目录一起挡掉 —— 2026-09-15 实测：OpenCV 的 `_internal/cv2/data`（haarcascade 分类器）
# 和 xhshow 的 `_internal/xhshow/data` 都会命中，构建直接失败。
# 用户数据只会出现在**发布目录的顶层**（`data/`、`browser_data/`、`.cache`），
# 那几个位置由 validate() 里的顶层检查和上面的 browser_data 覆盖，已经够用。


def validate(distribution: Path) -> None:
    required = (
        distribution / "SiYe.exe",
        distribution / "四野.exe",
        distribution / "browser_extension",
        distribution / "LICENSE",
        distribution / "README.md",
        distribution / "RELEASE_VERSION",
    )
    missing = [str(path.relative_to(distribution)) for path in required
               if not path.exists()]
    if missing:
        raise AssertionError(f"missing executable entries: {', '.join(missing)}")
    # 运行过的目录可能含本机收藏数据库；禁止随发行包上传。
    for name in ("data", ".cache"):
        if (distribution / name).exists():
            raise AssertionError(f"user data must not be packaged: {name}")

    bad = []
    for path in distribution.rglob("*"):
        relative = path.relative_to(distribution).as_posix()
        if any(part in relative.split("/") for part in FORBIDDEN_PARTS):
            bad.append(relative)
    if (distribution / "webui" / "src").exists():
        bad.append("webui/src")
    if bad:
        raise AssertionError(f"forbidden executable entries: {', '.join(bad[:20])}")


def archive(distribution: Path, output_dir: Path) -> tuple[Path, Path]:
    validate(distribution)
    archive_path = Path(shutil.make_archive(
        str(output_dir / "SiYe-Windows-x64"),
        "zip",
        root_dir=distribution.parent,
        base_dir=distribution.name,
    ))
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{digest}  {archive_path.name}\n", encoding="ascii")
    return archive_path, checksum_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", type=Path,
                        default=Path("dist") / "SiYe")
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    distribution = args.distribution.resolve()
    if args.validate_only:
        validate(distribution)
        print(f"validated executable distribution: {distribution}")
        return 0
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path, checksum_path = archive(distribution, output_dir)
    print(f"validated executable distribution: {distribution}")
    print(f"created archive: {archive_path}")
    print(f"created checksum: {checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
