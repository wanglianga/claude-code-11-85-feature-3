"""用药覆盖日历命令行核验入口。

用法（在仓库根目录）：
    .venv/bin/python backend/verify_coverage.py

会连接 config.DATABASE_URL 指向的库，执行 7 项断言并以退出码反映结果
（全部通过 exit 0，否则 exit 1）。若尚未播种，先执行：
    PYTHONPATH=backend .venv/bin/python -m app.seed
"""
import json
import sys

from app.database import Base, SessionLocal, engine
from app.coverage_verify import run_coverage_verify


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = run_coverage_verify(db)
    finally:
        db.close()

    print(f"COVERAGE VERIFY {'ALL PASS' if result['overall'] else 'FAILED'} "
          f"{result['passed']}/{result['total']}")
    for c in result["checks"]:
        mark = "PASS" if c["passed"] else "FAIL"
        print(f"[{mark}] {c['key']} — {c['name']}")
        if not c["passed"]:
            print("    expected:", c["expected"])
            print("    actual  :", json.dumps(c["actual"], ensure_ascii=False))
    return 0 if result["overall"] else 1


if __name__ == "__main__":
    sys.exit(main())
