"""HDRConverterGUI must construct and operate without the Pro batch mixin.

Every batch call site in gui.py is guarded by `if self._licensed:` (or a
disabled/hidden widget, or an always-empty batch_items list) -- these tests
verify that claim rather than trusting it.
"""
import importlib.util
import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _tk_probe import available  # noqa: E402


def _probe_tk() -> None:
    """Prove Tk can be initialized (requires a display or xvfb).

    The root is destroyed immediately and nothing here reuses it: unlike
    gui_integration_test's module-level root, every test in this file builds
    its own root so it can construct a GUI against a freshly-reloaded copy of
    gui.py. Hence `available` rather than `probe` -- there is no root to keep.
    """
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
    root.withdraw()
    root.destroy()


# _tk_probe carries the skip/fail decision: it reports why Tk was unavailable
# instead of discarding the exception, and turns the skip into a hard error
# under HDR_REQUIRE_TK so CI cannot go green with these tests unrun.
_TK_OK, _SKIP = available(_probe_tk)


def _load_gui_without_pro():
    """Load a throwaway copy of gui.py with `pro.batch` forced unimportable.

    src/gui.py reaches `pro.batch` via `importlib.import_module(...)`
    (required to avoid a pyright bug where a named `from pro.X import Y`
    shadows the except-ImportError fallback -- see src/licensing.py and
    src/dialogs.py for the same pattern and its rationale).
    importlib.import_module calls `_bootstrap._gcd_import()` directly and
    never routes through `builtins.__import__`, so patching
    `builtins.__import__` does not actually intercept it. Setting
    `sys.modules['pro.batch'] = None` works with both import mechanisms:
    Python's import system raises ImportError for any dotted import whose
    exact sys.modules entry is None, which `importlib.import_module` honors
    just as `__import__` does.

    Deliberately NOT `importlib.reload(src.gui)` (the idiom used by
    test/dialogs_facade_test.py and test/licensing_facade_test.py for their
    own modules): reloading a module already in sys.modules rebinds its
    class objects in place, for the rest of this test process -- and
    characterization_test.py/gui_test.py/gui_integration_test.py all did
    `from src.gui import HDRConverterGUI` at their own collection time,
    capturing that ORIGINAL class object. A reload here would leave those
    files holding a stale HDRConverterGUI distinct from the one this file's
    own reload just installed into sys.modules['src.gui'] -- and
    gui_test.py's `@patch('src.gui.HDRConverterGUI.unregister_drop_target')`
    patches whichever class sys.modules currently points at, not the one an
    unrelated test's self.gui instance actually is, silently breaking that
    unrelated test. (Confirmed empirically: reload-in-place here made
    test.gui_test.TestHDRConverterGUI.test_video_conversion fail only when
    run in the same process as this file, never in isolation.) Loading gui.py
    into a private, throwaway module name instead never touches
    sys.modules['gui'] or ['src.gui'], so no other module's cached reference
    is affected.
    """
    gui_path = os.path.join(_SRC, 'gui.py')
    spec = importlib.util.spec_from_file_location('_gui_free_edition_probe', gui_path)
    module = importlib.util.module_from_spec(spec)
    # NOT patch.dict(sys.modules, {'pro.batch': None}) here: patch.dict
    # snapshots the WHOLE dict on entry and restores the WHOLE thing on
    # exit -- so it doesn't just undo the one key it set, it evicts every
    # module gui.py transitively imports during exec_module (tkinter,
    # dark_theme, conversion, tkinterdnd2, ...) the moment the `with` block
    # ends. This module's own namespace keeps stale references to those now
    # de-registered objects, so a later, unrelated `import tkinter`
    # elsewhere in the process creates a SECOND, disconnected tkinter
    # module -- which silently splits tkinter's global `_default_root`
    # across two copies and made TkinterDnD.Tk() construction fail with
    # "Too early to create variable: no default root window" (confirmed
    # empirically). Saving/restoring only the one key sidesteps this.
    had_pro_batch = 'pro.batch' in sys.modules
    prior_pro_batch = sys.modules.get('pro.batch')
    sys.modules['pro.batch'] = None
    try:
        spec.loader.exec_module(module)
    finally:
        if had_pro_batch:
            sys.modules['pro.batch'] = prior_pro_batch
        else:
            del sys.modules['pro.batch']
    return module


