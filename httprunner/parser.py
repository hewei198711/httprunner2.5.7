# encoding: utf-8

import ast
import builtins
import collections
import json
import re

from httprunner import exceptions, utils, loader
from httprunner import logger
from httprunner.compat import basestring, numeric_types, str

# use $$ to escape $ notation
dolloar_regex_compile = re.compile(r"\$\$")
# variable notation, e.g. ${var} or $var
variable_regex_compile = re.compile(r"\$\{(\w+)\}|\$(\w+)")
# function notation, e.g. ${func1($var_1, $var_3)}
function_regex_compile = re.compile(r"\$\{(\w+)\(([\$\w\.\-/\s=,]*)\)\}")

""" Store parse failed api/testcase/testsuite file path
    存储解析失败的api/testcase/testsuite文件路径
"""
parse_failed_testfiles = {}  # 解析失败的测试文件


def get_parse_failed_testfiles():
    return parse_failed_testfiles


def parse_string_value(str_value):
    """ parse string to number if possible
        如果可能的话，将字符串解析为数字,列表，字典
    e.g. "123" => 123
         "12.2" => 12.3
         "abc" => "abc"
         "$var" => "$var"
    """
    try:
        return ast.literal_eval(str_value)
    except ValueError:
        return str_value
    except SyntaxError:
        # e.g. $var, ${func}
        return str_value


def is_var_or_func_exist(content):
    """ check if variable or function exist
        检查是否有引用变量或函数存在
    """
    if not isinstance(content, basestring):
        return False

    try:
        match_start_position = content.index("$", 0)  # 匹配起始位置($的下标在哪里)
    except ValueError:
        return False

    while match_start_position < len(content):  # $的下标不在最后
        dollar_match = dolloar_regex_compile.match(content, match_start_position)
        # content匹配$$成功，返回一个匹配的对象，否则返回None
        # 如果匹配到$$,那就再从下一位开始匹配
        if dollar_match:
            match_start_position = dollar_match.end()
            continue

        func_match = function_regex_compile.match(content, match_start_position)
        if func_match:
            return True

        var_match = variable_regex_compile.match(content, match_start_position)  # 变量正则表达式编译
        if var_match:
            return True

        return False


def regex_findall_variables(content):
    """ extract all variable names from content, which is in format $variable
        从content中提取所有变量名，其格式为$variable

    Args:
        content (str): string content

    Returns:
        list: variables list extracted from string content
        从字符串内容中提取的变量列表

    Examples:
        >>> regex_findall_variables("$variable")
        ["variable"]

        >>> regex_findall_variables("/blog/$postid")
        ["postid"]

        >>> regex_findall_variables("/$var1/$var2")
        ["var1", "var2"]

        >>> regex_findall_variables("abc")
        []

    """
    try:
        vars_list = []
        for var_tuple in variable_regex_compile.findall(content):
            vars_list.append(
                var_tuple[0] or var_tuple[1]
            )
        return vars_list
    except TypeError:
        return []


def regex_findall_functions(content):
    """ extract all functions from string content, which are in format ${fun()}

    Args:
        content (str): string content

    Returns:
        list: functions list extracted from string content

    Examples:
        >>> regex_findall_functions("${func(5)}")
        ["func(5)"]

        >>> regex_findall_functions("${func(a=1, b=2)}")
        ["func(a=1, b=2)"]

        >>> regex_findall_functions("/api/1000?_t=${get_timestamp()}")
        ["get_timestamp()"]

        >>> regex_findall_functions("/api/${add(1, 2)}")
        ["add(1, 2)"]

        >>> regex_findall_functions("/api/${add(1, 2)}?_t=${get_timestamp()}")
        ["add(1, 2)", "get_timestamp()"]

    """
    try:
        return function_regex_compile.findall(content)
    except TypeError:
        return []


def parse_parameters(parameters, variables_mapping=None, functions_mapping=None):
    """ parse parameters and generate cartesian product.
        解析参数并生成笛卡尔积

    Args:
        parameters (list) parameters: parameter name and value in list参数(列表)参数:列表中的参数名称和值
            parameter value may be in three types:参数值可以有三种类型
                (1) data list, e.g. ["iOS/10.1", "iOS/10.2", "iOS/10.3"]
                (2) call built-in parameterize function, "${parameterize(account.csv)}"
                (3) call custom function in debugtalk.py, "${gen_app_version()}"

        variables_mapping (dict): variables mapping loaded from testcase config从testcase配置加载的变量映射
        functions_mapping (dict): functions mapping loaded from debugtalk.py函数映射:从debugtalk.py加载的函数映射

    Returns:
        list: cartesian product list笛卡儿产品列表

    Examples:
        >>> parameters = [
            {"user_agent": ["iOS/10.1", "iOS/10.2", "iOS/10.3"]},
            {"username-password": "${parameterize(account.csv)}"},
            {"app_version": "${gen_app_version()}"}
        ]
        >>> parse_parameters(parameters)

    """
    variables_mapping = variables_mapping or {}
    functions_mapping = functions_mapping or {}
    parsed_parameters_list = []

    parameters = utils.ensure_mapping_format(parameters)  # 确保映射的格式是字典
    for parameter_name, parameter_content in parameters.items():
        parameter_name_list = parameter_name.split("-")

        if isinstance(parameter_content, list):
            # (1) data list
            # e.g. {"app_version": ["2.8.5", "2.8.6"]}
            #       => [{"app_version": "2.8.5", "app_version": "2.8.6"}]
            # e.g. {"username-password": [["user1", "111111"], ["test2", "222222"]}
            #       => [{"username": "user1", "password": "111111"}, {"username": "user2", "password": "222222"}]
            parameter_content_list = []
            for parameter_item in parameter_content:
                if not isinstance(parameter_item, (list, tuple)):
                    # "2.8.5" => ["2.8.5"]
                    parameter_item = [parameter_item]

                # ["app_version"], ["2.8.5"] => {"app_version": "2.8.5"}
                # ["username", "password"], ["user1", "111111"] => {"username": "user1", "password": "111111"}
                parameter_content_dict = dict(zip(parameter_name_list, parameter_item))

                parameter_content_list.append(parameter_content_dict)
        else:
            # (2) & (3)
            parsed_variables_mapping = parse_variables_mapping(
                variables_mapping
            )
            parsed_parameter_content = eval_lazy_data(
                parameter_content,
                parsed_variables_mapping,
                functions_mapping
            )
            if not isinstance(parsed_parameter_content, list):
                raise exceptions.ParamsError("parameters syntax error!")

            parameter_content_list = []
            for parameter_item in parsed_parameter_content:
                if isinstance(parameter_item, dict):
                    # get subset by parameter name通过参数名称获取子集
                    # {"app_version": "${gen_app_version()}"}
                    # gen_app_version() => [{'app_version': '2.8.5'}, {'app_version': '2.8.6'}]
                    # {"username-password": "${get_account()}"}
                    # get_account() => [
                    #       {"username": "user1", "password": "111111"},
                    #       {"username": "user2", "password": "222222"}
                    # ]
                    parameter_dict = {key: parameter_item[key] for key in parameter_name_list}
                elif isinstance(parameter_item, (list, tuple)):
                    # {"username-password": "${get_account()}"}
                    # get_account() => [("user1", "111111"), ("user2", "222222")]
                    parameter_dict = dict(zip(parameter_name_list, parameter_item))
                elif len(parameter_name_list) == 1:
                    # {"user_agent": "${get_user_agent()}"}
                    # get_user_agent() => ["iOS/10.1", "iOS/10.2"]
                    parameter_dict = {
                        parameter_name_list[0]: parameter_item
                    }

                parameter_content_list.append(parameter_dict)

        parsed_parameters_list.append(parameter_content_list)

    return utils.gen_cartesian_product(*parsed_parameters_list)  # 创笛卡儿积


