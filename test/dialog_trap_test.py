"""The suite must never be able to open a real modal window.

An unmocked messagebox/filedialog call does not fail a test -- it opens a
window and blocks the run until a human clicks it, which is how it reached a
developer's screen during the ConversionView port work. test/_dialog_trap.py
replaces every blocking dialog entry point with a trap that raises instead,
so the same mistake fails loudly and names itself.

This file imports that module itself rather than relying on the package
__init__: `unittest discover -s ./test` (no -t) never imports
test/__init__.py, and this file's own
test_the_trap_message_names_the_offender calls a real messagebox -- so
without the trap it is this guard that opens the modal.

Tests that legitimately exercise these functions patch them, which swaps the
trap out for their own mock; only genuinely unmocked calls trip it.
"""
import ast
import importlib.util
import os
import sys
import tkinter.colorchooser
import tkinter.commondialog
import tkinter.filedialog
import tkinter.messagebox
import tkinter.simpledialog
import unittest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
# Explicit, not a bare import: `unittest discover -t .` puts only the
# top-level dir on sys.path (unittest.loader line 273 is the sole
# sys.path.insert), so ./test is on it only because test/__init__.py just
# put it there. Under `-s ./test` it is sys.path[0] already. Inserting it
# here makes this file's import work on its own under either invocation.
sys.path.insert(0, _TEST_DIR)

import _dialog_trap  # noqa: E402,F401


class TestBlockingDialogsAreTrapped(unittest.TestCase):

    def test_every_blocking_entry_point_is_trapped(self):
        """Each blocking function and class entry point is trapped, verified
        by checking for the is_dialog_trap marker. This non-invoking check
        prevents typos or deletions from _BLOCKING_DIALOGS from silently
        leaving a function untapped and reachable by the test itself."""
        # Hardcoded list, independent of test/__init__.py, so a deletion
        # from that file doesn't also disappear from here.
        cases = [
            (tkinter.messagebox, 'showinfo'),
            (tkinter.messagebox, 'showwarning'),
            (tkinter.messagebox, 'showerror'),
            (tkinter.messagebox, 'askyesno'),
            (tkinter.messagebox, 'askquestion'),
            (tkinter.messagebox, 'askokcancel'),
            (tkinter.messagebox, 'askretrycancel'),
            (tkinter.messagebox, 'askyesnocancel'),
            (tkinter.filedialog, 'askopenfilename'),
            (tkinter.filedialog, 'askopenfilenames'),
            (tkinter.filedialog, 'askopenfiles'),
            (tkinter.filedialog, 'asksaveasfilename'),
            (tkinter.filedialog, 'askdirectory'),
            (tkinter.filedialog, 'askopenfile'),
            (tkinter.filedialog, 'asksaveasfile'),
            (tkinter.colorchooser, 'askcolor'),
            (tkinter.simpledialog, 'askstring'),
            (tkinter.simpledialog, 'askinteger'),
            (tkinter.simpledialog, 'askfloat'),
        ]
        for module, name in cases:
            with self.subTest(dialog=f'{module.__name__}.{name}'):
                self.assertTrue(
                    hasattr(module, name),
                    msg=f'{module.__name__}.{name} no longer exists; drop it '
                        f'from the list in test/dialog_trap_test.py')
                func = getattr(module, name)
                self.assertTrue(
                    getattr(func, 'is_dialog_trap', False),
                    msg=f'{module.__name__}.{name} is not trapped '
                        f'(missing is_dialog_trap marker)')

    def test_dialog_class_show_methods_are_trapped(self):
        """The blocking dialog classes (Message, Open, SaveAs, Directory,
        Chooser) all inherit from tkinter.commondialog.Dialog and use its
        .show() method. Trap Dialog.show as the chokepoint."""
        dialog_classes = [
            tkinter.messagebox.Message,
            tkinter.filedialog.Open,
            tkinter.filedialog.SaveAs,
            tkinter.filedialog.Directory,
            tkinter.colorchooser.Chooser,
        ]
        for cls in dialog_classes:
            with self.subTest(dialog=f'{cls.__module__}.{cls.__name__}'):
                # Verify it has the show method (from Dialog)
                self.assertTrue(
                    hasattr(cls, 'show'),
                    msg=f'{cls.__module__}.{cls.__name__} no longer inherits '
                        f'from commondialog.Dialog')
                # Verify Dialog.show itself is trapped (will affect all subclasses)
                self.assertTrue(
                    getattr(tkinter.commondialog.Dialog.show, 'is_dialog_trap',
                            False),
                    msg='tkinter.commondialog.Dialog.show is not trapped')

    def test_legacy_filedialog_classes_are_trapped(self):
        """The legacy FileDialog, LoadFileDialog, and SaveFileDialog classes
        do not inherit from commondialog.Dialog. They use .go() as their
        blocking entry point. LoadFileDialog and SaveFileDialog inherit from
        FileDialog and do not override .go(), so trapping FileDialog.go is the
        single chokepoint. Use non-invoking marker check (same as for functions)
        to avoid calling .go() in the test itself."""
        legacy_classes = [
            tkinter.filedialog.FileDialog,
            tkinter.filedialog.LoadFileDialog,
            tkinter.filedialog.SaveFileDialog,
        ]
        for cls in legacy_classes:
            with self.subTest(dialog=f'{cls.__module__}.{cls.__name__}'):
                # Verify it has the go method
                self.assertTrue(
                    hasattr(cls, 'go'),
                    msg=f'{cls.__module__}.{cls.__name__} no longer has a .go() '
                        f'method')
                # Verify FileDialog.go is trapped (will affect all subclasses)
                # Use non-invoking marker check: assert the trapped method has
                # the is_dialog_trap marker.
                self.assertTrue(
                    getattr(tkinter.filedialog.FileDialog.go, 'is_dialog_trap',
                            False),
                    msg='tkinter.filedialog.FileDialog.go is not trapped')

    def test_the_trap_message_names_the_offender(self):
        """The failure has to say what to patch, or the next person sees a
        bare AssertionError from inside tkinter and starts guessing."""
        with self.assertRaises(AssertionError) as caught:
            tkinter.messagebox.showerror('T', 'B')
        message = str(caught.exception)
        self.assertIn('showerror', message)
        self.assertIn('messagebox', message)

    def test_patching_still_works_over_the_trap(self):
        """The trap must not defeat the mocking it exists to enforce."""
        from unittest.mock import patch
        with patch('tkinter.messagebox.showinfo') as mock_show:
            tkinter.messagebox.showinfo('T', 'B')
        mock_show.assert_called_once_with('T', 'B')

    def test_the_legacy_go_trap_accepts_the_real_call_signature(self):
        """FileDialog.go's real signature is
        go(self, dir_or_file='.', pattern='*', default='', key=None), and
        passing those is the normal call form. A stand-in taking only self
        raises TypeError -- which is not the diagnostic AssertionError, does
        not name the offender, and reads like a bug in the test harness."""
        dialog = tkinter.filedialog.FileDialog.__new__(
            tkinter.filedialog.FileDialog)  # no __init__: that builds widgets
        with self.assertRaises(AssertionError) as caught:
            tkinter.filedialog.FileDialog.go(dialog, 'C:/videos', '*.mkv')
        self.assertIn('FileDialog.go()', str(caught.exception))


