import logging
import os
import sys

# The suite deliberately drives many error/failure paths (bad codecs, GPU
# fallback, unserializable settings, etc.). Those paths log at WARNING/ERROR,
# which otherwise spams the test console even though the tests pass. Silence
# application logging for the duration of the test run.
logging.disable(logging.CRITICAL)

# Arm the blocking-dialog traps. The mechanism deliberately does NOT live in
# this file: `unittest discover -s ./test` (no -t, which is what the IDE's
# default unittestArgs produce) never imports it, so a trap armed here is
# missing from exactly the invocation a developer runs most. _dialog_trap.py
# is imported from here, from _no_external.py and from dialog_trap_test.py,
# so it is armed however discovery was started; importing it more than once
# is a no-op by design.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _dialog_trap  # noqa: E402,F401
