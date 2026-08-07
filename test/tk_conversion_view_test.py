"""The one module that still knows about tkinter.

Everything the conversion used to do to widgets directly now happens here, so
this file carries the tests that used to live as messagebox-patch assertions
in conversion_test.py: the combobox readonly rule, the progress marshalling
order, and batch mode logging instead of popping a modal.
"""
import logging
import os
import sys
import unittest
from tkinter import ttk
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from _recording_view import RecordingConversionView  # noqa: E402
from conversion_view import ConversionView, Notice  # noqa: E402
from src.tk_conversion_view import BatchConversionView, TkConversionView  # noqa: E402


def _view(cls=TkConversionView, elements=None, **kwargs):
    """A view wired to mocks; override only what the test asserts on."""
    gui = MagicMock()
    return cls(gui, MagicMock(), elements if elements is not None else [],
               MagicMock(), **kwargs)


class TestNotify(unittest.TestCase):

    def test_each_kind_reaches_its_messagebox_function(self):
        view = _view()
        with patch('src.tk_conversion_view.messagebox') as box:
            view.notify(Notice.info('I', 'a'))
            view.notify(Notice.warning('W', 'b'))
            view.notify(Notice.error('E', 'c'))
        box.showinfo.assert_called_once_with('I', 'a')
        box.showwarning.assert_called_once_with('W', 'b')
        box.showerror.assert_called_once_with('E', 'c')


class TestScheduleAndProgress(unittest.TestCase):

    def test_schedule_marshals_onto_the_tk_main_thread(self):
        view = _view()
        fn = MagicMock()
        view.schedule(fn)
        view._gui.root.after.assert_called_once_with(0, fn)

    def test_set_progress_sets_the_var_then_pumps_idle_tasks(self):
        """Both hops go through after(0) because callers run on the ffmpeg
        monitor thread. Order matters: update_idletasks before the var is set
        would redraw the old value."""
        progress_var = MagicMock()
        gui = MagicMock()
        view = TkConversionView(gui, progress_var, [], MagicMock())
        gui.root.after.side_effect = lambda _delay, fn: fn()

        view.set_progress(42.0)

        progress_var.set.assert_called_once_with(42.0)
        self.assertEqual(gui.root.after.call_count, 2)
        self.assertEqual(gui.root.after.call_args_list[1],
                         call(0, gui.root.update_idletasks))


class TestInputsEnabled(unittest.TestCase):

    def test_disable_sets_every_element_disabled(self):
        widgets = [MagicMock(), MagicMock()]
        _view(elements=widgets).set_inputs_enabled(False)
        for widget in widgets:
            widget.config.assert_called_once_with(state='disabled')

    def test_enable_restores_a_combobox_to_readonly_not_normal(self):
        """A ttk.Combobox re-enabled to 'normal' becomes freely typeable. This
        was the reason conversion.py could not drop `from tkinter import ttk`
        by deleting its messagebox calls alone."""
        combobox = MagicMock(spec=ttk.Combobox)
        plain = MagicMock()
        _view(elements=[combobox, plain]).set_inputs_enabled(True)
        combobox.config.assert_called_once_with(state='readonly')
        plain.config.assert_called_once_with(state='normal')


class TestCancelVisibility(unittest.TestCase):

    def test_showing_with_a_handler_binds_it_then_grids(self):
        button = MagicMock()
        view = TkConversionView(MagicMock(), MagicMock(), [], button)
        handler = lambda: None
        view.set_cancel_visible(True, on_cancel=handler)
        button.config.assert_called_once_with(command=handler)
        button.grid.assert_called_once_with()

    def test_showing_without_a_handler_leaves_the_binding_alone(self):
        button = MagicMock()
        view = TkConversionView(MagicMock(), MagicMock(), [], button)
        view.set_cancel_visible(True)
        button.config.assert_not_called()
        button.grid.assert_called_once_with()

    def test_hiding_removes_it_from_the_grid(self):
        button = MagicMock()
        view = TkConversionView(MagicMock(), MagicMock(), [], button)
        view.set_cancel_visible(False)
        button.grid_remove.assert_called_once_with()


class TestDropTargetAndFallback(unittest.TestCase):

    def test_restore_drop_target_calls_through_when_the_gui_has_one(self):
        gui = MagicMock()
        TkConversionView(gui, MagicMock(), [], MagicMock()).restore_drop_target()
        gui.register_drop_target.assert_called_once_with()

    def test_restore_drop_target_is_a_no_op_without_one(self):
        """A view whose gui has nothing to restore must not raise -- the
        hasattr guard this replaces existed for exactly that case."""
        gui = MagicMock(spec=[])
        TkConversionView(gui, MagicMock(), [], MagicMock()).restore_drop_target()