def get_uniform_comparator(comparator):
    """ convert comparator alias to uniform name
        将比较器别名转换为统一名称
    """
    if comparator in ["eq", "equals", "==", "is"]:
        return "equals"
    elif comparator in ["lt", "less_than"]:
        return "less_than"  # 小于
    elif comparator in ["le", "less_than_or_equals"]:
        return "less_than_or_equals"  # 小于等于
    elif comparator in ["gt", "greater_than"]:
        return "greater_than"  # 大于
    elif comparator in ["ge", "greater_than_or_equals"]:
        return "greater_than_or_equals"  # 大于等于
    elif comparator in ["ne", "not_equals"]:
        return "not_equals"  # 不等于
    elif comparator in ["str_eq", "string_equals"]:
        return "string_equals"  # 字符串匹配
    elif comparator in ["len_eq", "length_equals", "count_eq"]:
        return "length_equals"  # 长度相等
    elif comparator in ["len_gt", "count_gt", "length_greater_than", "count_greater_than"]:
        return "length_greater_than"  # 长度大于
    elif comparator in ["len_ge", "count_ge", "length_greater_than_or_equals",
                        "count_greater_than_or_equals"]:
        return "length_greater_than_or_equals"  # 长度大于等于
    elif comparator in ["len_lt", "count_lt", "length_less_than", "count_less_than"]:
        return "length_less_than" # 长度小于
    elif comparator in ["len_le", "count_le", "length_less_than_or_equals",
                        "count_less_than_or_equals"]:
        return "length_less_than_or_equals"  # 长度小于或等于
    else:
        return comparator


def uniform_validator(validator):
    """ unify validator统一验证器

    Args:
        validator (dict): validator maybe in two formats:

            format1: this is kept for compatiblity with the previous versions.
                    这样做是为了与以前的版本兼容
                {"check": "status_code", "comparator": "eq", "expect": 201}
                {"check": "$resp_body_success", "comparator": "eq", "expect": True}
            format2: recommended new version, {comparator: [check_item, expected_value]}
                {'eq': ['status_code', 201]}
                {'eq': ['$resp_body_success', True]}

    Returns
        dict: validator info

            {
                "check": "status_code",
                "expect": 201,
                "comparator": "equals"
            }

    """
    if not isinstance(validator, dict):
        raise exceptions.ParamsError("invalid validator: {}".format(validator))

    if "check" in validator and "expect" in validator:
        # format1
        check_item = validator["check"]
        expect_value = validator["expect"]
        comparator = validator.get("comparator", "eq")

    elif len(validator) == 1:
        # format2
        comparator = list(validator.keys())[0]
        compare_values = validator[comparator]

        if not isinstance(compare_values, list) or len(compare_values) != 2:
            raise exceptions.ParamsError("invalid validator: {}".format(validator))

        check_item, expect_value = compare_values

    else:
        raise exceptions.ParamsError("invalid validator: {}".format(validator))

    # uniform comparator, e.g. lt => less_than, eq => equals
    comparator = get_uniform_comparator(comparator)

    return {
        "check": check_item,
        "expect": expect_value,
        "comparator": comparator
    }


def _convert_validators_to_mapping(validators):
    """ convert validators list to mapping.将验证器列表转换为映射

    Args:
        validators (list): validators in list

    Returns:
        dict: validators mapping, use (check, comparator) as key.

    Examples:
        >>> validators = [
            {"check": "v1", "expect": 201, "comparator": "eq"},
            {"check": {"b": 1}, "expect": 200, "comparator": "eq"}
        ]
        >>> print(_convert_validators_to_mapping(validators))
        {
            ("v1", "eq"): {"check": "v1", "expect": 201, "comparator": "eq"},
            ('{"b": 1}', "eq"): {"check": {"b": 1}, "expect": 200, "comparator": "eq"}
        }

    """
    validators_mapping = {}

    for validator in validators:
        if not isinstance(validator["check"], collections.Hashable):
            check = json.dumps(validator["check"])
        else:
            check = validator["check"]

        key = (check, validator["comparator"])
        validators_mapping[key] = validator

    return validators_mapping


