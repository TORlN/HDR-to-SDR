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
import tkinter.filedialog
import tkinter.messagebox
import unittest


class TestBlockingDialogsAreTrapped(unittest.TestCase):

    def test_every_blocking_entry_point_raises_instead_of_opening(self):
        """Each one, individually: a trap that missed a single function would
        leave exactly one way to hang the suite."""
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
            (tkinter.filedialog, 'asksaveasfilename'),
            (tkinter.filedialog, 'askdirectory'),
            (tkinter.filedialog, 'askopenfile'),
            (tkinter.filedialog, 'asksaveasfile'),
            (tkinter.colorchooser, 'askcolor'),
        ]
        for module, name in cases:
            with self.subTest(dialog=f'{module.__name__}.{name}'):
                self.assertTrue(
                    hasattr(module, name),
                    msg=f'{module.__name__}.{name} no longer exists; drop it '
                        f'from the trap list in test/__init__.py')
                with self.assertRaises(AssertionError):
                    getattr(module, name)()

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
