"""bench 命令行。"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "用法：\n"
            "  bench fixtures run [--skill ID] [--repeat N] [--update]\n"
            "      跑回归测试集（R0.5）\n"
            "  bench skills\n"
            "      列出已注册的 skill 与加载失败的原因\n"
        )
        return 0
    cmd = argv[0]
    if cmd == "fixtures":
        from bench.accuracy.fixtures import main as fixtures_main
        return fixtures_main(argv[1:])
    if cmd == "skills":
        from bench.skills.registry import get_registry
        reg = get_registry()
        for sk in reg.list():
            print(f"  {sk.id}@{sk.manifest.version}  {sk.manifest.name}")
        for name, err in reg.errors.items():
            print(f"  [加载失败] {name}\n{err}")
        return 0
    print(f"未知命令 {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