class TestFreeEditionGui(unittest.TestCase):
    def test_gui_module_imports_without_pro(self):
        gui = _load_gui_without_pro()
        self.assertTrue(hasattr(gui, 'HDRConverterGUI'))

    def test_batch_mixin_is_a_class(self):
        gui = _load_gui_without_pro()
        self.assertTrue(isinstance(gui._BatchMixin, type))

    def test_batch_mixin_in_gui_mro(self):
        gui = _load_gui_without_pro()
        self.assertIn(gui._BatchMixin, gui.HDRConverterGUI.__mro__)

    def test_fallback_mixin_contributes_no_real_batch_behavior(self):
        """The real Pro mixin IS a class in the MRO too, so the two asserts
        above can't tell fallback from Pro -- this is the discriminator: the
        fallback must not expose any of the Pro-only queue/conflict-review
        *logic* (the conflict-review methods, the settings-comparison used
        for the listbox '*' marker, the status icons, ...). It's allowed to
        define no-op stand-ins for add_batch_files/start_batch/
        _refresh_batch_list/etc -- gui.py's own body calls those by name
        unconditionally (documented on the fallback class itself) -- but
        those stand-ins must be behaviorally inert, which the next test
        checks; this one checks that the methods with no such structural
        excuse are genuinely absent."""
        gui = _load_gui_without_pro()
        pro_only_methods = [
            '_start_next_batch_item',
            '_on_batch_item_complete', '_finish_batch',
            '_detect_batch_conflicts', '_toggle_batch_conflict_item',
            '_enter_batch_conflict_review_ui', '_exit_batch_conflict_review_ui',
            '_settings_relevant_for_comparison',
            '_batch_conflict_row_notes', '_STATUS_ICONS',
        ]
        present = [m for m in pro_only_methods if m in gui._BatchMixin.__dict__]
        self.assertEqual(present, [], msg=(
            f'fallback _BatchMixin must not define any real batch behavior, '
            f'found: {present}'))

    def test_structural_stubs_are_behaviorally_inert(self):
        """add_batch_files/start_batch/_refresh_batch_list exist on the
        fallback (gui.py's own body calls them unconditionally by name --
        see the fallback class's docstring), but they must be no-ops: a
        real add_batch_files would actually grow batch_items, and a real
        start_batch would actually try to convert."""
        gui = _load_gui_without_pro()
        mixin = gui._BatchMixin()
        mixin.batch_items = []
        mixin.add_batch_files(['C:/v/a.mp4'])
        self.assertEqual(mixin.batch_items, [],
                          msg='fallback add_batch_files must not queue anything')
        self.assertIs(mixin.start_batch(), False,
                       msg='fallback start_batch must never actually start a batch')

    @unittest.skipUnless(_TK_OK, _SKIP)
    def test_gui_constructs_unlicensed(self):
        """The real construction path, with no pro/ present."""
        gui = _load_gui_without_pro()
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        app = gui.HDRConverterGUI(root, licensed=False)
        self.assertFalse(app._licensed)

    @unittest.skipUnless(_TK_OK, _SKIP)
    def test_rebuild_interactable_elements_excludes_premium(self):
        gui = _load_gui_without_pro()
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        app = gui.HDRConverterGUI(root, licensed=False)
        self.assertNotIn(app.quality_slider, app.interactable_elements)
        self.assertNotIn(app.quality_entry, app.interactable_elements)
        self.assertIn(app.browse_button, app.interactable_elements)

    @unittest.skipUnless(_TK_OK, _SKIP)
    def test_single_file_drop_still_works_without_pro(self):
        """Batch (multi-file) drops are Pro, but a single-file drop is a
        core, license-agnostic feature -- handle_file_drop parses the drop
        payload via the mixin's _parse_drop_paths before it even checks
        licensing, so that parser must still work in the free build."""
        gui = _load_gui_without_pro()
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        app = gui.HDRConverterGUI(root, licensed=False)
        with patch.object(app, '_load_input_file') as mock_load:
            app.handle_file_drop(type('E', (), {'data': 'C:/v/a.mp4'})())
        mock_load.assert_called_once_with('C:/v/a.mp4')

    @unittest.skipUnless(_TK_OK, _SKIP)
    def test_editing_a_control_does_not_crash_without_pro(self):
        """_write_back_current_settings runs on every control-change handler
        (gamma, format, output path, ...) for every file, licensed or not --
        it must not require the real batch mixin's
        _batch_item_for_current_input to be present."""
        gui = _load_gui_without_pro()
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        app = gui.HDRConverterGUI(root, licensed=False)
        app._write_back_current_settings()  # must not raise