class TestOpenOutput(unittest.TestCase):

    def test_open_output_launches_the_browser(self):
        view = _view()
        with patch('src.tk_conversion_view.webbrowser.open') as opener:
            view.open_output('done.mkv')
        opener.assert_called_once_with('done.mkv')


class TestBatchConversionView(unittest.TestCase):

    def test_batch_logs_instead_of_popping_a_modal(self):
        """A modal mid-queue stalls the whole batch until someone clicks it,
        and the item's status is already visible in the batch list."""
        view = _view(cls=BatchConversionView)
        # test/__init__.py disables all logging suite-wide (logging.disable
        # is checked in Logger.isEnabledFor before assertLogs' own handler
        # ever sees the record), which would otherwise defeat assertLogs
        # below regardless of what notify() does. Re-enable for the duration
        # of this one test only, then restore whatever was in place -- read,
        # not assumed: `discover -s ./test` never runs test/__init__.py, so
        # a hard-coded CRITICAL here would silence every later test in that
        # invocation. The cleanup is registered before the risky `with`
        # block so the level is restored even if the body raises.
        previous_disable = logging.root.manager.disable
        self.addCleanup(logging.disable, previous_disable)
        logging.disable(logging.NOTSET)
        with patch('src.tk_conversion_view.messagebox') as box, \
                self.assertLogs(level=logging.ERROR) as captured:
            view.notify(Notice.warning('GPU Acceleration Failed', 'switching'))
        box.showwarning.assert_not_called()
        box.showinfo.assert_not_called()
        box.showerror.assert_not_called()
        self.assertIn('GPU Acceleration Failed', captured.output[0])
        self.assertIn('switching', captured.output[0])

    def test_the_logging_test_restores_whatever_it_found(self):
        """The test above has to lift the suite-wide logging.disable to use
        assertLogs. Its cleanup must put back the level that was actually in
        place, not a hard-coded CRITICAL: `unittest discover -s ./test` (no
        -t) never runs test/__init__.py, so logging is NOT disabled going in
        -- and a cleanup that assumes otherwise would globally silence every
        test that runs after this one for the rest of that invocation."""
        outer = logging.root.manager.disable
        self.addCleanup(logging.disable, outer)

        logging.disable(logging.NOTSET)  # stand in for the -s ./test run
        result = unittest.TestResult()
        TestBatchConversionView(
            'test_batch_logs_instead_of_popping_a_modal').run(result)

        self.assertEqual(
            (result.errors, result.failures), ([], []),
            msg='the inner test itself failed; fix that before reading the '
                'restore assertion below')
        self.assertEqual(
            logging.root.manager.disable, logging.NOTSET,
            msg=f'the logging test left the global disable level at '
                f'{logging.root.manager.disable} instead of the NOTSET it '
                f'found -- capture the level before changing it rather than '
                f'hard-coding the one test/__init__.py happens to set')

    def test_batch_inherits_every_other_behavior(self):
        """It overrides exactly one method; a second override would mean
        batch and interactive runs had silently diverged.

        Dunders are filtered rather than subtracted by name: Python 3.13 adds
        __firstlineno__ and __static_attributes__ to every class dict, so a
        subtract-the-known-ones list would have to grow with the interpreter.
        """
        overridden = {n for n in vars(BatchConversionView)
                      if not n.startswith('__')}
        self.assertEqual(
            overridden, {'notify'},
            msg=f'BatchConversionView overrides {sorted(overridden)}; it is '
                f'meant to override notify and nothing else')


class TestEveryImplementationSatisfiesTheProtocol(unittest.TestCase):
    """Drift guard: a tenth protocol member must not appear without every
    implementation, including the test double, gaining it."""

    def _members(self):
        names = {n for n in vars(ConversionView) if not n.startswith('_')}
        names |= {n for n in getattr(ConversionView, '__annotations__', {})
                  if not n.startswith('_')}
        return names

    def test_all_three_implementations_are_complete(self):
        for cls in (TkConversionView, BatchConversionView, RecordingConversionView):
            instance = (RecordingConversionView() if cls is RecordingConversionView
                        else cls(MagicMock(), MagicMock(), [], MagicMock()))
            missing = sorted(m for m in self._members() if not hasattr(instance, m))
            with self.subTest(implementation=cls.__name__):
                self.assertEqual(
                    missing, [],
                    msg=f'{cls.__name__} is missing ConversionView member(s) '
                        f'{missing}')


if __name__ == '__main__':
    unittest.main()
