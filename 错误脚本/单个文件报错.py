"""
演示脚本：包含 4 个简单 Bug，直接运行测试即可触发失败
用法：python demo_bug.py
"""

import traceback
import sys


# ==================== 有 Bug 的函数 ====================

def divide(a, b):
    """除法 - 缺少除零检查"""
    return a / b  # 🐛 b=0 时 ZeroDivisionError


def get_average(numbers):
    """平均值 - 空列表时除零"""
    return sum(numbers) / len(numbers)  # 🐛 空列表 ZeroDivisionError


def get_item(data, index):
    """取元素 - 缺少越界检查"""
    return data[index]  # 🐛 索引越界 IndexError


def parse_config(config_dict, key):
    """取值 - 缺少键检查"""
    return config_dict[key]  # 🐛 键不存在 KeyError


# ==================== 简易测试框架 ====================

PASS = 0
FAIL = 0


def test(name, func, *args, **kwargs):
    """运行单个测试并打印结果"""
    global PASS, FAIL
    try:
        expected = kwargs.get("expected")
        result = func(*args)

        if expected is not None and result != expected:
            raise AssertionError(f"期望 {expected}，实际 {result}")

        print(f"  ✅ {name}")
        PASS += 1
    except Exception as e:
        print(f"  ❌ {name} -> {type(e).__name__}: {e}")
        print(f"     Traceback:\n{traceback.format_exc()[-200:]}")
        FAIL += 1


# ==================== 运行所有测试 ====================

print("=" * 50)
print("运行测试...")
print("=" * 50)

# divide 测试
print("\n📌 divide()")
test("正常除法", divide, 10, 2, expected=5.0)
test("除零错误", divide, 10, 0)  #  会失败

# get_average 测试
print("\n📌 get_average()")
test("正常平均值", get_average, [1, 2, 3], expected=2.0)
test("空列表", get_average, [])  #  会失败

# get_item 测试
print("\n📌 get_item()")
test("正常取值", get_item, [1, 2, 3], 1, expected=2)
test("索引越界", get_item, [1, 2, 3], 10)  #  会失败

# parse_config 测试
print("\n📌 parse_config()")
test("键存在", parse_config, {"host": "localhost"}, "host", expected="localhost")
test("键缺失", parse_config, {}, "port")  #  会失败

# ==================== 汇总 ====================
print("\n" + "=" * 50)
print(f"结果: {PASS} 通过 / {FAIL} 失败 (共 {PASS + FAIL} 个测试)")
print("=" * 50)

sys.exit(FAIL)