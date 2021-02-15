"""
HttpRunner loader

- check: validate api/testcase/testsuite data structure with JSON schema
    检查:验证api/testcase/testsuite数据结构与JSON模式
- locate: locate debugtalk.py, make it's dir as project root path
    定位:定位debugtalk。py，使它的dir作为项目根路径
- load: load testcase files and relevant data, including debugtalk.py, .env, yaml/json api/testcases, csv, etc.
    加载:加载testcase文件和相关数据，包括debugtalk。.env, yaml/json api/testcases, csv等等。
- buildup: assemble loaded content to httprunner testcase/testsuite data structure
    构建:将加载的内容组装到httprunner testcase/testsuite数据结构中

"""

from httprunner.loader.check import is_test_path, is_test_content, JsonSchemaChecker
from httprunner.loader.locate import get_project_working_directory as get_pwd, \
    init_project_working_directory as init_pwd
from httprunner.loader.load import load_csv_file, load_builtin_functions
from httprunner.loader.buildup import load_cases, load_project_data

__all__ = [
    "is_test_path",
    "is_test_content",
    "JsonSchemaChecker",
    "get_pwd",
    "init_pwd",
    "load_csv_file",
    "load_builtin_functions",
    "load_project_data",
    "load_cases"
]