def extend_validators(raw_validators, override_validators):
    """ extend raw_validators with override_validators.
        使用覆盖验证器扩展原始验证器
        override_validators will merge and override raw_validators.
        覆盖验证器将合并覆盖原始验证器-更新验证器

    Args:
        raw_validators (dict):
        override_validators (dict): 覆盖验证器

    Returns:
        list: extended validators 扩展的验证器

    Examples:
        >>> raw_validators = [{'eq': ['v1', 200]}, {"check": "s2", "expect": 16, "comparator": "len_eq"}]
        >>> override_validators = [{"check": "v1", "expect": 201}, {'len_eq': ['s3', 12]}]
        >>> extend_validators(raw_validators, override_validators)
            [
                {"check": "v1", "expect": 201, "comparator": "eq"},
                {"check": "s2", "expect": 16, "comparator": "len_eq"},
                {"check": "s3", "expect": 12, "comparator": "len_eq"}
            ]

    """

    if not raw_validators:
        return override_validators

    elif not override_validators:
        return raw_validators

    else:
        def_validators_mapping = _convert_validators_to_mapping(raw_validators)
        ref_validators_mapping = _convert_validators_to_mapping(override_validators)

        def_validators_mapping.update(ref_validators_mapping)
        return list(def_validators_mapping.values())


###############################################################################
##  parse content with variables and functions mapping
###############################################################################

def get_mapping_variable(variable_name, variables_mapping):
    """ get variable from variables_mapping.
        从变量映射中获取变量

    Args:
        variable_name (str): variable name
        variables_mapping (dict): variables mapping

    Returns:
        mapping variable value.

    Raises:
        exceptions.VariableNotFound: variable is not found.

    """
    try:
        return variables_mapping[variable_name]
    except KeyError:
        raise exceptions.VariableNotFound("{} is not found.".format(variable_name))


def get_mapping_function(function_name, functions_mapping):
    """ get function from functions_mapping,
        从函数映射中获取函数
        if not found, then try to check if builtin function.
        如果没有找到，然后尝试检查内置函数

    Args:
        function_name (str): function name
        functions_mapping (dict): functions mapping

    Returns:
        mapping function object.

    Raises:
        exceptions.FunctionNotFound: function is neither defined in debugtalk.py nor builtin.
        函数既不是在debugtalk.py中定义的，也不是内置函数。

    """
    if function_name in functions_mapping:
        return functions_mapping[function_name]

    elif function_name in ["parameterize", "P"]:
        return loader.load_csv_file

    elif function_name in ["environ", "ENV"]:
        return utils.get_os_environ

    elif function_name in ["multipart_encoder", "multipart_content_type"]:
        # extension for upload test 上传测试扩展
        from httprunner.ext import uploader
        return getattr(uploader, function_name)

    try:
        # check if HttpRunner builtin functions 检查HttpRunner内置函数
        built_in_functions = loader.load_builtin_functions()
        return built_in_functions[function_name]
    except KeyError:
        pass

    try:
        # check if Python builtin functions 检查Python内建函数
        return getattr(builtins, function_name)
    except AttributeError:  # 属性错误
        pass

    raise exceptions.FunctionNotFound("{} is not found.".format(function_name))


def parse_function_params(params):
    """ parse function params to args and kwargs.
        解析函数参数为arg和kwarg

    Args:
        params (str): function param in string 函数参数字符串

    Returns:
        dict: function meta dict

            {
                "args": [],
                "kwargs": {}
            }

    Examples:
        >>> parse_function_params("")
        {'args': [], 'kwargs': {}}

        >>> parse_function_params("5")
        {'args': [5], 'kwargs': {}}

        >>> parse_function_params("1, 2")
        {'args': [1, 2], 'kwargs': {}}

        >>> parse_function_params("a=1, b=2")
        {'args': [], 'kwargs': {'a': 1, 'b': 2}}

        >>> parse_function_params("1, 2, a=3, b=4")
        {'args': [1, 2], 'kwargs': {'a':3, 'b':4}}

    """
    function_meta = {  # 元函数
        "args": [],
        "kwargs": {}
    }

    params_str = params.strip()
    if params_str == "":
        return function_meta

    args_list = params_str.split(',')
    for arg in args_list:
        arg = arg.strip()
        if '=' in arg:
            key, value = arg.split('=')
            function_meta["kwargs"][key.strip()] = parse_string_value(value.strip())
        else:
            function_meta["args"].append(parse_string_value(arg))

    return function_meta


