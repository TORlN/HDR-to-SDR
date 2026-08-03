import logging
import tkinter.colorchooser
import tkinter.filedialog
import tkinter.messagebox

# The suite deliberately drives many error/failure paths (bad codecs, GPU
# fallback, unserializable settings, etc.). Those paths log at WARNING/ERROR,
# which otherwise spams the test console even though the tests pass. Silence
# application logging for the duration of the test run.
logging.disable(logging.CRITICAL)

# Every blocking Tk dialog, replaced with a trap that raises.
#
# An unmocked dialog call does not fail a test -- it opens a real modal window
# and blocks the run until a human clicks it. That is how one reached a
# developer's screen: src/tk_conversion_view.py pre-bound the messagebox
# functions into a dict at import time, so patching the module attribute never
# reached them. The code bug was fixable; the fact that the suite could not
# tell anyone about it was the real problem.
#
# A test that patches one of these swaps the trap for its own mock, so this
# affects only calls nobody mocked. See test/dialog_trap_test.py.
_BLOCKING_DIALOGS = (
    (tkinter.messagebox, ('showinfo', 'showwarning', 'showerror', 'askyesno',
                          'askquestion', 'askokcancel', 'askretrycancel',
                          'askyesnocancel')),
    (tkinter.filedialog, ('askopenfilename', 'askopenfilenames',
                          'asksaveasfilename', 'askdirectory', 'askopenfile',
                          'asksaveasfile')),
    (tkinter.colorchooser, ('askcolor',)),
)


def _dialog_trap(where: str):
    def _blocked(*args, **kwargs):
        raise AssertionError(
            f'UNMOCKED BLOCKING DIALOG: {where}{args!r} -- this would have '
            f'opened a real modal window and hung the run until someone '
            f'clicked it. Patch it in the test, or route it through a view '
            f'the test can record.')
    return _blocked


for _module, _names in _BLOCKING_DIALOGS:
    for _name in _names:
        if hasattr(_module, _name):
            setattr(_module, _name, _dialog_trap(f'{_module.__name__}.{_name}'))
