"""The double gets its own tests so a broken double cannot silently pass the
suites that depend on it.

conversion_test.py and characterization_test.py drive the real
ConversionManager against a RecordingConversionView and assert on what it
recorded. If `notify` stopped appending, those files would go green having
checked nothing.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from _recording_view import RecordingConversionView  # noqa: E402
from conversion_view import Notice  # noqa: E402


class TestRecordingConversionView(unittest.TestCase):

    def test_notices_accumulate_in_order(self):
        view = RecordingConversionView()
        view.notify(Notice.warning('W', 'first'))
        view.notify(Notice.info('I', 'second'))
        self.assertEqual(view.notices,
                         [Notice.warning('W', 'first'), Notice.info('I', 'second')])

    def test_schedule_runs_inline(self):
        """A test has no Tk main loop to marshal onto, so scheduled work must
        run where it is scheduled -- this is what removes the root.after mock
        choreography the old conversion tests needed."""
        view = RecordingConversionView()
        ran = []
        view.schedule(lambda: ran.append(True))
        self.assertEqual(ran, [True])

    def test_progress_and_ui_state_are_recorded_in_order(self):
        view = RecordingConversionView()
        view.set_progress(50.0)
        view.set_inputs_enabled(False)
        view.set_inputs_enabled(True)
        self.assertEqual(view.progress, [50.0])
        self.assertEqual(view.inputs_enabled, [False, True])

    def test_cancel_visibility_records_the_handler_it_was_given(self):
        view = RecordingConversionView()
        handler = lambda: None
        view.set_cancel_visible(True, on_cancel=handler)
        view.set_cancel_visible(False)
        self.assertEqual(view.cancel_visible, [True, False])
        self.assertIs(view.cancel_handler, handler)

    def test_counters_and_opened_paths(self):
        view = RecordingConversionView()
        view.restore_drop_target()
        view.open_output('out.mkv')
        self.assertEqual(view.drop_target_restored, 1)
        self.assertEqual(view.opened, ['out.mkv'])

    def test_on_complete_defaults_to_none_and_is_settable(self):
        """None means interactive; a callable means a batch run is driving the
        queue. conversion.py reads exactly this to decide between them."""
        self.assertIsNone(RecordingConversionView().on_complete)
        done = lambda success, reason: None
        self.assertIs(RecordingConversionView(on_complete=done).on_complete, done)


if __name__ == '__main__':
    unittest.main()
