"""Omarchy installs the plugin as a git clone and runs it with the system python3.
Nothing installs a dependency for it, so every import has to come from the
standard library of every Python from 3.9 on."""

import ast
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "omarchy_last_session").rglob("*.py")) + [ROOT / "bin" / "omarchy-last-session"]
# Run under -I -S, where no site-packages is on the path: only the standard
# library and the plugin itself can be imported.
IMPORT_EACH = """
import sys
sys.path.insert(0, sys.argv[1])
failed = []
for where, statement in zip(sys.argv[2::2], sys.argv[3::2]):
    try:
        exec(statement, {})
    except ImportError as e:
        failed.append(f"{where}: {statement}  ({e})")
sys.exit("\\n".join(failed) or None)
"""


def list_imports():
    """(file:line, statement) for every import, those inside functions included."""
    return [
        (f"{path.relative_to(ROOT)}:{node.lineno}", ast.unparse(node))
        for path in SOURCES
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


class StandardLibraryOnly(unittest.TestCase):
    def test_every_import_resolves_without_site_packages(self):
        args = [part for where_and_statement in list_imports() for part in where_and_statement]
        done = subprocess.run(
            [sys.executable, "-I", "-S", "-c", IMPORT_EACH, str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
