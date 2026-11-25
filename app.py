"""传统入口：``python app.py`` 默认输出样例复核看板。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

# 即使使用 ``python -I app.py``，也显式把本项目根目录加入导入路径。
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_governance.cli import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        argv = ["sample-report", "--date", date.today().isoformat()]
    raise SystemExit(main(argv))
