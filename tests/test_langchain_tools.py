"""LangChain工具测试"""
import os
import tempfile
import pytest
from src.agent.tools import (
    set_tool_context,
    read_file,
    write_file,
    search_files,
    search_code,
    parse_python_errors
)


class TestLangChainTools:
    """LangChain标准工具测试"""

    def setup_method(self):
        """测试前设置"""
        # 创建临时测试目录
        self.tmp_dir = tempfile.mkdtemp(prefix="spiderclaw_test_")
        set_tool_context({
            "repo_path": self.tmp_dir,
            "github_token": "test_token"
        })

        # 创建测试文件
        self.test_file = os.path.join(self.tmp_dir, "test.py")
        with open(self.test_file, "w") as f:
            f.write("""
def divide(a, b):
    return a / b

def hello():
    return "Hello World"

# 测试错误
# divide(10, 0)
""")

    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.tmp_dir)

    def test_read_file(self):
        """测试读取文件工具"""
        content = read_file.invoke({"file_path": "test.py"})
        assert "def divide(a, b):" in content
        assert "def hello():" in content

    def test_read_file_not_found(self):
        """测试读取不存在的文件"""
        result = read_file.invoke({"file_path": "nonexistent.py"})
        assert result.startswith("Error: 文件")

    def test_write_file(self):
        """测试写入文件工具"""
        result = write_file.invoke({
            "file_path": "new_test.py",
            "content": "def test_func():\n    return 42"
        })
        assert result == "Success"

        # 验证文件内容
        new_file = os.path.join(self.tmp_dir, "new_test.py")
        assert os.path.exists(new_file)
        with open(new_file, "r") as f:
            content = f.read()
        assert "def test_func():" in content
        assert "return 42" in content

    def test_write_file_path_traversal(self):
        """测试路径穿越防护"""
        result = write_file.invoke({
            "file_path": "../etc/passwd",
            "content": "malicious content"
        })
        assert result.startswith("Error: 路径")

    def test_search_files(self):
        """测试搜索文件工具"""
        # 创建多个Python文件
        for i in range(3):
            with open(os.path.join(self.tmp_dir, f"file_{i}.py"), "w") as f:
                f.write(f"# 文件 {i}")

        # 创建非Python文件
        with open(os.path.join(self.tmp_dir, "README.md"), "w") as f:
            f.write("# README")

        files = search_files.invoke({"pattern": "**/*.py"})
        assert len(files) >= 4  # test.py + 3个新文件
        assert "test.py" in files
        assert "file_0.py" in files
        assert "file_1.py" in files
        assert "file_2.py" in files

    def test_search_code(self):
        """测试代码搜索工具"""
        results = search_code.invoke({"keyword": "def divide"})
        assert len(results) == 1
        assert results[0]["file_path"] == "test.py"
        assert results[0]["line_number"] == 2
        assert "def divide(a, b):" in results[0]["line_content"]

    def test_parse_python_errors(self):
        """测试Python错误解析工具"""
        log_content = """
Traceback (most recent call last):
  File "test.py", line 8, in <module>
    result = divide(10, 0)
  File "test.py", line 3, in divide
    return a / b
ZeroDivisionError: division by zero

Another error:
ValueError: invalid value
"""
        errors = parse_python_errors.invoke({"log_content": log_content})
        assert len(errors) == 2

        # 检查Traceback错误
        tb_error = errors[0]
        assert tb_error["type"] == "traceback"
        assert tb_error["error_type"] == "ZeroDivisionError"
        assert tb_error["error_message"] == "division by zero"
        assert tb_error["file_path"] == "test.py"
        assert tb_error["line_number"] == 3

        # 检查简单错误
        simple_error = errors[1]
        assert simple_error["type"] == "simple"
        assert simple_error["error_type"] == "ValueError"
        assert simple_error["error_message"] == "invalid value"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
