"""pytest配置文件"""
import sys
from pathlib import Path

# 将src目录添加到Python路径
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir / "src"))

# pytest配置
pytest_plugins = []
