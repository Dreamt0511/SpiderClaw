#!/usr/bin/env python3
"""SpiderClaw - 入口文件"""
import sys
import os

ENTRY_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(ENTRY_DIR)
if ENTRY_DIR not in sys.path:
    sys.path.insert(0, ENTRY_DIR)


def main():
    from src.cli.app import app
    app()


if __name__ == "__main__":
    main()
