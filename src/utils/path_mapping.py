"""路径映射 — 将生产环境运行时路径转换为仓库相对路径"""
import logging

logger = logging.getLogger(__name__)


def apply_path_mapping(runtime_path: str, mapping: dict[str, str]) -> str:
    """将运行时路径转换为仓库相对路径

    规则：按 mapping key 长度降序匹配（最长前缀优先）。
    无匹配时返回原路径。

    Args:
        runtime_path: 生产环境的文件路径，如 "/app/services/order.py"
        mapping: 路径映射规则，如 {"/app/": "src/"}

    Returns:
        映射后的仓库相对路径
    """
    if not mapping or not runtime_path:
        return runtime_path

    sorted_keys = sorted(mapping.keys(), key=len, reverse=True)

    for prefix in sorted_keys:
        if runtime_path.startswith(prefix):
            suffix = runtime_path[len(prefix):]
            result = mapping[prefix] + suffix
            logger.debug(f"路径映射: {runtime_path} -> {result}")
            return result

    return runtime_path
