"""The suite must never be able to open a real modal window.

An unmocked messagebox/filedialog call does not fail a test -- it opens a
window and blocks the run until a human clicks it, which is how it reached a
developer's screen during the ConversionView port work. test/__init__.py
replaces every blocking dialog entry point with a trap that raises instead,
so the same mistake fails loudly and names itself.

Tests that legitimately exercise these functions patch them, which swaps the
trap out for their own mock; only genuinely unmocked calls trip it.
"""
import tkinter.colorchooser
import tkinter.commondialog
import tkinter.filedialog
import tkinter.messagebox
import tkinter.simpledialog
import unittest


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
