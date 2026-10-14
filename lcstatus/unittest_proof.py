"""Count native connect-contracts unittest outcomes without editing its exact tree."""
import json
import runpy
import unittest
from pathlib import Path


class CountedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.failed_tests = set()
        self.error_tests = set()
        self.skipped_tests = set()

    def addFailure(self, test, err):
        self.failed_tests.add(id(test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.error_tests.add(id(test))
        super().addError(test, err)

    def addUnexpectedSuccess(self, test):
        self.failed_tests.add(id(test))
        super().addUnexpectedSuccess(test)

    def addSkip(self, test, reason):
        if not isinstance(test, unittest.case._SubTest):
            self.skipped_tests.add(id(test))
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            target = self.failed_tests if issubclass(err[0], test.failureException) else self.error_tests
            target.add(id(test))
        super().addSubTest(test, subtest, err)


class CountedRunner(unittest.TextTestRunner):
    resultclass = CountedResult
    receipt_prefix = None

    def run(self, test):
        # Only a normally returned native run furnishes a report; an interrupted
        # framework run must not print partial counts that could look completed.
        result = super().run(test)
        report = dict(schema_version=1, runner='accept_ew_ds', phase='contracts-test',
                      tests=result.testsRun, failures=len(result.failed_tests),
                      errors=len(result.error_tests), skipped=len(result.skipped_tests))
        print(self.receipt_prefix + json.dumps(report, sort_keys=True), flush=True)
        return result


def execute(outcomes_path):
    rules = runpy.run_path(outcomes_path)
    CountedRunner.receipt_prefix = rules['UNITTEST_PREFIX']
    # Keep unittest's own exit policy, including its no-tests status.
    unittest.main(module=None, argv=['unittest', 'discover', '-s', 'tests'],
                  testRunner=CountedRunner)


def bootstrap():
    here = Path(__file__).resolve()
    return (f"import runpy; runpy.run_path({str(here)!r})['execute']("
            f"{str(here.with_name('outcomes.py'))!r})")
