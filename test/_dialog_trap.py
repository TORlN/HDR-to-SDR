"""Replaces every blocking Tk dialog with a trap that raises. Import to arm.

An unmocked dialog call does not fail a test -- it opens a real modal window
and blocks the run until a human clicks it. That is how one reached a
developer's screen: src/tk_conversion_view.py pre-bound the messagebox
functions into a dict at import time, so patching the module attribute never
reached them. The code bug was fixable; the fact that the suite could not
tell anyone about it was the real problem.

This lives in its own module rather than in test/__init__.py because
`unittest discover -s ./test` (no -t, which is what the IDE's default
unittestArgs produce) makes ./test the top-level dir, imports every test
module under a bare top-level name, and never imports test/__init__.py at
all. Anything armed from that __init__ is therefore absent from exactly the
workflow a developer runs most. Importing this module is invocation-
independent: test/__init__.py, test/_no_external.py and
test/dialog_trap_test.py each import it, so at least one of them is loaded
whichever way discovery is started.

Importing it more than once is a no-op. Under `-t .` the package __init__
imports it as `test._dialog_trap` while a sibling test module imports it as
`_dialog_trap` -- two distinct module objects, both executing this file. The
already-trapped check below is what stops the second pass wrapping a trap in
a trap (which would bury the real function beyond recovery).

A test that patches one of these swaps the trap for its own mock, so this
affects only calls nobody mocked. See test/dialog_trap_test.py.
"""
import tkinter.colorchooser
import tkinter.commondialog
import tkinter.filedialog
import tkinter.messagebox
import tkinter.simpledialog

_BLOCKING_DIALOGS = (
    (tkinter.messagebox, ('showinfo', 'showwarning', 'showerror', 'askyesno',
                          'askquestion', 'askokcancel', 'askretrycancel',
                          'askyesnocancel')),
    (tkinter.filedialog, ('askopenfilename', 'askopenfilenames', 'askopenfiles',
                          'asksaveasfilename', 'askdirectory', 'askopenfile',
                          'asksaveasfile')),
    (tkinter.colorchooser, ('askcolor',)),
    (tkinter.simpledialog, ('askstring', 'askinteger', 'askfloat')),
)


def _is_trapped(obj) -> bool:
    return getattr(obj, 'is_dialog_trap', False) is True


def _dialog_trap(where: str):
    def _blocked(*args, **kwargs):
        raise AssertionError(
            f'UNMOCKED BLOCKING DIALOG: {where}{args!r} -- this would have '
            f'opened a real modal window and hung the run until someone '
            f'clicked it. Patch it in the test, or route it through a view '
            f'the test can record.')
    _blocked.is_dialog_trap = True  # type: ignore[reportFunctionMemberAccess]
    return _blocked


# Trap the Dialog.show method on commondialog.Dialog, which is the single
# chokepoint for all modern blocking dialog classes: messagebox.Message,
# filedialog.Open/SaveAs/Directory, colorchooser.Chooser.


def _trapped_dialog_show(self, **options):
    raise AssertionError(
        f'UNMOCKED BLOCKING DIALOG: {self.__class__.__module__}.'
        f'{self.__class__.__name__}.show{(options,)!r} -- this would have '
        f'opened a real modal window and hung the run until someone '
        f'clicked it. Patch it in the test, or route it through a view '
        f'the test can record.')


_trapped_dialog_show.is_dialog_trap = True  # type: ignore[reportFunctionMemberAccess]


# Trap the go method on FileDialog, the chokepoint for legacy blocking dialog
# classes: FileDialog, LoadFileDialog, SaveFileDialog (which inherit from
# FileDialog and do not override go).
#
# *args/**kwargs, not a bare self: the real signature is
# go(self, dir_or_file='.', pattern='*', default='', key=None), and passing
# those -- the normal call form -- to a one-parameter stand-in raises
# TypeError instead of the diagnostic AssertionError this exists to produce.


def _trapped_filedialog_go(self, *args, **kwargs):
    raise AssertionError(
        f'UNMOCKED BLOCKING DIALOG: {self.__class__.__module__}.'
        f'{self.__class__.__name__}.go() -- this would have opened a real '
        f'modal window and hung the run until someone clicked it. Patch it '
        f'in the test, or route it through a view the test can record.')


_trapped_filedialog_go.is_dialog_trap = True  # type: ignore[reportFunctionMemberAccess]


def install() -> None:
    """Arm every trap. Safe to call repeatedly; already-armed entries are
    left exactly as they are rather than re-wrapped."""
    for module, names in _BLOCKING_DIALOGS:
        for name in names:
            existing = getattr(module, name, None)
            if existing is not None and not _is_trapped(existing):
                setattr(module, name, _dialog_trap(f'{module.__name__}.{name}'))

    if not _is_trapped(tkinter.commondialog.Dialog.show):
        tkinter.commondialog.Dialog.show = _trapped_dialog_show
    if not _is_trapped(tkinter.filedialog.FileDialog.go):
        tkinter.filedialog.FileDialog.go = _trapped_filedialog_go


install()
