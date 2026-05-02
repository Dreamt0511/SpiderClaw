"""traceback 指纹算法测试"""

from src.store.repair_store import compute_traceback_fingerprint


class TestComputeTracebackFingerprint:
    """compute_traceback_fingerprint 测试"""

    def test_same_error_same_fingerprint(self):
        """同一错误在不同路径前缀下应生成相同指纹"""
        tb1 = '''Traceback (most recent call last):
  File "/github/workspace/src/utils.py", line 15
    return data["key"]
KeyError: 'key' '''

        tb2 = '''Traceback (most recent call last):
  File "/home/runner/work/repo/repo/./src/utils.py", line 15
    return data["key"]
KeyError: 'key' '''

        assert compute_traceback_fingerprint(tb1) == compute_traceback_fingerprint(tb2)

    def test_different_error_different_fingerprint(self):
        """不同错误类型应生成不同指纹"""
        tb1 = 'File "app.py", line 10\nValueError: bad value'
        tb2 = 'File "app.py", line 10\nTypeError: bad type'
        assert compute_traceback_fingerprint(tb1) != compute_traceback_fingerprint(tb2)

    def test_different_file_different_fingerprint(self):
        """不同文件应生成不同指纹"""
        tb1 = 'File "app.py", line 10\nValueError: bad'
        tb2 = 'File "other.py", line 10\nValueError: bad'
        assert compute_traceback_fingerprint(tb1) != compute_traceback_fingerprint(tb2)

    def test_same_error_different_line_same_fingerprint(self):
        """同文件同错误类型但不同行号应生成相同指纹（不含行号）"""
        tb1 = 'File "app.py", line 10\nValueError: bad'
        tb2 = 'File "app.py", line 20\nValueError: bad'
        assert compute_traceback_fingerprint(tb1) == compute_traceback_fingerprint(tb2)

    def test_empty_traceback(self):
        """空输入返回空字符串"""
        assert compute_traceback_fingerprint("") == ""

    def test_fingerprint_length(self):
        """指纹长度应为 12"""
        tb = 'File "app.py", line 1\nRuntimeError: oops'
        fp = compute_traceback_fingerprint(tb)
        assert len(fp) == 12
        assert all(c in "0123456789abcdef" for c in fp)

    def test_framework_code_excluded(self):
        """site-packages 等框架代码应被排除"""
        tb1 = '''Traceback (most recent call last):
  File "/usr/lib/python3.12/site-packages/flask/app.py", line 123
    response = self.handle_exception(e)
  File "/app/myapp.py", line 42
    result = process()
ValueError: invalid'''

        tb2 = '''Traceback (most recent call last):
  File "/app/myapp.py", line 42
    result = process()
ValueError: invalid'''

        # 两者都应基于 myapp.py + ValueError 生成指纹
        fp1 = compute_traceback_fingerprint(tb1)
        fp2 = compute_traceback_fingerprint(tb2)
        assert fp1 == fp2

    def test_error_message_included(self):
        """不同错误消息应生成不同指纹"""
        tb1 = 'File "app.py", line 1\nKeyError: "name"'
        tb2 = 'File "app.py", line 1\nKeyError: "age"'
        assert compute_traceback_fingerprint(tb1) != compute_traceback_fingerprint(tb2)

    def test_no_error_type_uses_raw_content(self):
        """无法提取错误类型时，使用原始内容前200字符"""
        tb = "some random text without any error pattern"
        fp = compute_traceback_fingerprint(tb)
        assert len(fp) == 12

    def test_real_world_traceback(self):
        """真实 traceback 测试"""
        tb = """Traceback (most recent call last):
  File "/app/main.py", line 10, in <module>
    result = 1 / 0
ZeroDivisionError: division by zero"""
        fp = compute_traceback_fingerprint(tb)
        assert len(fp) == 12
        # 同样的 traceback 应该一致
        assert compute_traceback_fingerprint(tb) == fp