class LazyFunction(object):
    """ call function lazily. 调用惰性函数
    """

    def __init__(self, function_meta, functions_mapping=None, check_variables_set=None):
        """ init LazyFunction object with function_meta
            用元函数初始化惰性函数对象

        Args:
            function_meta (dict): function name, args and kwargs.
                {
                    "func_name": "func",
                    "args": [1, 2]
                    "kwargs": {"a": 3, "b": 4}
                }

        """
        self.functions_mapping = functions_mapping or {}
        self.check_variables_set = check_variables_set or set()  # 检查变量设置
        self.cache_key = None  # 缓存键
        self.__parse(function_meta)

    def __parse(self, function_meta):
        """ init func as lazy functon instance
            初始化函数作为惰性函数实例

        Args:
            function_meta (dict): function meta including name, args and kwargs
            包含名称的函数元
        """
        self._func = get_mapping_function(
            function_meta["func_name"],
            self.functions_mapping
        )
        self.func_name = self._func.__name__
        self._args = prepare_lazy_data(
            function_meta.get("args", []),
            self.functions_mapping,
            self.check_variables_set
        )
        self._kwargs = prepare_lazy_data(
            function_meta.get("kwargs", {}),
            self.functions_mapping,
            self.check_variables_set
        )

        if self.func_name == "load_csv_file":
            if len(self._args) != 1 or self._kwargs:
                raise exceptions.ParamsError("P() should only pass in one argument!")
            self._args = [self._args[0]]
        elif self.func_name == "get_os_environ":
            if len(self._args) != 1 or self._kwargs:
                raise exceptions.ParamsError("ENV() should only pass in one argument!")
            self._args = [self._args[0]]

    def get_args(self):
        return self._args

    def update_args(self, args):
        self._args = args

    def __repr__(self):
        args_string = ""

        if self._args:
            str_args = [str(arg) for arg in self._args]
            args_string += ", ".join(str_args)

        if self._kwargs:
            args_string += ", "
            str_kwargs = [
                "{}={}".format(key, str(value))
                for key, value in self._kwargs.items()
            ]
            args_string += ", ".join(str_kwargs)

        return "LazyFunction({}({}))".format(self.func_name, args_string)

    def __prepare_cache_key(self, args, kwargs):
        return self.func_name, repr(args), repr(kwargs)

    def to_value(self, variables_mapping=None):
        """ parse lazy data with evaluated variables mapping.
            用已计算变量映射解析惰性数据
            Notice: variables_mapping should not contain any variable or function.
            注意:variables_mapping不应该包含任何变量或函数
        """
        variables_mapping = variables_mapping or {}
        args = parse_lazy_data(self._args, variables_mapping)
        kwargs = parse_lazy_data(self._kwargs, variables_mapping)
        self.cache_key = self.__prepare_cache_key(args, kwargs)
        return self._func(*args, **kwargs)


cached_functions_mapping = {}
""" cached function calling results.缓存函数调用结果
"""


class LazyString(object):
    """ evaluate string lazily.计算字符串懒洋洋地
    """

    def __init__(self, raw_string, functions_mapping=None, check_variables_set=None, cached=False):
        """ make raw_string as lazy object with functions_mapping
            使原始字符串作为惰性对象,函数映射
            check if any variable undefined in check_variables_set
            检查变量是否设置了未定义的变量
        """
        self.raw_string = raw_string
        self.functions_mapping = functions_mapping or {}
        self.check_variables_set = check_variables_set or set()
        self.cached = cached
        self.__parse(raw_string)

    def __parse(self, raw_string):
        """ parse raw string, replace function and variable with {}
            解析原始字符串，用{}替换引用的函数和变量

        Args:
            raw_string(str): string with functions or varialbes
                            带有函数或变量的字符串
            e.g. "ABC${func2($a, $b)}DE$c"

        Returns:
            string: "ABC{}DE{}"
            args: ["${func2($a, $b)}", "$c"]

        """
        self._args = []

        def escape_braces(origin_string):  # 逃避括号
            return origin_string.replace("{", "{{").replace("}", "}}")

        try:
            match_start_position = raw_string.index("$", 0)
            begin_string = raw_string[0:match_start_position]
            self._string = escape_braces(begin_string)
        except ValueError:
            self._string = escape_braces(raw_string)
            return

        while match_start_position < len(raw_string):

            # Notice: notation priority注意:符号优先级
            # $$ > ${func($a, $b)} > $var

            # search $$
            dollar_match = dolloar_regex_compile.match(raw_string, match_start_position)
            if dollar_match:
                match_start_position = dollar_match.end()
                self._string += "$"
                continue

            # search function like ${func($a, $b)}
            func_match = function_regex_compile.match(raw_string, match_start_position)
            if func_match:
                function_meta = {
                    "func_name": func_match.group(1)
                }
                function_meta.update(parse_function_params(func_match.group(2)))
                lazy_func = LazyFunction(
                    function_meta,
                    self.functions_mapping,
                    self.check_variables_set
                )
                self._args.append(lazy_func)
                match_start_position = func_match.end()
                self._string += "{}"
                continue

            # search variable like ${var} or $var
            var_match = variable_regex_compile.match(raw_string, match_start_position)
            if var_match:
                var_name = var_match.group(1) or var_match.group(2)
                # check if any variable undefined in check_variables_set
                if var_name not in self.check_variables_set:
                    raise exceptions.VariableNotFound(var_name)

                self._args.append(var_name)
                match_start_position = var_match.end()
                self._string += "{}"
                continue

            curr_position = match_start_position
            try:
                # find next $ location 查找下一个$位置
                match_start_position = raw_string.index("$", curr_position + 1)
                remain_string = raw_string[curr_position:match_start_position]
            except ValueError:
                remain_string = raw_string[curr_position:]
                # break while loop
                match_start_position = len(raw_string)

            self._string += escape_braces(remain_string)

    def __repr__(self):
        return "LazyString({})".format(self.raw_string)

    def to_value(self, variables_mapping=None):
        """ parse lazy data with evaluated variables mapping.
            用已计算变量映射解析惰性数据。
            Notice: variables_mapping should not contain any variable or function.
            注意:variables_mapping不应该包含任何变量或函数。
        """
        variables_mapping = variables_mapping or {}

        args = []
        for arg in self._args:
            if isinstance(arg, LazyFunction):
                if self.cached and arg.cache_key and arg.cache_key in cached_functions_mapping:
                    value = cached_functions_mapping[arg.cache_key]
                else:
                    value = arg.to_value(variables_mapping)
                    cached_functions_mapping[arg.cache_key] = value
                args.append(value)
            else:
                # variable
                var_value = get_mapping_variable(arg, variables_mapping)
                args.append(var_value)

        if self._string == "{}":
            return args[0]
        else:
            return self._string.format(*args)


