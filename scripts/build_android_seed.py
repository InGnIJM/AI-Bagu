#!/usr/bin/env python
"""Build a clean Android seed database without touching its source database."""
import argparse
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bagu  # noqa: E402


def _create_empty_seed(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        bagu.prepare_mobile_database(temporary)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成干净的 Android 题库种子")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="只读源题库 SQLite 文件")
    source.add_argument("--empty", action="store_true", help="生成空题库种子")
    parser.add_argument("--output", type=Path, required=True, help="生成的种子数据库路径")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.source is not None and args.source.resolve() == output:
        parser.error("--output 不能覆盖 --source")
    if args.empty:
        _create_empty_seed(output)
        count = 0
    else:
        count = bagu.create_seed_database(args.source, output)
    print(f"已生成种子数据库：{output}（{count} 道题）")


if __name__ == "__main__":
    main()
