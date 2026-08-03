"""The shared Tk probe must never let a missing display pass silently on CI.

Audit item 12: three test modules each ran their own copy of a
`try: Tk() / except Exception: return False` probe. A failed probe skips its
whole module -- ~120 GUI tests -- and a skip is not an error, so CI stays
green with the entire GUI suite unexecuted. The `except Exception` also
discarded the exception, so the skip reason said "no Tk display available"
whether the real cause was a missing display, a broken Tcl install, or an
xvfb that failed to start.

These tests pin both halves of the fix: the HDR_REQUIRE_TK escape hatch that
turns the skip into a hard error, and the diagnosis that survives into the
skip message otherwise.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _tk_probe import (  # noqa: E402
    BASE_SKIP, REQUIRE_ENV, TkUnavailableError, available, probe, tk_required,
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_WORKFLOW = os.path.join(_REPO_ROOT, '.github', 'workflows', 'python-tests.yml')


class _Boom(RuntimeError):
    """Stand-in for the TclError a real failed probe raises."""


class TestTkRequired(unittest.TestCase):
    """HDR_REQUIRE_TK decides whether a missing display is fatal."""

    def test_unset_means_not_required(self):
        self.assertFalse(tk_required({}))

    def test_truthy_values_require_tk(self):
        for value in ('1', 'true', 'TRUE', 'yes', 'on', ' 1 '):
            with self.subTest(value=value):
                self.assertTrue(tk_required({REQUIRE_ENV: value}),
                                msg=f'{value!r} should require Tk')

    def test_falsey_values_do_not_require_tk(self):
        # An empty value is what `env: HDR_REQUIRE_TK:` with nothing after it
        # produces -- it must not silently arm the hard failure.
        for value in ('', '0', 'false', 'FALSE', 'no', 'off', '   '):
            with self.subTest(value=value):
                self.assertFalse(tk_required({REQUIRE_ENV: value}),
                                 msg=f'{value!r} should not require Tk')

    def test_defaults_to_the_real_environment(self):
        # Called with no argument it must read os.environ, since that is how
        # every module-level probe invokes it.
        self.assertEqual(tk_required(), tk_required(dict(os.environ)))


class TestProbeSuccess(unittest.TestCase):
    """A working factory is returned untouched, requirement or not."""

    def test_returns_the_root_and_no_skip_reason(self):
        sentinel = object()
        root, reason = probe(lambda: sentinel, env={})
        self.assertIs(root, sentinel)
        self.assertEqual(reason, '')

    def test_does_not_raise_when_tk_is_required_and_present(self):
        sentinel = object()
        root, reason = probe(lambda: sentinel, env={REQUIRE_ENV: '1'})
        self.assertIs(root, sentinel)
        self.assertEqual(reason, '')


class TestProbeFailure(unittest.TestCase):
    """A failed factory skips by default and errors under HDR_REQUIRE_TK."""

    def _fail(self):
        raise _Boom("can't find a usable init.tcl")

    def test_returns_no_root_and_a_reason(self):
        root, reason = probe(self._fail, env={})
        self.assertIsNone(root)
        self.assertTrue(reason)

    def test_reason_names_the_underlying_exception(self):
        # The whole point: "no Tk display available" alone is not a diagnosis.
        # Without this, a broken Tcl install and a missing display are
        # indistinguishable in the test output.
        _root, reason = probe(self._fail, env={})
        self.assertIn(BASE_SKIP, reason)
        self.assertIn('_Boom', reason, msg=f'exception type missing: {reason}')
        self.assertIn("can't find a usable init.tcl", reason,
                      msg=f'exception message missing: {reason}')

    def test_raises_when_tk_is_required(self):
        with self.assertRaises(TkUnavailableError):
            probe(self._fail, env={REQUIRE_ENV: '1'})

    def test_raised_error_names_the_env_var_and_chains_the_cause(self):
        with self.assertRaises(TkUnavailableError) as caught:
            probe(self._fail, env={REQUIRE_ENV: '1'})
        self.assertIn(REQUIRE_ENV, str(caught.exception),
                      msg='the error must say which switch made it fatal')
        self.assertIsInstance(caught.exception.__cause__, _Boom,
                              msg='the original failure must stay chained')

    def test_a_failed_probe_never_swallows_keyboardinterrupt(self):
        # `except Exception` was already correct here; pinned so a later
        # broadening to `except BaseException` cannot make Ctrl-C look like a
        # missing display.
        def interrupt():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            probe(interrupt, env={})


class TestAvailable(unittest.TestCase):
    """`available` is for probes that destroy their root and keep nothing."""

    def test_reports_true_on_success(self):
        ok, reason = available(lambda: object(), env={})
        self.assertTrue(ok)
        self.assertEqual(reason, '')

    def test_reports_false_with_a_reason_on_failure(self):
        def fail():
            raise _Boom('display :99 refused')

        ok, reason = available(fail, env={})
        self.assertFalse(ok)
        self.assertIn('display :99 refused', reason)

    def test_success_does_not_depend_on_what_the_factory_returns(self):
        # gui_free_edition_test's probe destroys its root and has nothing
        # meaningful to hand back; a factory returning None must still count
        # as "Tk works".
        ok, reason = available(lambda: None, env={})
        self.assertTrue(ok, msg='a None-returning factory must still be a success')
        self.assertEqual(reason, '')

    def test_still_raises_when_tk_is_required(self):
        def fail():
            raise _Boom('nope')

        with self.assertRaises(TkUnavailableError):
            available(fail, env={REQUIRE_ENV: '1'})


class TestAllProbesShareThisModule(unittest.TestCase):
    """Drift guard: the copies this module replaced must not grow back.

    Item 12 existed because three modules each hand-rolled the same
    `except Exception: return False`. A fourth copy would re-open the hole
    silently, so the skip-reason string is pinned to this module.
    """

    # The three modules that had hand-rolled probes when item 12 was written.
    # Kept as a floor so the dynamic scan below can never silently match
    # nothing (a broken glob would otherwise make this whole class vacuous).
    _KNOWN = ('gui_integration_test.py', 'gui_free_edition_test.py',
              'dialogs_facade_test.py')

    @property
    def _MODULES(self) -> tuple:
        """Every test module that gates itself on Tk, found by its `_TK_OK`.

        Discovered rather than listed: a *fourth* module hand-rolling its own
        probe is exactly the drift this class exists to catch, and a hardcoded
        list would not see it.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        found = []
        for name in sorted(os.listdir(here)):
            if not name.endswith('_test.py') or name == os.path.basename(__file__):
                continue
            with open(os.path.join(here, name), encoding='utf-8') as handle:
                if re.search(r'^_TK_OK\b', handle.read(), re.MULTILINE):
                    found.append(name)
        return tuple(found)

    def _source(self, name: str) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, name), encoding='utf-8') as handle:
            return handle.read()

    def test_the_scan_finds_at_least_the_known_modules(self):
        missing = [m for m in self._KNOWN if m not in self._MODULES]
        self.assertEqual(missing, [], msg=(
            f'the _TK_OK scan stopped matching {missing}; if these modules '
            f'were renamed update _KNOWN, otherwise the scan is broken and '
            f'every other assertion in this class is vacuous'))

    # assertIn/assertNotIn would echo the whole 1,400-line module into the
    # failure output, so these assert on a bool and put the diagnosis in msg=.

    def test_no_module_hardcodes_its_own_skip_reason(self):
        for name in self._MODULES:
            with self.subTest(module=name):
                source = self._source(name)
                hardcoded = f"'{BASE_SKIP}'" in source or f'"{BASE_SKIP}"' in source
                self.assertFalse(
                    hardcoded,
                    msg=f'{name} hardcodes the skip reason instead of taking '
                        f'it from _tk_probe, so a failed probe there would '
                        f'report no diagnosis')

    def test_every_module_imports_the_shared_probe(self):
        for name in self._MODULES:
            with self.subTest(module=name):
                self.assertTrue(
                    '_tk_probe' in self._source(name),
                    msg=f'{name} must obtain its Tk probe from _tk_probe so '
                        f'HDR_REQUIRE_TK reaches it')