def prepare_lazy_data(content, functions_mapping=None, check_variables_set=None, cached=False):
    """ make string in content as lazy object with functions_mapping
        使内容中的字符串作为函数映射的惰性对象

    Raises:
        exceptions.VariableNotFound: if any variable undefined in check_variables_set
        如果在检查变量中设置了未定义的变量

    """
    # TODO: refactor type check 重构类型检查
    if content is None or isinstance(content, (numeric_types, bool, type)):
        return content

    elif isinstance(content, (list, set, tuple)):
        return [
            prepare_lazy_data(
                item,
                functions_mapping,
                check_variables_set,
                cached
            )
            for item in content
        ]

    elif isinstance(content, dict):
        parsed_content = {}
        for key, value in content.items():
            parsed_key = prepare_lazy_data(
                key,
                functions_mapping,
                check_variables_set,
                cached
            )
            parsed_value = prepare_lazy_data(
                value,
                functions_mapping,
                check_variables_set,
                cached
            )
            parsed_content[parsed_key] = parsed_value

        return parsed_content

    elif isinstance(content, basestring):
        # content is in string format here 这里的内容是字符串格式的
        if not is_var_or_func_exist(content):
            # content is neither variable nor function
            # 内容既不是变量，也不是函数
            # replace $$ notation with $ and consider it as normal char.
            # 将$$符号替换为$，并将其视为普通字符
            # e.g. abc => abc, abc$$def => abc$def, abc$$$$def$$h => abc$$def$h
            return content.replace("$$", "$")

        functions_mapping = functions_mapping or {}
        check_variables_set = check_variables_set or set()
        content = LazyString(content, functions_mapping, check_variables_set, cached)

    return content


def parse_lazy_data(content, variables_mapping=None):
    """ parse lazy data with evaluated variables mapping.
        用已计算变量映射解析惰性数据
        Notice: variables_mapping should not contain any variable or function.
        注意:变量映射不应该包含任何变量或函数
    """
    # TODO: refactor type check
    if content is None or isinstance(content, (numeric_types, bool, type)):
        return content

    elif isinstance(content, LazyString):
        variables_mapping = utils.ensure_mapping_format(variables_mapping or {})
        return content.to_value(variables_mapping)

    elif isinstance(content, (list, set, tuple)):
        return [
            parse_lazy_data(item, variables_mapping)
            for item in content
        ]

    elif isinstance(content, dict):
        parsed_content = {}
        for key, value in content.items():
            parsed_key = parse_lazy_data(key, variables_mapping)
            parsed_value = parse_lazy_data(value, variables_mapping)
            parsed_content[parsed_key] = parsed_value

        return parsed_content

    return content


def eval_lazy_data(content, variables_mapping=None, functions_mapping=None):
    """ evaluate data instantly.立即评估数据
        Notice: variables_mapping should not contain any variable or function.
    """
    variables_mapping = variables_mapping or {}
    check_variables_set = set(variables_mapping.keys())
    return parse_lazy_data(
        prepare_lazy_data(
            content,
            functions_mapping,
            check_variables_set
        ),
        variables_mapping
    )


def extract_variables(content):
    """ extract all variables in content recursively.
        递归提取内容中的所有变量
    """
    if isinstance(content, (list, set, tuple)):
        variables = set()
        for item in content:
            variables = variables | extract_variables(item)
        return variables

    elif isinstance(content, dict):
        variables = set()
        for key, value in content.items():
            variables = variables | extract_variables(value)
        return variables

    elif isinstance(content, LazyString):
        return set(regex_findall_variables(content.raw_string))

    return set()


def parse_variables_mapping(variables_mapping):
    """ eval each prepared variable and function in variables_mapping.
        在变量映射中eval每个变量和函数,求出每个变量的值

    Args:
        variables_mapping (dict):
            {
                "varA": LazyString(123$varB),
                "varB": LazyString(456$varC),
                "varC": LazyString(${sum_two($a, $b)}),
                "a": 1,
                "b": 2,
                "c": {"key": LazyString($b)},
                "d": [LazyString($a), 3]
            }

    Returns:
        dict: parsed variables_mapping should not contain any variable or function.
            {
                "varA": "1234563",
                "varB": "4563",
                "varC": "3",
                "a": 1,
                "b": 2,
                "c": {"key": 2},
                "d": [1, 3]
            }

    """
    run_times = 0
    parsed_variables_mapping = {}

    while len(parsed_variables_mapping) != len(variables_mapping):
        for var_name in variables_mapping:

            run_times += 1
            if run_times > len(variables_mapping) * 4:
                not_found_variables = {
                    key: variables_mapping[key]
                    for key in variables_mapping
                    if key not in parsed_variables_mapping
                }
                raise exceptions.VariableNotFound(not_found_variables)

            if var_name in parsed_variables_mapping:
                continue

            value = variables_mapping[var_name]
            variables = extract_variables(value)

            # check if reference variable itself
            if var_name in variables:
                # e.g.
                # var_name = "token"
                # variables_mapping = {"token": LazyString($token)}
                # var_name = "key"
                # variables_mapping = {"key": [LazyString($key), 2]}
                raise exceptions.VariableNotFound(var_name)

            if variables:
                # reference other variable, or function call with other variable
                # e.g. {"varA": "123$varB", "varB": "456$varC"}
                # e.g. {"varC": "${sum_two($a, $b)}"}
                if any([_var_name not in parsed_variables_mapping for _var_name in variables]):
                    # reference variable not parsed
                    continue

            parsed_value = parse_lazy_data(value, parsed_variables_mapping)
            parsed_variables_mapping[var_name] = parsed_value

    return parsed_variables_mapping


