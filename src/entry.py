"""SpiderClaw 控制台入口（供 console_scripts 使用）"""
import os
import sys


def main():
    # 找到项目根目录：从 src/entry.py 向上两级
    this_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(this_file))
    os.chdir(project_root)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from src.cli.app import app

    app()
