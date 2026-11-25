import sys

from .cli import main

if __name__ == "__main__":
    # 无参数时直接给出内置样例看板；传 --help 仍显示完整命令帮助。
    raise SystemExit(main(sys.argv[1:] or ["sample-report"]))