def _extend_with_api(test_dict, api_def_dict):
    """ extend test with api definition, test will merge and override api definition.
        使用api定义扩展测试，测试将合并并覆盖api定义

    Args:
        test_dict (dict): test block, this will override api_def_dict
        测试块，这将覆盖api的定义
        api_def_dict (dict): api definition

    Examples:
        >>> api_def_dict = {
                "name": "get token 1",
                "request": {...},
                "validate": [{'eq': ['status_code', 200]}]
            }
        >>> test_dict = {
                "name": "get token 2",
                "extract": {"token": "content.token"},
                "validate": [{'eq': ['status_code', 201]}, {'len_eq': ['content.token', 16]}]
            }
        >>> _extend_with_api(test_dict, api_def_dict)
        >>> print(test_dict)
            {
                "name": "get token 2",
                "request": {...},
                "extract": {"token": "content.token"},
                "validate": [{'eq': ['status_code', 201]}, {'len_eq': ['content.token', 16]}]
            }

    """
    # override api name覆盖api名称
    test_dict.setdefault("name", api_def_dict.pop("name", "api name undefined"))

    # override variables覆盖变量
    def_variables = api_def_dict.pop("variables", [])
    test_dict["variables"] = utils.extend_variables(
        def_variables,
        test_dict.get("variables", {})
    )

    # merge & override validators TODO: relocate
    # 合并和覆盖参数？
    def_raw_validators = api_def_dict.pop("validate", [])
    def_validators = [
        uniform_validator(_validator)
        for _validator in def_raw_validators
    ]
    ref_validators = test_dict.pop("validate", [])
    test_dict["validate"] = extend_validators(
        def_validators,
        ref_validators
    )

    # merge & override extractors合并和覆盖验证器
    def_extrators = api_def_dict.pop("extract", {})
    test_dict["extract"] = utils.extend_variables(
        def_extrators,
        test_dict.get("extract", {})
    )

    # merge & override request 合并和覆盖请求
    test_dict["request"] = api_def_dict.pop("request", {})

    # base_url & verify: priority api_def_dict > test_dict 优先级
    if api_def_dict.get("base_url"):
        test_dict["base_url"] = api_def_dict["base_url"]

    if "verify" in api_def_dict:
        test_dict["request"]["verify"] = api_def_dict["verify"]

    # merge & override setup_hooks
    def_setup_hooks = api_def_dict.pop("setup_hooks", [])
    ref_setup_hooks = test_dict.get("setup_hooks", [])
    extended_setup_hooks_tmp = def_setup_hooks + ref_setup_hooks
    extended_setup_hooks = list(set(extended_setup_hooks_tmp))
    extended_setup_hooks.sort(key=extended_setup_hooks_tmp.index)
    if extended_setup_hooks:
        test_dict["setup_hooks"] = extended_setup_hooks
    # merge & override teardown_hooks
    def_teardown_hooks = api_def_dict.pop("teardown_hooks", [])
    ref_teardown_hooks = test_dict.get("teardown_hooks", [])
    extended_teardown_hooks_tmp = def_teardown_hooks + ref_teardown_hooks
    extended_teardown_hooks = list(set(extended_teardown_hooks_tmp))
    extended_teardown_hooks.sort(key=extended_teardown_hooks_tmp.index)
    if extended_teardown_hooks:
        test_dict["teardown_hooks"] = extended_teardown_hooks

    # TODO: extend with other api definition items, e.g. times
    # 使用其他api定义项进行扩展
    test_dict.update(api_def_dict)


def _extend_with_testcase(test_dict, testcase_def_dict):
    """ extend test with testcase definition
        使用testcase定义扩展测试
        test will merge and override testcase config definition.
        测试将合并并覆盖testcase配置定义

    Args:
        test_dict (dict): test block
        testcase_def_dict (dict): testcase definition

    Returns:
        dict: extended test dict.

    """
    # override testcase config variables重写testcase配置变量
    testcase_def_dict["config"].setdefault("variables", {})
    testcase_def_variables = utils.ensure_mapping_format(
        testcase_def_dict["config"].get("variables", {}))
    testcase_def_variables.update(test_dict.pop("variables", {}))
    testcase_def_dict["config"]["variables"] = testcase_def_variables

    # override base_url, verify 覆盖base_url, verify
    # priority: testcase config > testsuite tests
    test_base_url = test_dict.pop("base_url", "")
    if not testcase_def_dict["config"].get("base_url"):
        testcase_def_dict["config"]["base_url"] = test_base_url

    # override name重写名称
    test_name = test_dict.pop("name", None) \
                or testcase_def_dict["config"].pop("name", None) \
                or "testcase name undefined"

    # override testcase config name, output, etc.
    testcase_def_dict["config"].update(test_dict)
    testcase_def_dict["config"]["name"] = test_name

    test_dict.clear()
    test_dict.update(testcase_def_dict)


def __prepare_config(config, project_mapping, session_variables_set=None):
    """ parse testcase/testsuite config.
    """
    # get config variables得到配置变量
    raw_config_variables = config.pop("variables", {})

    override_variables = utils.deepcopy_dict(project_mapping.get("variables", {}))
    functions = project_mapping.get("functions", {})

    if isinstance(raw_config_variables, basestring) and function_regex_compile.match(
            raw_config_variables):
        # config variables are generated by calling function
        # 配置变量是由调用函数生成的
        # e.g.
        # "config": {
        #     "name": "basic test with httpbin",
        #     "variables": "${gen_variables()}"
        # }
        raw_config_variables_mapping = parse_lazy_data(
            prepare_lazy_data(raw_config_variables, functions_mapping=functions)
        )
    else:
        raw_config_variables_mapping = utils.ensure_mapping_format(raw_config_variables)

    # override config variables with passed in variables
    # 用传入的变量重写配置变量
    raw_config_variables_mapping.update(override_variables)

    if raw_config_variables_mapping:
        config["variables"] = raw_config_variables_mapping

    check_variables_set = set(raw_config_variables_mapping.keys())
    check_variables_set |= (session_variables_set or set())
    prepared_config = prepare_lazy_data(config, functions, check_variables_set, cached=True)
    return prepared_config


