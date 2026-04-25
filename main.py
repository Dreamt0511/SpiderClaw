#!/usr/bin/env python3
"""项目入口脚本"""
# 提前配置日志和编码
from src.utils.logging import setup_logging
setup_logging()

from src.cli.app import app

if __name__ == "__main__":
    app()
