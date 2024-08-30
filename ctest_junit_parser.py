import argparse
import os
import re
from pathlib import Path
from typing import Optional

from lxml import etree
from strenum import StrEnum

RE_FAILURE = re.compile(r".+?EnergyPlus\/(?P<rel_path>.*):(?P<line_num>\d+)")


def write_step_summary(msg):
    """Print to console and if applicable write to GITHUB_STEP_SUMMARY."""
    print(msg)
    if "GITHUB_STEP_SUMMARY" not in os.environ:
        return
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
        f.write(msg + "\n")


def dict_to_markdown(h: dict, header0: str, header1: str) -> str:
    """Convert a dict to a markdown table."""
    n0 = max([len(k) for k in h.keys()] + [len(header0)])
    n1 = max([len(str(v)) for v in h.values()] + [len(header1)])

    content = [
        f"| {header0.ljust(n0)} | {header1.ljust(n1)} |",
        "| " + "-" * n0 + " | " + "-" * n1 + " |",
    ] + [f"| {k.ljust(n0)} | {str(v).ljust(n1)} |" for k, v in h.items()]
    return "\n".join(content)


class CTestStatus(StrEnum):
    """The possible statuses of CTest XML."""

    Passed = "run"
    Skipped = "disabled"
    NotRun = "notrun"
    Fail = "fail"