class TestCIArmsTheGuard(unittest.TestCase):
    """The guard is worthless if CI never sets it.

    Same drift risk build_spec_test.py guards for the installer: the switch
    and the thing that flips it live in different files, so nothing but a
    test keeps them in sync.
    """

    def _workflow(self) -> str:
        with open(_WORKFLOW, encoding='utf-8') as handle:
            return handle.read()

    def test_workflow_exists(self):
        self.assertTrue(os.path.isfile(_WORKFLOW), msg=_WORKFLOW)

    def test_the_test_step_sets_the_variable_truthy(self):
        # Split into steps so the assertion is about the step that actually
        # runs the suite, not merely about the string appearing somewhere.
        steps = re.split(r'^    - name: ', self._workflow(), flags=re.MULTILINE)
        running = [s for s in steps if 'unittest discover' in s]
        self.assertTrue(running, msg='no workflow step runs unittest discover')
        for step in running:
            match = re.search(rf'^\s*{REQUIRE_ENV}:\s*[\'"]?(\S+?)[\'"]?\s*$',
                              step, re.MULTILINE)
            self.assertIsNotNone(
                match, msg=f'{REQUIRE_ENV} not set on the test step:\n{step}')
            assert match is not None
            self.assertTrue(
                tk_required({REQUIRE_ENV: match.group(1)}),
                msg=f'{REQUIRE_ENV}={match.group(1)!r} is falsey -- the guard '
                    f'is set but disarmed')


if __name__ == '__main__':
    unittest.main()
