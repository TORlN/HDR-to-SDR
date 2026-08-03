"""Shared Tk availability probe: one decision, one diagnosis, one CI guard.

Three test modules (`gui_integration_test`, `gui_free_edition_test`,
`dialogs_facade_test`) each need to know whether Tk can start before they can
run. Each used to carry its own copy of:

    try:
        root = Tk()
        return True
    except Exception:
        return False

Two problems with that, both found by the 2026-08-02 audit (item 12):

1. **A failed probe skips silently.** ~120 GUI tests flip to SKIPPED, and a
   skip is not an error -- CI stays green with the entire GUI suite
   unexecuted. CI runs under `xvfb-run -a`; nothing asserted that xvfb
   actually started. Setting `HDR_REQUIRE_TK=1` (the CI workflow does, and
   test/tk_probe_test.py holds it to that) turns the skip into a hard error.

2. **`except Exception: return False` throws away the diagnosis.** A missing
   display, an incomplete Tcl install and an xvfb that failed to start all
   produced the same "no Tk display available" line, which is why the
   intermittent local skips were never traced to a cause. `probe` folds the
   exception type and message into the skip reason so the next occurrence
   reports what actually went wrong.

Deliberately NOT handled here: each module's *root lifetime* policy. They
differ for reasons documented at each site (a plain `tk.Tk` that releases
`_default_root` vs. a `TkinterDnD.Tk` that must hold it), and the
intermittent skip has never been reproduced on demand -- so unifying those
would be speculative surgery on working code. This module unifies only the
decision and the diagnosis, which is what makes the next occurrence
debuggable.
"""
import os
from typing import Any, Callable, Mapping, Optional, Tuple

REQUIRE_ENV = 'HDR_REQUIRE_TK'
BASE_SKIP = 'no Tk display available (need a desktop session or xvfb)'

# Anything not in this set counts as "on". Defaulting unknown values to on is
# the safe direction: a typo'd `HDR_REQUIRE_TK=ture` in CI then still fails
# loudly rather than silently disarming the guard.
_OFF = frozenset(('', '0', 'false', 'no', 'off'))


class TkUnavailableError(RuntimeError):
    """Tk could not start while HDR_REQUIRE_TK demanded that it must."""


def tk_required(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether a missing display must fail the run instead of skipping it."""
    source = os.environ if env is None else env
    return source.get(REQUIRE_ENV, '').strip().lower() not in _OFF


def probe(
    factory: Callable[[], Any],
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Optional[Any], str]:
    """Build a Tk root via *factory*, reporting availability rather than raising.

    Returns `(root, '')` on success and `(None, reason)` when Tk is
    unavailable, where *reason* is ready to hand to `unittest.skipUnless`.

    Raises `TkUnavailableError` instead of reporting a skip when
    `HDR_REQUIRE_TK` is set -- see the module docstring for why CI needs that.

    `except Exception` (not `BaseException`) is deliberate: a Ctrl-C during
    the probe must stay a KeyboardInterrupt, not become a missing display.
    """
    try:
        return factory(), ''
    except Exception as exc:
        reason = f'{BASE_SKIP} [{type(exc).__name__}: {exc}]'
        if tk_required(env):
            raise TkUnavailableError(
                f'{REQUIRE_ENV} is set, so a missing Tk display is fatal '
                f'instead of skippable: {reason}'
            ) from exc
        return None, reason


def available(
    factory: Callable[[], Any],
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[bool, str]:
    """`probe` for callers that destroy their root and keep nothing.

    Success is decided by the absence of a failure reason, not by the
    factory's return value -- a probe that builds a root, proves Tk works and
    immediately destroys it has nothing meaningful to hand back.
    """
    _root, reason = probe(factory, env)
    return not reason, reason
