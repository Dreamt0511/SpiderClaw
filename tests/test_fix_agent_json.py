"""测试FixAgent的JSON健壮解析"""
import json
from src.agent.subagents.fix_agent import FixAgent


def test_json_standard():
    """标准JSON应该直接通过"""
    js = json.dumps({
        "fix_description": "修复bug",
        "modified_files": ["test.py"],
        "code_changes": {"test.py": "print('ok')"},
    }, ensure_ascii=False)
    result = FixAgent._parse_json_safely(js)
    assert result is not None
    assert result["fix_description"] == "修复bug"


def test_json_trailing_comma():
    """尾随逗号应该被修复"""
    js = '''{
        "fix_description": "修复bug",
        "modified_files": ["test.py",],
        "code_changes": {"test.py": "print(1)",}
    }'''
    result = FixAgent._parse_json_safely(js)
    assert result is not None
    assert "test.py" in result["code_changes"]


def test_json_unescaped_newlines():
    """字符串值中未转义的换行符应该被处理"""
    js = '''{
        "fix_description": "修复bug",
        "modified_files": ["test.py"],
        "code_changes": {
            "test.py": "def foo():
    return 1
"
        }
    }'''
    result = FixAgent._parse_json_safely(js)
    assert result is not None
    assert "test.py" in result["code_changes"]
    assert "def foo():" in result["code_changes"]["test.py"]


def test_json_unescaped_quotes_in_code():
    """代码内容中未转义的双引号应该被处理"""
    js = '''{
        "fix_description": "修复bug",
        "modified_files": ["test.py"],
        "code_changes": {
            "test.py": "print(u2["hits"])"
        }
    }'''
    result = FixAgent._parse_json_safely(js)
    assert result is not None
    assert "test.py" in result["code_changes"]


def test_json_large_llm_output():
    """模拟真正的LLM输出（含中文和大量代码）"""
    js = '''{
        "fix_description": "1. 修复导入问题 2. 修复安全问题",
        "modified_files": ["test_syntax_errors.py"],
        "code_changes": {
            "test_syntax_errors.py": "import os\\nimport ast\\nfrom functools import lru_cache\\n\\n# 环境变量注入安全\\nSECRET = os.environ.get(\\"EVIL\\", \\"2+2\\")\\nresult = ast.literal_eval(SECRET)\\n\\ndef add(x, cache=None):\\n    if cache is None:\\n        cache = []\\n    cache.append(x)\\n    return cache\\n\\ndef main():\\n    print(add(1))\\n\\nif __name__ == \\"__main__\\":\\n    main()"
        }
    }'''
    result = FixAgent._parse_json_safely(js)
    assert result is not None
    assert result["fix_description"].startswith("1. 修复导入问题")
    assert "test_syntax_errors.py" in result["code_changes"]
    assert "import os" in result["code_changes"]["test_syntax_errors.py"]


def test_json_missing_fields():
    """最坏情况：解析失败时至少返回空结构"""
    result = FixAgent._parse_json_safely("{broken json:")
    assert result is None