def __prepare_testcase_tests(tests, config, project_mapping, session_variables_set=None):
    """ override tests with testcase config variables, base_url and verify.
        使用testcase配置/变量/base_url/验证来重写测试
        test maybe nested testcase.
        测试可能是嵌套的测试用例

        variables priority:变量的优先级
        testcase config > testcase test > testcase_def config > testcase_def test > api

        base_url priority:
        testcase test > testcase config > testsuite test > testsuite config > api

        verify priority:
        testcase teststep (api) > testcase config > testsuite config

    Args:
        tests (list):
        config (dict):
        project_mapping (dict):

    """
    config_variables = config.get("variables", {})
    config_base_url = config.get("base_url", "")
    config_verify = config.get("verify", True)
    functions = project_mapping.get("functions", {})

    prepared_testcase_tests = []
    session_variables_set = set(config_variables.keys()) | (session_variables_set or set())
    for test_dict in tests:

        teststep_variables_set = {"request", "response"}

        # 1, testcase config => testcase tests
        # override test_dict variables
        test_dict_variables = utils.extend_variables(
            test_dict.pop("variables", {}),
            config_variables
        )
        test_dict["variables"] = test_dict_variables

        # base_url & verify: priority test_dict > config
        if (not test_dict.get("base_url")) and config_base_url:
            test_dict["base_url"] = config_base_url

        # unify validators' format统一验证器的形式
        if "validate" in test_dict:
            ref_raw_validators = test_dict.pop("validate", [])
            test_dict["validate"] = [
                uniform_validator(_validator)
                for _validator in ref_raw_validators
            ]

        if "testcase_def" in test_dict:
            # test_dict is nested testcase

            # pass former teststep's (as a testcase) export value to next teststep
            # 通过前一个teststep的(作为一个testcase)导出值到下一个teststep
            # Since V2.2.2, `extract` is used to replace `output`,
            # 从V2.2.2开始，' extract '用于替换' output '，
            # `output` is also kept for compatibility
            # `output`也是为了兼容性
            if "extract" in test_dict:
                session_variables_set |= set(test_dict["extract"])
            elif "output" in test_dict:
                # kept for compatibility保持兼容
                session_variables_set |= set(test_dict["output"])

            # 2, testcase test_dict => testcase_def config
            testcase_def = test_dict.pop("testcase_def")
            _extend_with_testcase(test_dict, testcase_def)

            # verify priority: nested testcase config > testcase config
            test_dict["config"].setdefault("verify", config_verify)

            # 3, testcase_def config => testcase_def test_dict
            test_dict = _parse_testcase(test_dict, project_mapping, session_variables_set)
            if not test_dict:
                continue

        elif "api_def" in test_dict:
            # test_dict has API reference
            # 2, test_dict => api
            api_def_dict = test_dict.pop("api_def")
            _extend_with_api(test_dict, api_def_dict)

        # verify priority: testcase teststep > testcase config
        if "request" in test_dict:
            if "verify" not in test_dict["request"]:
                test_dict["request"]["verify"] = config_verify

            if "upload" in test_dict["request"]:
                from httprunner.ext.uploader import prepare_upload_test
                prepare_upload_test(test_dict)

        # current teststep variables当前teststep变量
        teststep_variables_set |= set(test_dict.get("variables", {}).keys())

        # move extracted variable to session variables将提取的变量移动到会话变量
        if "extract" in test_dict:
            extract_mapping = utils.ensure_mapping_format(test_dict["extract"])
            session_variables_set |= set(extract_mapping.keys())

        teststep_variables_set |= session_variables_set

        # convert validators to lazy function将验证器转换为惰性函数
        validators = test_dict.pop("validate", [])
        prepared_validators = []
        for _validator in validators:
            function_meta = {
                "func_name": _validator["comparator"],
                "args": [
                    _validator["check"],
                    _validator["expect"]
                ],
                "kwargs": {}
            }
            prepared_validators.append(
                LazyFunction(
                    function_meta,
                    functions,
                    teststep_variables_set
                )
            )
        test_dict["validate"] = prepared_validators

        # convert variables and functions to lazy object.将变量和函数转换为惰性对象
        # raises VariableNotFound if undefined variable exists in test_dict
        # 如果test_dict中存在未定义的变量，则引发变量c错误
        prepared_test_dict = prepare_lazy_data(
            test_dict,
            functions,
            teststep_variables_set
        )
        prepared_testcase_tests.append(prepared_test_dict)

    return prepared_testcase_tests


def _parse_testcase(testcase, project_mapping, session_variables_set=None):
    """ parse testcase

    Args:
        testcase (dict):
            {
                "config": {},
                "teststeps": []
            }

    """
    testcase.setdefault("config", {})

    try:
        prepared_config = __prepare_config(
            testcase["config"],
            project_mapping,
            session_variables_set
        )
        prepared_testcase_tests = __prepare_testcase_tests(
            testcase["teststeps"],
            prepared_config,
            project_mapping,
            session_variables_set
        )
        return {
            "config": prepared_config,
            "teststeps": prepared_testcase_tests
        }
    except (exceptions.MyBaseFailure, exceptions.MyBaseError) as ex:
        testcase_type = testcase["type"]
        testcase_path = testcase.get("path")

        logger.log_error("failed to parse testcase: {}, error: {}".format(testcase_path, ex))

        global parse_failed_testfiles
        if testcase_type not in parse_failed_testfiles:
            parse_failed_testfiles[testcase_type] = []

        parse_failed_testfiles[testcase_type].append(testcase_path)

        return None


