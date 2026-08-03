"""The port itself: a value type and a shape, with no behavior to speak of.

conversion_view.py is the one module in src/ that a headless/CLI process must
be able to import, so these tests deliberately do not touch tkinter -- and
test/architecture_test.py holds the module to that in general.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

# Bare, not `src.conversion_view`: production code imports it this way, and the
# two spellings are different module objects with different Notice classes.
from conversion_view import ConversionView, Notice  # noqa: E402


def _protocol_members(protocol) -> set:
    """Every public member a ConversionView implementation must provide.

    on_complete is an annotation rather than a def, so vars() alone misses it.
    """
    names = {n for n in vars(protocol) if not n.startswith('_')}
    names |= {n for n in getattr(protocol, '__annotations__', {})
              if not n.startswith('_')}
    return names


class TestNotice(unittest.TestCase):

    def test_the_three_kinds_are_built_by_classmethod(self):
        self.assertEqual(Notice.info('T', 'B'), Notice('info', 'T', 'B'))
        self.assertEqual(Notice.warning('T', 'B'), Notice('warning', 'T', 'B'))
        self.assertEqual(Notice.error('T', 'B'), Notice('error', 'T', 'B'))

    def test_notices_compare_by_value(self):
        """Frozen and eq so a test can assert on a whole list of them at once,
        which is what replaces asserting on messagebox call order."""
        self.assertEqual([Notice.warning('W', 'body')], [Notice('warning', 'W', 'body')])

    def test_notice_is_frozen(self):
        from dataclasses import FrozenInstanceError
        notice = Notice.info('T', 'B')
        with self.assertRaises(FrozenInstanceError):
            notice.title = 'other'  # type: ignore[misc]


class TestConversionViewProtocol(unittest.TestCase):

    def test_the_protocol_has_exactly_nine_members(self):
        """A tenth member must not appear without the doubles being updated;
        test/tk_conversion_view_test.py checks each implementation against
        this same list."""
        members = _protocol_members(ConversionView)
        self.assertEqual(
            len(members), 9,
            msg=f'ConversionView has {len(members)} members: {sorted(members)}')

    def test_the_members_are_the_expected_ones(self):
        self.assertEqual(_protocol_members(ConversionView), {
            'on_complete', 'notify', 'schedule', 'set_progress',
            'set_inputs_enabled', 'set_cancel_visible', 'restore_drop_target',
            'on_gpu_fallback', 'open_output',
        })


if __name__ == '__main__':
    unittest.main()
