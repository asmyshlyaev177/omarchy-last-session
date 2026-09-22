"""The suite reads no config file of the machine it runs on, and can write to
no state directory of it either: before any test runs, config is pointed at a
file that does not exist and a state directory under a temporary one. This
runs on the first `tests.` import, which every module makes after importing
the package, so it reloads rather than sets an environment variable."""

import os
import tempfile

from omarchy_last_session import config

_scratch = tempfile.TemporaryDirectory(prefix="omarchy-last-session-tests-")
config.CONFIG_FILE = os.path.join(_scratch.name, "last-session.ini")
config.DEFAULT_STATE_DIR = os.path.join(_scratch.name, "state")
config.reload()