class GithubAnnotation:
    def __init__(
        self,
        test_name: str,
        status: CTestStatus,
        rel_path: Optional[str] = None,
        line_num: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        self.test_name = test_name
        self.rel_path = rel_path
        self.line_num = line_num
        self.reason = reason
        self.message_lines = []
        self.status = status

        self.line_num = None
        if self.rel_path:
            if not self.line_num:
                print(f"For {self.test_name}, rel_path was passed but line_num wasn't. {rel_path=}")
                self.line_num = 1

    def __repr__(self):
        message = "\n".join(self.message_lines)
        return f"{self.test_name}, {self.rel_path}:{self.line_num}\n{message}"

    def __str__(self):
        msg = "::warning " if self.status == CTestStatus.Skipped else "::error "
        if self.rel_path:
            msg += f"file={self.rel_path},"
            if self.line_num:
                msg += f"line={self.line_num},"
        msg += f"title={self.test_name}"
        if self.reason:
            msg += f" ({self.reason})"
        if self.message_lines:
            msg += "::" + "%0A".join(self.message_lines)

        return msg


class CTestTestCase:
    def __init__(self, test_case: etree._Element):
        self.test_name = test_case.attrib["name"]  # classname is the same
        self.time = test_case.attrib["time"]
        self.status = CTestStatus(test_case.attrib["status"])
        self.reason = None
        self.annotations = []

        system_out = test_case.find("system-out")
        assert system_out is not None

        self.system_out = system_out.text.replace("[NON-XML-CHAR-0x1B]", "\033")
        self.marked_passed_when_actually_disabled = False

        if self.status == CTestStatus.NotRun:
            skipped = test_case.find("skipped")
            assert skipped is not None
            self.reason = skipped.attrib["message"]
        elif self.status == CTestStatus.Passed:
            if "YOU HAVE 1 DISABLED TEST" in self.system_out:
                self.status = CTestStatus.Skipped
                self.marked_passed_when_actually_disabled = True
                self.annotations.append(
                    GithubAnnotation(test_name=self.test_name, status=self.status, reason="Disabled")
                )

        elif self.status == CTestStatus.Fail:
            failure = test_case.find("failure")
            assert failure is not None
            self.reason = failure.attrib["message"]
            self.parse_failure_stdout()

    def parse_failure_stdout(self):
        annotations = []
        annotation = None
        in_block = False
        for line in self.system_out.splitlines():
            if "[ RUN      ]" in line:
                in_block = True
                continue
            if in_block and "[  FAILED  ]" in line:
                break

            if in_block:
                if m := RE_FAILURE.match(line):
                    if annotation is not None:
                        annotations.append(annotation)
                    annotation = GithubAnnotation(
                        test_name=self.test_name,
                        status=self.status,
                        rel_path=m.groupdict()["rel_path"],
                        line_num=m.groupdict()["line_num"],
                        reason=self.reason,
                    )
                    continue
                annotation.message_lines.append(line)

        assert in_block
        if annotation:
            annotations.append(annotation)
        else:
            annotations.append(GithubAnnotation(test_name=self.test_name, status=self.status, reason=self.reason))
        self.annotations = annotations

    def __repr__(self):
        reason = f" ({self.reason})" if self.reason is not None else ""
        return f"{self.test_name} - {self.status.name}{reason}"


class CTestInfo:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.tree = etree.parse(str(filepath))
        self.root_elem = self.tree.getroot()
        assert self.root_elem.tag == "testsuite"
        self.n_tests = int(self.root_elem.attrib.get("tests", 0))
        self.n_failures = int(self.root_elem.attrib.get("failures", 0))
        self.n_skipped = int(self.root_elem.attrib.get("skipped", 0))
        self.n_errors = int(self.root_elem.attrib.get("errors", 0))  # this is empty!

        self.test_cases = [CTestTestCase(test_case=test_case) for test_case in self.root_elem]

        n_diff = sum([t.marked_passed_when_actually_disabled for t in self.test_cases])
        self.n_skipped += n_diff

        self.n_actually_run = self.n_tests - self.n_skipped

        self.n_passed = self.n_tests - (self.n_failures + self.n_errors + self.n_skipped)
        self.success_rate = self.n_passed / self.n_actually_run

        self.summary_dict = {
            "Total Tests": self.n_tests,
            "Skipped": self.n_skipped,
            "Passed": self.n_passed,
            "Failures": self.n_failures,
            "Success Rate": f"{self.success_rate:.2%}",
            # 'Errors':  self.n_errors,
        }
        self.summary_table = dict_to_markdown(self.summary_dict, header0="Metric", header1="Value")

    def __repr__(self):
        return f"CTest({self.n_passed}/{self.n_actually_run} ({self.success_rate:.2%}))"

    def write_step_summary_table(self):
        write_step_summary("## CTest Results\n")
        write_step_summary(f"{self.n_passed}/{self.n_actually_run} ({self.n_skipped} Skipped)\n")
        write_step_summary(self.summary_table)
        write_step_summary("")
        skipped = [t for t in self.test_cases if t.status == CTestStatus.Skipped]
        failed = [t for t in self.test_cases if t.status == CTestStatus.Fail]
        if failed:
            write_step_summary("<details>\n")
            write_step_summary("<summary>:boom: <strong>Failed Tests</strong> (Click to expand)</summary>")
            write_step_summary("\n* ".join([""] + [t.__repr__() for t in failed]))
            write_step_summary("\n</details>\n")
        if skipped:
            write_step_summary("<details>\n")
            write_step_summary("<summary>:warning: <strong>Skipped Tests</strong> (Click to expand)</summary>")
            write_step_summary("\n* ".join([""] + [t.__repr__() for t in skipped]))
            write_step_summary("\n</details>\n")

    def create_github_annotations(self, include_skipped: bool = False):
        keep_statuses = [CTestStatus.Fail]
        if include_skipped:
            keep_statuses.append(CTestStatus.Skipped)
        for test_case in self.test_cases:
            if test_case.status not in keep_statuses:
                continue
            for annotation in test_case.annotations:
                print(annotation)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse CTest JUnit XML and Generate a report.")
    parser.add_argument("ctest_xml", type=Path, help="The JUnit xml filepath. Run `ctest --output-junit ctest.xml`")
    parser.add_argument(
        "-i",
        "--include-skipped-warnings",
        action="store_true",
        default=False,
        help="Include Skipped Warnings Annotations",
    )
    args = parser.parse_args()

    ctest_xml = args.ctest_xml.resolve()
    if not (ctest_xml.exists() and ctest_xml.is_file()):
        raise IOError(f"{ctest_xml} is not a valid file")

    info = CTestInfo(filepath=ctest_xml)
    print("=" * 80)
    print("Step Summary".center(80))
    print("=" * 80)
    info.write_step_summary_table()
    print("=" * 80)
    print("Annotations".center(80))
    print("=" * 80)
    info.create_github_annotations(include_skipped=args.include_skipped_warnings)