def __get_parsed_testsuite_testcases(testcases, testsuite_config, project_mapping):
    """ override testscases with testsuite config variables, base_url and verify.
        使用testsuite的配置/变量/base_url/验证来重写testscases

        variables priority:
        parameters > testsuite config > testcase config > testcase_def config > testcase_def tests > api

        base_url priority:
        testcase_def tests > testcase_def config > testcase config > testsuite config

    Args:
        testcases (dict):
            {
                "testcase1 name": {
                    "testcase": "testcases/create_user.yml",
                    "weight": 2,
                    "variables": {
                        "uid": 1000
                    },
                    "parameters": {
                        "uid": [100, 101, 102]
                    },
                    "testcase_def": {
                        "config": {},
                        "teststeps": []
                    }
                },
                "testcase2 name": {}
            }
        testsuite_config (dict):
            {
                "name": "testsuite name",
                "variables": {
                    "device_sn": "${gen_random_string(15)}"
                },
                "base_url": "http://127.0.0.1:5000"
            }
        project_mapping (dict):
            {
                "env": {},
                "functions": {}
            }

    """
    testsuite_base_url = testsuite_config.get("base_url")
    testsuite_config_variables = testsuite_config.get("variables", {})
    functions = project_mapping.get("functions", {})
    parsed_testcase_list = []

    for testcase_name, testcase in testcases.items():

        parsed_testcase = testcase.pop("testcase_def")
        parsed_testcase.setdefault("config", {})
        parsed_testcase["path"] = testcase["testcase"]
        parsed_testcase["type"] = "testcase"
        parsed_testcase["config"]["name"] = testcase_name

        if "weight" in testcase:
            parsed_testcase["config"]["weight"] = testcase["weight"]

        # base_url priority: testcase config > testsuite config
        parsed_testcase["config"].setdefault("base_url", testsuite_base_url)

        # 1, testsuite config => testcase config
        # override test_dict variables
        testcase_config_variables = utils.extend_variables(
            testcase.pop("variables", {}),
            testsuite_config_variables
        )

        # 2, testcase config > testcase_def config
        # override testcase_def config variables
        overrided_testcase_config_variables = utils.extend_variables(
            parsed_testcase["config"].pop("variables", {}),
            testcase_config_variables
        )

        if overrided_testcase_config_variables:
            parsed_testcase["config"]["variables"] = overrided_testcase_config_variables

        # parse config variables
        parsed_config_variables = parse_variables_mapping(overrided_testcase_config_variables)

        # parse parameters
        if "parameters" in testcase and testcase["parameters"]:
            cartesian_product_parameters = parse_parameters(
                testcase["parameters"],
                parsed_config_variables,
                functions
            )

            for parameter_variables in cartesian_product_parameters:
                # deepcopy to avoid influence between parameters
                testcase_copied = utils.deepcopy_dict(parsed_testcase)
                parsed_config_variables_copied = utils.deepcopy_dict(parsed_config_variables)
                testcase_copied["config"]["variables"] = utils.extend_variables(
                    parsed_config_variables_copied,
                    parameter_variables
                )
                parsed_testcase_copied = _parse_testcase(testcase_copied, project_mapping)
                if not parsed_testcase_copied:
                    continue
                parsed_testcase_copied["config"]["name"] = parse_lazy_data(
                    parsed_testcase_copied["config"]["name"],
                    testcase_copied["config"]["variables"]
                )
                parsed_testcase_list.append(parsed_testcase_copied)

        else:
            parsed_testcase = _parse_testcase(parsed_testcase, project_mapping)
            if not parsed_testcase:
                continue
            parsed_testcase_list.append(parsed_testcase)

    return parsed_testcase_list


def _parse_testsuite(testsuite, project_mapping):
    testsuite.setdefault("config", {})
    prepared_config = __prepare_config(testsuite["config"], project_mapping)
    parsed_testcase_list = __get_parsed_testsuite_testcases(
        testsuite["testcases"],
        prepared_config,
        project_mapping
    )
    return parsed_testcase_list


def parse_tests(tests_mapping):
    """ parse tests and load to parsed testcases
        解析测试并加载到解析的测试用例
        tests include api, testcases and testsuites.
        测试包括api、测试用例和测试套件。

    Args:
        tests_mapping (dict): project info and testcases list.项目信息和测试用例列表。

            {
                "project_mapping": {
                    "PWD": "XXXXX",
                    "functions": {},
                    "variables": {},       # optional, priority 1 可选的,优先级 1
                    "env": {}
                },
                "testsuites": [
                    {   # testsuite data structure testsuite数据结构
                        "config": {},
                        "testcases": {
                            "testcase1 name": {
                                "variables": {
                                    "uid": 1000
                                },
                                "parameters": {
                                    "uid": [100, 101, 102]
                                },
                                "testcase_def": {
                                    "config": {},
                                    "teststeps": []
                                }
                            },
                            "testcase2 name": {}
                        }
                    }
                ],
                "testcases": [
                    {   # testcase data structure
                        "config": {
                            "name": "desc1",
                            "path": "testcase1_path",
                            "variables": {},                # optional, priority 2
                        },
                        "teststeps": [
                            # test data structure
                            {
                                'name': 'test step desc1',
                                'variables': [],            # optional, priority 3
                                'extract': [],
                                'validate': [],
                                'api_def': {
                                    "variables": {}         # optional, priority 4
                                    'request': {},
                                }
                            },
                            test_dict_2   # another test dict
                        ]
                    },
                    testcase_dict_2     # another testcase dict
                ],
                "api": {
                    "variables": {},
                    "request": {}
                }
            }

    """
    project_mapping = tests_mapping.get("project_mapping", {})
    testcases = []

    for test_type in tests_mapping:

        if test_type == "testsuites":
            # load testcases of testsuite
            testsuites = tests_mapping["testsuites"]
            for testsuite in testsuites:
                parsed_testcases = _parse_testsuite(testsuite, project_mapping)
                for parsed_testcase in parsed_testcases:
                    testcases.append(parsed_testcase)

        elif test_type == "testcases":
            for testcase in tests_mapping["testcases"]:
                testcase["type"] = "testcase"
                parsed_testcase = _parse_testcase(testcase, project_mapping)
                if not parsed_testcase:
                    continue
                testcases.append(parsed_testcase)

        elif test_type == "apis":
            # encapsulate api as a testcase
            for api_content in tests_mapping["apis"]:
                testcase = {
                    "config": {
                        "name": api_content.get("name")
                    },
                    "teststeps": [api_content],
                    "path": api_content.pop("path", None),
                    "type": api_content.pop("type", "api")
                }
                parsed_testcase = _parse_testcase(testcase, project_mapping)
                if not parsed_testcase:
                    continue
                testcases.append(parsed_testcase)

    return testcases
