"""Runs the live tests several at a time, each in a process of its own. A test spends most of
its time waiting for windows, and each one starts a compositor of its own."""

from __future__ import annotations

import argparse
import contextlib
import os
import queue
import signal
import subprocess
import sys
import time
import unittest
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
# What unittest exits with when no test ran.
NO_TESTS = 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-k", dest="patterns", action="append", default=[], help="only the tests matching, as unittest's -k"
    )
    tests = find_tests(parser.parse_args().patterns)
    if not tests:
        print("no test matches", file=sys.stderr)
        return NO_TESTS
    displays = make_displays()
    print(f"{len(tests)} tests, {displays.qsize()} at a time", flush=True)
    running: set[subprocess.Popen[str]] = set()
    failed: list[str] = []
    started = time.monotonic()
    with ThreadPoolExecutor(displays.qsize()) as pool:
        runs = [pool.submit(run_test, test, displays, running) for test in tests]
        try:
            for run in as_completed(runs):
                failed += report(*run.result())
        except KeyboardInterrupt:
            # unittest skips a test's cleanups on SIGINT, so its compositor goes with its group.
            pool.shutdown(wait=False, cancel_futures=True)
            for child in list(running):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
            return 130
    print(f"\nRan {len(tests)} tests in {time.monotonic() - started:.0f} s")
    print(f"FAILED: {', '.join(failed)}" if failed else "OK")
    return 1 if failed else 0


def find_tests(patterns: list[str]) -> list[str]:
    loader = unittest.TestLoader()
    # As unittest's own -k, a pattern without a wildcard matches anywhere in the name.
    loader.testNamePatterns = [p if "*" in p else f"*{p}*" for p in patterns] or None
    suite = loader.discover(HERE)
    if loader.errors:
        sys.exit("\n".join(loader.errors))
    return [test.id() for test in iterate_tests(suite)]


def iterate_tests(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iterate_tests(item)
        else:
            yield item


def make_displays() -> queue.Queue[dict[str, str]]:
    """One free display per test that may run at once: a card of OLS_DRM_CARDS, since a DRM card
    takes one compositor at a time, or else one of OLS_JOBS compositors nested in labwc."""
    displays: queue.Queue[dict[str, str]] = queue.Queue()
    cards = [{"OLS_DRM_CARD": card} for card in os.environ.get("OLS_DRM_CARDS", "").split()]
    for display in cards or [{}] * max(1, int(os.environ.get("OLS_JOBS", "1"))):
        displays.put(display)
    return displays


def run_test(
    test: str, displays: queue.Queue[dict[str, str]], running: set[subprocess.Popen[str]]
) -> tuple[str, int, str, float]:
    display = displays.get()
    started = time.monotonic()
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "unittest", "-v", test],
            cwd=HERE,
            env=dict(os.environ, **display),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # A group of its own, which its labwc and Hyprland join, and out of reach of a
            # terminal's Ctrl-C, which only this process acts on.
            start_new_session=True,
        )
        running.add(child)
        output = child.communicate()[0]
        running.discard(child)
    finally:
        displays.put(display)
    return test, child.returncode, output, time.monotonic() - started


def report(test: str, returncode: int, output: str, seconds: float) -> list[str]:
    """Prints the test's verdict, and its whole output when it failed."""
    name = test.rsplit(".", 1)[-1]
    if returncode != 0:
        print(f"{name} ... FAILED ({seconds:.0f} s)\n{output}", flush=True)
        return [name]
    print(f"{name} ... {'skipped' if '(skipped=' in output else 'ok'} ({seconds:.0f} s)", flush=True)
    return []


if __name__ == "__main__":
    sys.exit(main())