class TestTheTrapSurvivesEveryInvocation(unittest.TestCase):
    """The trap used to live in test/__init__.py, which `unittest discover
    -s ./test` (no -t) never imports -- so the whole safety net was absent
    from the IDE's own default unittestArgs, the very workflow that let a
    real modal reach a developer's screen. CI's `-t .` hid it.

    These guard the shape of the fix rather than the one-off symptom: the
    mechanism must live in an ordinary module that each entry point imports
    for itself, and importing it twice must be harmless.
    """

    _ENTRY_POINTS = ('__init__.py', '_no_external.py', 'dialog_trap_test.py')

    def _module_source(self, name):
        with open(os.path.join(_TEST_DIR, name), encoding='utf-8') as handle:
            return handle.read()

    def test_the_trap_is_not_armed_from_the_package_init(self):
        """Anything test/__init__.py does is invisible under `-s ./test`.
        It may import the trap module; it may not BE the trap module."""
        source = self._module_source('__init__.py')
        self.assertNotIn(
            'is_dialog_trap', source,
            msg='test/__init__.py installs dialog traps itself again. A '
                '`unittest discover -s ./test` run (the IDE default, no -t) '
                'never imports it, so that net would be missing from exactly '
                'the invocation it exists to protect. Keep the mechanism in '
                'test/_dialog_trap.py and import it from here.')

    def test_every_entry_point_imports_the_trap_module(self):
        """Discovery imports every matched module before running any test, so
        one importer is enough to arm the traps -- but only if that importer
        is loaded under every invocation. __init__.py covers `-t .`,
        dialog_trap_test.py is matched by both patterns, and _no_external.py
        is loaded by every GUI test."""
        for name in self._ENTRY_POINTS:
            with self.subTest(module=name):
                tree = ast.parse(self._module_source(name), name)
                imported = {
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names}
                self.assertIn(
                    '_dialog_trap', imported,
                    msg=f'test/{name} no longer imports _dialog_trap -- the '
                        f'blocking-dialog traps are only armed by the modules '
                        f'that import them, and this one is an entry point '
                        f'for an invocation the others do not cover')

    def test_importing_the_trap_module_twice_does_not_re_wrap(self):
        """Under `-t .` the package imports it as test._dialog_trap while a
        sibling imports it as _dialog_trap: two module objects, both running
        the file. The second pass must leave the already-armed traps exactly
        as they are -- wrapping a trap in a trap buries the real function
        beyond any restore()."""
        before = (tkinter.messagebox.showinfo,
                  tkinter.filedialog.askopenfilename,
                  tkinter.commondialog.Dialog.show,
                  tkinter.filedialog.FileDialog.go)

        spec = importlib.util.spec_from_file_location(
            '_dialog_trap_reimported',
            os.path.join(_TEST_DIR, '_dialog_trap.py'))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # runs install() a second time

        after = (tkinter.messagebox.showinfo,
                 tkinter.filedialog.askopenfilename,
                 tkinter.commondialog.Dialog.show,
                 tkinter.filedialog.FileDialog.go)
        for original, current in zip(before, after):
            self.assertIs(
                original, current,
                msg=f'{original!r} was replaced on re-import; install() must '
                    f'skip entries that already carry the is_dialog_trap '
                    f'marker')
