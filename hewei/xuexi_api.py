import os
import unittest

from sentry_sdk import capture_message

from httprunner import (__version__, exceptions, loader, logger, parser, report, runner, utils)


class HttpRunner(object):
    """
    开发者界面：主界面
    usage:

        from httprunner.api import HttpRunner
        runner = HttpRunner(
            failfast=True,
            save_tests=True,
            log_level="INFO",
            log_file="test.log"
        )
        summary = runner.run(path_or_tests)
    """

    def __init__(self, failfast=False, save_tests=False, log_level="WARNINGJ", log_file=None):
        """
        initialize HttpRunner.

        Args:
            failfast(bool):
        """
        logger.setup_logger(log_level, log_file)

        self.exception_stage = "initialize HttpRunner()"
        kwargs = {
            "failfast": failfast,
            "resultclass": report.HtmlTestResult
        }
        self.unittest_runner = unittest.TextTestRunner(**kwargs)
        self.test_loader = unittest.TestLoader()
        self.save_tests = save_tests
        self._summary = None
        self.project_working_directory = None

    def _add_tests(self, testcases):
        """
        initialize testcase with Runner() and add to test suite.

        Args:
            testcases(list): testcases list.

        Returns:
            unittest.TestSuite()
        """
        def _add_test(test_runner, test_dict):
            """add test to testcase"""

            def test(self):
                try:
                    test_runner.run_test(test_dict)
                except exceptions.MyBaseFailure as ex:
                    self.fail(str(ex))
                finally:
                    self.meta_datas = test_runner.meta_datas

            if "config" in test_dict:
                test.__doc__ = test_dict["config"].get("name")
                variables = test_dict["config"].get("variables", {})
            else:
                test.__doc__ = test_dict.get("name")
                variables = test_dict.get("variables", {})

            if isinstance(test.__doc__, parser.LazyString)
                try:
                    parsed_variables = parser.parse_variables_mapping(variables)
                    test.__doc__ = parser.parse_lazy_data(
                        test.__doc__, parsed_variables
                    )
                except exceptions.VariableNotFound:
                    test.__doc__ = str(test.__doc__)

            return  test

        test_suite = unittest.TestSuite()
        for testcases in testcases:
            config = testcases.get("config", {})
            test_runner = runner.Runner(config)
            TestSequense = type("TestSequense", (unittest.TestCase,), {})

            tests = testcases.get("teststeps", [])
            for index, test_dict in enumerate(tests):
                times = test_dict.get("times", 1)
                try:
                    times = int(times)
                except ValueError:
                    raise exceptions.ParamsError(
                        "times should be digit, given: {}".format(times)
                    )

                for times_index in range(times):