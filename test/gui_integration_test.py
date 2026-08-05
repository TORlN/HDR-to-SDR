"""Comprehensive UI tests against a REAL Tk widget tree.

Where `gui_test.py` mocks Tk and `characterization_test.py` uses bare instances
(logic only), this suite builds the actual HDRConverterGUI on a real (withdrawn)
TkinterDnD root and asserts the real widgets: construction, defaults, variable
wiring, widget states, grid layout, styles, tooltips, and the user-action flows
(file select, drop, gpu toggle, convert, cancel, close).

The root is withdrawn so nothing pops up. On a headless box without a display
(or xvfb) Tk can't start, so the whole module skips rather than fails — matching
how CI runs the existing GUI tests under xvfb.
"""
import os
import sys
import types
import unittest
from typing import Any, Callable
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import tkinter as tk
from tkinter import ttk
from tkinterdnd2 import TkinterDnD

from src.gui import HDRConverterGUI, DEFAULT_MIN_SIZE
from src.conversion import conversion_manager
from src.tk_conversion_view import TkConversionView
from src.utils import TONEMAP
from src.settings import DEFAULTS

# Imported by path rather than as `from . import` so the guards are available
# under every discovery invocation: `unittest discover -s ./test` (what the
# VS Code Testing extension issues) loads these modules as top-level names with
# no package, so a relative import would fail outright.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _no_external import (  # noqa: E402
    drain_after_timers, no_real_dialogs, no_real_subprocess,
    pending_after_scripts,
)
from _tk_probe import probe  # noqa: E402


# One Tk instance shared across the entire module.  Creating and destroying a
# Tk() per test causes Tcl to deinit/reinit its library on each cycle, which
# is unreliable when the system's Tcl installation is incomplete (e.g. a
# Python 3.13 install missing init.tcl at the expected path).  Keeping one
# interpreter alive for the whole run avoids all reinit.
def _make_probe_root() -> "TkinterDnD.Tk":
    root = TkinterDnD.Tk()
    root.withdraw()
    return root


# _tk_probe carries the skip/fail decision: it reports why Tk was unavailable
# instead of discarding the exception, and turns the skip into a hard error
# under HDR_REQUIRE_TK so CI cannot go green with these 126 tests unrun.
_probe_root, _SKIP = probe(_make_probe_root)
_TK_OK = _probe_root is not None


class _SyncThread:
    """Stand-in for threading.Thread that runs its target immediately, inline,
    instead of on a real worker thread -- lets tests assert on the result of
    a backgrounded call without sleeping/polling for a real thread to finish."""

    # `target` is required, unlike threading.Thread's optional one: every site
    # that patches this in passes it, and a stand-in with nothing to run is a
    # mistake worth catching at construction rather than as a bare
    # "'NoneType' object is not callable" inside start().
    def __init__(self, target: Callable[[], Any], daemon=None, **_kw):
        self._target = target

    def start(self) -> None:
        self._target()


class TestSyncThread(unittest.TestCase):
    """_SyncThread's own contract, which every download test leans on.

    Needs no display, so it stays outside the Tk-gated classes below.
    """

    def test_start_runs_the_target_inline(self):
        calls = []
        _SyncThread(target=lambda: calls.append('ran')).start()
        self.assertEqual(calls, ['ran'],
                         msg='start() must run the target synchronously, not '
                             'defer it to a real thread')

    def test_a_targetless_stand_in_is_rejected_at_construction(self):
        # Every site that patches this in passes target=; without the
        # requirement a targetless instance stays silent until .start(),
        # where it fails as a bare "'NoneType' object is not callable" with
        # nothing pointing back at the stand-in.
        with self.assertRaises(TypeError):
            _SyncThread(daemon=True)  # type: ignore[call-arg]


@unittest.skipUnless(_TK_OK, _SKIP)
class _GuiTestBase(unittest.TestCase):
    def setUp(self):
        # Isolate tests from any on-disk settings file so default-value assertions
        # are deterministic regardless of what the user has saved.
        self._load_patch = patch('src.gui.load_settings', return_value=dict(DEFAULTS))
        self._save_patch = patch('src.gui.save_settings')
        self._load_patch.start()
        self._save_patch.start()
        # Every file-loading path runs _update_info_label, which shells out to
        # ffprobe via get_video_properties/get_maxcll. The fixture files are
        # names like 'movie.mp4' that do not exist, so a real probe can only
        # ever fail -- it returned None after spawning a process per test.
        # Returning None directly is the same answer without the spawn; tests
        # that need real metadata patch these themselves and override this.
        self._props_patch = patch('src.gui.get_video_properties', return_value=None)
        self._maxcll_patch = patch('src.gui.get_maxcll', return_value=None)
        self._props_patch.start()
        self._maxcll_patch.start()
        # Reuse the module-level Tk — never destroy it between tests.
        # Destroying and recreating Tk forces Tcl to deinit/reinit, which is
        # unreliable on broken system Tcl installs.  Instead, destroy only the
        # child widgets so HDRConverterGUI can build a fresh tree on the same root.
        self.root = _probe_root
        drain_after_timers(self.root)
        for w in self.root.winfo_children():
            w.destroy()
        self.gui = HDRConverterGUI(self.root, licensed=True)

    def tearDown(self):
        self._load_patch.stop()
        self._save_patch.stop()
        self._props_patch.stop()
        self._maxcll_patch.stop()


class TestConstruction(_GuiTestBase):

    def test_window_title_and_minsize(self):
        from updater import APP_VERSION
        self.assertEqual(self.root.title(), f"HDR to SDR Converter v{APP_VERSION}")
        # Min size is computed from the controls (issue 3) so they can't be
        # clipped: at least the default floor, and wide enough for the controls.
        min_w, min_h = self.root.minsize()
        self.assertEqual((min_w, min_h), self.gui._min_window_size)
        self.assertGreaterEqual(min_w, DEFAULT_MIN_SIZE[0])
        self.assertGreaterEqual(min_h, DEFAULT_MIN_SIZE[1])
        self.assertGreaterEqual(min_w, self.gui.control_frame.winfo_reqwidth())

    def test_variable_defaults(self):
        self.assertEqual(self.gui.gamma_var.get(), 1.0)
        self.assertEqual(self.gui.tonemap_var.get(), 'Mobius')
        self.assertFalse(self.gui.gpu_accel_var.get())
        self.assertTrue(self.gui.display_image_var.get())
        self.assertEqual(self.gui.progress_var.get(), 0)

    def test_quality_mode_and_bitrate_defaults(self):
        self.assertEqual(self.gui.quality_mode_var.get(), 'Constant Quality')
        self.assertEqual(self.gui.bitrate_var.get(), DEFAULTS['quality_bitrate_kbps'])
        self.assertEqual(self.gui.quality_display_var.get(), str(self.gui.quality_var.get()))

    def test_quality_display_var_follows_quality_var_changes(self):
        self.gui.quality_var.set(19)
        self.assertEqual(self.gui.quality_display_var.get(), '19')

    def test_quality_display_var_follows_bitrate_var_changes_in_target_bitrate_mode(self):
        self.gui.quality_mode_var.set('Target Bitrate')
        self.gui.bitrate_var.set(15000)
        self.assertEqual(self.gui.quality_display_var.get(), '15,000 kbps')

    @patch('src.gui.vulkan_libplacebo_available', return_value=True)
    def test_tonemap_combobox_shows_all_entries_when_gpu_tonemap_active(self, _avail):
        self.gui.gpu_accel_var.set(True)
        self.gui._apply_tonemap_choices()
        self.assertEqual(tuple(self.gui.tonemap_combobox.cget('values')),
                         tuple(TONEMAP))
        self.assertEqual(str(self.gui.tonemap_combobox.cget('state')), 'readonly')

    @patch('src.gui.vulkan_libplacebo_available', return_value=False)
    def test_tonemap_combobox_suffixes_gpu_only_when_unavailable(self, _avail):
        self.gui.gpu_accel_var.set(False)
        self.gui._apply_tonemap_choices()
        values = tuple(self.gui.tonemap_combobox.cget('values'))
        suffix = self.gui._GPU_ONLY_SUFFIX
        self.assertIn(f'BT.2390{suffix}', values)
        self.assertIn(f'Spline{suffix}', values)
        self.assertNotIn('BT.2390', values)
        self.assertNotIn('Spline', values)
        self.assertIn('Reinhard', values)

    @patch('src.gui.vulkan_libplacebo_available', return_value=False)
    def test_tonemap_selection_resets_to_mobius_when_unavailable(self, _avail):
        self.gui.tonemap_var.set('BT.2390')
        self.gui.gpu_accel_var.set(False)
        self.gui._apply_tonemap_choices()
        self.assertEqual(self.gui.tonemap_var.get(), 'Mobius')

    @patch('src.gui.vulkan_libplacebo_available', return_value=False)
    def test_selecting_greyed_gpu_only_row_reverts_to_last_valid(self, _avail):
        self.gui.gpu_accel_var.set(False)
        self.gui._apply_tonemap_choices()
        self.gui._last_valid_tonemapper = 'Hable'
        self.gui.tonemap_var.set(f'BT.2390{self.gui._GPU_ONLY_SUFFIX}')
        self.gui._on_tonemap_selected()
        self.assertEqual(self.gui.tonemap_var.get(), 'Hable')

    @patch('src.gui.vulkan_libplacebo_available', return_value=True)
    def test_selecting_gpu_only_row_while_active_is_accepted(self, _avail):
        self.gui.gpu_accel_var.set(True)
        self.gui._apply_tonemap_choices()
        self.gui.tonemap_var.set('BT.2390')
        self.gui._on_tonemap_selected()
        self.assertEqual(self.gui.tonemap_var.get(), 'BT.2390')
        self.assertEqual(self.gui._last_valid_tonemapper, 'BT.2390')

    @patch('src.gui.vulkan_libplacebo_available', return_value=True)
    def test_lut_export_checkbox_stays_enabled_across_any_tonemapper_switch(self, _avail):
        """Accurate GPU Color's availability depends only on GPU acceleration
        being on, not on which tonemapper is selected -- libplacebo's gamut
        handling was found to diverge from the LUT reference for CPU-capable
        tonemappers too (Hable measured ~61/255), not just GPU-only ones, so
        switching tonemappers must never regrey it while GPU accel stays on."""
        self.gui.gpu_accel_var.set(True)
        self.gui._apply_tonemap_choices()
        self.gui.tonemap_var.set('BT.2390')
        self.gui._on_tonemap_selected()
        self.assertEqual(str(self.gui.lut_export_checkbutton.cget('state')), 'normal')

        self.gui.tonemap_var.set('Mobius')
        self.gui._on_tonemap_selected()
        self.assertEqual(str(self.gui.lut_export_checkbutton.cget('state')), 'normal')

    def test_gamma_slider_range(self):
        self.assertAlmostEqual(float(self.gui.gamma_slider.cget('from')), 0.1)
        self.assertAlmostEqual(float(self.gui.gamma_slider.cget('to')), 3.0)

    def test_format_combobox_values_and_readonly(self):
        self.assertEqual(tuple(self.gui.format_combobox.cget('values')),
                         ('MP4', 'MKV', 'MOV'))
        self.assertEqual(str(self.gui.format_combobox.cget('state')), 'readonly')

    def test_quality_slider_defaults_to_cpu_crf_range(self):
        # GPU off by default -> CRF range, worst(28) on the left, best(17) on the right.
        self.assertAlmostEqual(float(self.gui.quality_slider.cget('from')), 28)
        self.assertAlmostEqual(float(self.gui.quality_slider.cget('to')), 17)

    def test_no_legacy_color_depth_widget(self):
        """The old unconditional 8/10-bit picker is gone for good -- replaced
        by the conditional 10/12-bit toggle (see TestBitDepthToggle)."""
        self.assertFalse(hasattr(self.gui, 'color_depth_combobox'))
        self.assertFalse(hasattr(self.gui, 'color_depth_var'))

    def test_bit_depth_toggle_hidden_by_default(self):
        """No file loaded yet -- _source_bit_depth defaults to 8, so the
        10/12-bit toggle (only relevant above 10-bit) starts hidden."""
        self.assertEqual(self.gui.bit_depth_frame.grid_info(), {})

    def test_five_numbered_frame_buttons(self):
        self.assertEqual(len(self.gui.frame_buttons), 5)
        self.assertEqual([b.cget('text') for b in self.gui.frame_buttons],
                         ['1', '2', '3', '4', '5'])

    def test_custom_seek_entry_and_button_exist(self):
        self.assertIsInstance(self.gui.custom_time_entry, ttk.Entry)
        self.assertIsInstance(self.gui.custom_seek_button, ttk.Button)
        # Both live inside the frame-button container so they hide/reveal with it.
        self.assertEqual(self.gui.custom_time_entry.winfo_parent(),
                         str(self.gui.button_container))

    def test_custom_seek_has_explanatory_caption(self):
        # Issue 2: the bare "Go" button needs a hint about what it does. A caption
        # above the entry explains the custom-seek field and its time format.
        self.assertIsInstance(self.gui.custom_seek_label, ttk.Label)
        self.assertTrue(self.gui.custom_seek_label.cget('text').strip())
        self.assertEqual(self.gui.custom_seek_label.winfo_parent(),
                         str(self.gui.button_container))

    def test_button_column_does_not_stretch(self):
        # Issue 1: when the window is maximized the frame-button column must not
        # absorb a third of the width (which left the buttons floating far to the
        # right of the converted image). The two image columns share the stretch;
        # the button column stays at its natural width, hugging the preview.
        cfg = self.gui.image_frame.grid_columnconfigure
        self.assertEqual(int(cfg(0)['weight']), 1)
        self.assertEqual(int(cfg(1)['weight']), 1)
        self.assertEqual(int(cfg(2)['weight']), 0)

    def test_entries_bound_to_path_variables(self):
        self.assertEqual(self.gui.input_entry.cget('textvariable'),
                         str(self.gui.input_path_var))
        self.assertEqual(self.gui.output_entry.cget('textvariable'),
                         str(self.gui.output_path_var))

    def test_interactable_elements_are_the_expected_widgets(self):
        expected = {
            self.gui.browse_button, self.gui.convert_button, self.gui.gamma_slider,
            self.gui.open_after_conversion_checkbutton,
            self.gui.display_image_checkbutton, self.gui.input_entry,
            self.gui.output_entry, self.gui.gamma_entry,
            self.gui.gpu_accel_checkbutton, self.gui.batch_listbox,
            self.gui.quality_slider, self.gui.quality_entry,
            self.gui.quality_mode_combobox, self.gui.format_combobox,
            self.gui.custom_time_entry, self.gui.custom_seek_button,
            self.gui.add_files_button, self.gui.clear_batch_button,
            self.gui.remove_batch_button,
            self.gui.bit_depth_10_radio, self.gui.bit_depth_12_radio,
            self.gui.apply_settings_button,
        }
        self.assertEqual(set(self.gui.interactable_elements), expected)

    def test_batch_listbox_disabled_during_conversion(self):
        # Regression: batch_listbox was never gated by set_inputs_enabled(False),
        # so clicking a different queue row mid-conversion could overwrite
        # input/output path vars while a GPU->CPU retry was about to re-read
        # them fresh, corrupting which file gets converted to which path.
        TkConversionView(self.gui, self.gui.progress_var,
                         self.gui.interactable_elements,
                         self.gui.cancel_button).set_inputs_enabled(False)
        self.assertEqual(str(self.gui.batch_listbox.cget('state')), 'disabled')

    def test_drop_target_registered_on_start(self):
        self.assertTrue(self.gui.drop_target_registered)

    def test_quality_mode_combobox_grid_position(self):
        info = self.gui.quality_mode_frame.grid_info()
        self.assertEqual(int(info['row']), 6)
        self.assertEqual(int(info['column']), 1)

    def test_quality_mode_combobox_values_and_readonly(self):
        self.assertEqual(tuple(self.gui.quality_mode_combobox.cget('values')),
                         ('Constant Quality', 'Target Bitrate'))
        self.assertEqual(str(self.gui.quality_mode_combobox.cget('state')), 'readonly')

    def test_quality_label_grid_position(self):
        widgets = self.gui.control_frame.grid_slaves(row=3, column=0)
        self.assertEqual(len(widgets), 1)
        self.assertEqual(str(widgets[0].cget('text')), 'Quality:')

    def test_quality_entry_grid_position(self):
        info = self.gui.quality_entry.grid_info()
        self.assertEqual(int(info['row']), 3)
        self.assertEqual(int(info['column']), 2)

    def test_quality_entry_shows_formatted_display_var(self):
        self.assertEqual(self.gui.quality_entry.cget('textvariable'),
                         str(self.gui.quality_display_var))

    def test_quality_entry_return_key_is_bound(self):
        self.assertTrue(self.gui.quality_entry.bind('<Return>'))

    def test_quality_slider_width_tracks_gamma_slider_at_natural_size(self):
        """Regression: quality_slider used to live in a columnspan=3
        sub-frame sized independently of control_frame's shared column 1, so
        it ended up wider than gamma_slider at every window size. Both must
        now resolve to the same actual pixel width. quality_slider's padx is
        (10, 10), exactly matching gamma_slider's, so the widths must be
        exactly equal -- not just close."""
        self.gui.root.update_idletasks()
        self.assertEqual(
            self.gui.gamma_slider.winfo_width(), self.gui.quality_slider.winfo_width(),
            msg='quality_slider must match gamma_slider width at natural window size')

    def test_quality_slider_width_tracks_gamma_slider_when_stretched(self):
        # A withdrawn toplevel ignores geometry() (it's never mapped), so
        # root.geometry() alone doesn't actually stretch anything -- force
        # control_frame's column 1 wider directly instead.
        self.gui.control_frame.grid_propagate(False)
        self.gui.control_frame.configure(width=1200)
        self.gui.root.update_idletasks()
        self.assertEqual(
            self.gui.gamma_slider.winfo_width(), self.gui.quality_slider.winfo_width(),
            msg='quality_slider must match gamma_slider width when the window is stretched wider')

    def test_quality_row_vertically_aligned_with_gamma_slider(self):
        """Regression: realigning the Quality row into control_frame's shared
        grid (so quality_slider/quality_entry sit directly in control_frame,
        not a sub-frame) left the "Smaller File <-> Better Quality" caption
        behind inside the old quality_frame at its own row 1, making that
        grid row two slider-heights tall. With no vertical sticky, the
        Quality label/entry then centered against that taller row instead of
        against the slider itself -- landing visibly below where the Gamma
        row's label/slider/entry (which has no such second line) center."""
        self.gui.root.update_idletasks()
        # quality_slider's parent is quality_frame (a sub-frame gridded into
        # control_frame), while quality_entry and the "Quality:" label are
        # parented directly to control_frame -- winfo_y() is parent-relative,
        # so comparing it across those different parents would be comparing
        # unrelated coordinate spaces. winfo_rooty() (absolute screen
        # position) is the only apples-to-apples comparison here.
        slider_center = (self.gui.quality_slider.winfo_rooty()
                          + self.gui.quality_slider.winfo_height() / 2)
        entry_center = (self.gui.quality_entry.winfo_rooty()
                         + self.gui.quality_entry.winfo_height() / 2)
        label_widgets = self.gui.control_frame.grid_slaves(row=3, column=0)
        label_center = (label_widgets[0].winfo_rooty()
                         + label_widgets[0].winfo_height() / 2)
        self.assertAlmostEqual(slider_center, entry_center, delta=2,
                                msg='quality_entry must vertically center on quality_slider')
        self.assertAlmostEqual(slider_center, label_center, delta=2,
                                msg='the "Quality:" label must vertically center on quality_slider')

    def test_quality_mode_tooltip_mentions_both_modes(self):
        text = self.gui._quality_mode_tooltip_text()
        self.assertIn('Constant Quality', text)
        self.assertIn('Target Bitrate', text)

    def test_target_bitrate_selectable_and_reconfigures_slider(self):
        self.gui._cached_props = {'bit_rate': 40_000_000}  # 40,000 kbps
        self.gui.quality_mode_var.set('Target Bitrate')
        self.gui._on_quality_mode_selected()
        self.assertAlmostEqual(float(self.gui.quality_slider.cget('from')), 1000)
        self.assertAlmostEqual(float(self.gui.quality_slider.cget('to')), 40000)

    def test_typed_exact_bitrate_flows_through_the_real_ceiling_end_to_end(self):
        # End-to-end coupling test: a real (non-mocked) cached source bitrate
        # feeding _bitrate_ceiling_kbps() feeding _on_quality_entry_change(),
        # all with real widgets -- the exact path TestOnQualityEntryChange
        # (characterization_test.py) can't cover since it mocks
        # _bitrate_ceiling_kbps directly in every test. 47,547 is deliberately
        # not a multiple of 500, matching the exact-truncation bug this whole
        # branch exists to fix.
        self.gui._cached_props = {'bit_rate': 47_547_000}  # 47,547 kbps
        self.gui.quality_mode_var.set('Target Bitrate')
        self.gui._on_quality_mode_selected()
        self.gui.quality_display_var.set('47547')
        self.gui._on_quality_entry_change()
        self.assertEqual(self.gui.bitrate_var.get(), 47547)
        self.assertEqual(int(self.gui.quality_slider.cget('to')), 47547)


@unittest.skipUnless(_TK_OK, _SKIP)
class TestRestoringBitrateModeAtStartup(unittest.TestCase):
    """Restoring a persisted Target Bitrate session must not corrupt the
    saved kbps value before the user touches anything. quality_slider's own
    priming .set() during construction (create_widgets) fires
    _on_quality_change synchronously -- with quality_mode_var already
    'Target Bitrate' at that point but the slider still wearing its CRF
    range, an unguarded call misreads the CRF number as kbps."""

    def setUp(self):
        persisted = dict(DEFAULTS)
        persisted['quality_mode'] = 'bitrate'
        persisted['quality_bitrate_kbps'] = 12000
        self._load_patch = patch('src.gui.load_settings', return_value=persisted)
        self._save_patch = patch('src.gui.save_settings')
        self._load_patch.start()
        self._save_patch.start()
        self._props_patch = patch('src.gui.get_video_properties', return_value=None)
        self._maxcll_patch = patch('src.gui.get_maxcll', return_value=None)
        self._props_patch.start()
        self._maxcll_patch.start()
        self.root = _probe_root
        drain_after_timers(self.root)
        for w in self.root.winfo_children():
            w.destroy()

    def tearDown(self):
        self._load_patch.stop()
        self._save_patch.stop()
        self._props_patch.stop()
        self._maxcll_patch.stop()

    def test_persisted_bitrate_survives_construction(self):
        gui = HDRConverterGUI(self.root, licensed=True)
        self.assertEqual(gui.bitrate_var.get(), 12000,
                         msg='persisted Target Bitrate kbps must not be '
                             'overwritten by the slider priming .set() '
                             'firing _on_quality_change during construction')
        self.assertFalse(gui._bitrate_customized_for_current_item,
                         msg='construction-time priming must not mark the '
                             '(nonexistent) current item as user-customized')


class TestDarkTheme(_GuiTestBase):
    """The color-based dark clam theme (replaces image-based sv_ttk)."""

    def test_uses_clam_engine(self):
        self.assertEqual(ttk.Style(self.root).theme_use(), 'clam')

    def test_window_background_is_dark(self):
        from src.dark_theme import BG
        self.assertEqual(str(self.root.cget('background')), BG)

    def test_listbox_inherits_dark_colors(self):
        # apply_dark_theme runs before create_widgets, so the classic Listbox
        # picks up the dark field color from the option database.
        from src.dark_theme import FIELD
        self.assertEqual(str(self.gui.batch_listbox.cget('background')), FIELD)

    def test_slider_knob_is_a_single_accent_color(self):
        # Fill, border and both bevel colors are pinned to the accent so the
        # gamma/quality knobs render as one flat color (no "blue edges, dark
        # middle" bevel).
        from src.dark_theme import ACCENT
        style = ttk.Style(self.root)
        for key in ('background', 'bordercolor', 'lightcolor', 'darkcolor'):
            self.assertEqual(
                str(style.lookup('Horizontal.TScale', key)), ACCENT,
                f"Horizontal.TScale {key} should be the accent color")


class TestBatchQueueWidgets(_GuiTestBase):
    """Real-widget checks for the batch (multi-file) queue panel.

    Only widget existence/layout and gui.py-owned state/methods live here.
    Everything that exercises real batch behavior (add/remove/clear/apply-
    to-all, queue-item selection, conflict review, batch execution) moved to
    src/pro/test/batch_test.py's TestBatchQueueWidgets alongside the private
    _BatchMixin it depends on -- see task-6 of the Pro/private-repo split.
    """

    def test_batch_widgets_exist(self):
        self.assertIsInstance(self.gui.batch_listbox, tk.Listbox)
        self.assertIsInstance(self.gui.add_files_button, ttk.Button)
        self.assertIsInstance(self.gui.clear_batch_button, ttk.Button)
        self.assertEqual(self.gui.batch_items, [])

    def test_batch_listbox_shows_several_rows(self):
        # Issue 2: a 4-row list made browsing a queue cramped. Show enough rows
        # that a handful of queued files are visible without scrolling.
        self.assertGreaterEqual(int(self.gui.batch_listbox.cget('height')), 8)

    def test_batch_listbox_fills_frame_vertically(self):
        # The listbox stretches to fill the batch panel (N/S) so the scrollbar
        # spans the whole list, not just four rows of it.
        info = self.gui.batch_listbox.grid_info()
        self.assertIn('n', str(info.get('sticky', '')))
        self.assertIn('s', str(info.get('sticky', '')))

    def test_batch_conflict_review_state_starts_empty(self):
        self.assertIsNone(self.gui._batch_conflict_groups)
        self.assertEqual(self.gui._batch_conflict_selection, {})

    def test_apply_to_all_button_exists(self):
        self.assertIsInstance(self.gui.apply_settings_button, ttk.Button)

    def test_batch_settings_info_button_exists_and_shows_tooltip(self):
        self.assertEqual(self.gui.batch_settings_info_button.cget('text'), 'ⓘ')
        event = types.SimpleNamespace(widget=MagicMock())
        event.widget.winfo_rootx.return_value = 100
        event.widget.winfo_rooty.return_value = 100

        self.gui.show_tooltip(event, self.gui._batch_settings_tooltip_text())
        labels = [w for w in self.gui.tooltip.winfo_children() if isinstance(w, ttk.Label)]
        self.assertTrue(labels)
        self.gui.hide_tooltip()

    def test_batch_settings_tooltip_text_covers_the_three_behaviors(self):
        text = self.gui._batch_settings_tooltip_text()
        self.assertIn('own settings', text.lower())
        self.assertIn('*', text)
        self.assertIn('apply to all', text.lower())

    def test_batch_review_cancel_button_hidden_by_default(self):
        self.assertEqual(self.gui.batch_review_cancel_button.grid_info(), {})


class TestStateAndLayout(_GuiTestBase):

    def test_set_inputs_enabled_false_disables_widgets(self):
        TkConversionView(self.gui, self.gui.progress_var,
                         self.gui.interactable_elements,
                         self.gui.cancel_button).set_inputs_enabled(False)
        for widget in self.gui.interactable_elements:
            self.assertIn('disabled', str(widget.cget('state')))

    def test_arrange_widgets_image_frame_true_rows(self):
        self.gui.arrange_widgets(image_frame=True)
        self.assertEqual(int(self.gui.button_frame.grid_info()['row']), 2)
        self.assertEqual(int(self.gui.progress_bar.grid_info()['row']), 3)

    def test_arrange_widgets_image_frame_false_rows(self):
        self.gui.arrange_widgets(image_frame=False)
        self.assertEqual(int(self.gui.button_frame.grid_info()['row']), 5)
        self.assertEqual(int(self.gui.progress_bar.grid_info()['row']), 6)

    def test_highlight_frame_button_applies_styles(self):
        self.gui.highlight_frame_button(3)
        self.assertEqual(self.gui.frame_buttons[2].cget('style'),
                         'Selected.TButton')
        for i, btn in enumerate(self.gui.frame_buttons, start=1):
            if i != 3:
                self.assertEqual(btn.cget('style'), 'TButton')

    def test_clear_preview_resets_images_and_minsize(self):
        self.gui.original_image = 'x'
        self.gui.converted_image_base = 'y'
        self.gui.clear_preview()
        self.assertIsNone(self.gui.original_image)
        self.assertIsNone(self.gui.converted_image_base)
        self.assertEqual(tuple(self.root.minsize()), self.gui._min_window_size)
        self.assertEqual(self.gui.original_image_label.cget('image'), '')
        self.assertEqual(self.gui.converted_image_label.cget('image'), '')

    def test_image_labels_anchor_top_of_expanded_row(self):
        """Regression: image_frame's row 1 (images + button_container) has
        rowconfigure weight=1, so it grows to fill any extra window height.
        button_container is pinned with sticky=tk.N, but the image labels
        left anchor at ttk.Label's default 'w' -- horizontally left but
        vertically *centered* -- so the image sank toward the middle of the
        tall cell, visibly below the "Original/Converted" titles and the
        frame-jump buttons once the window was resized taller. 'nw' keeps
        the existing left alignment and adds a top anchor."""
        self.assertEqual(str(self.gui.original_image_label.cget('anchor')), 'nw')
        self.assertEqual(str(self.gui.converted_image_label.cget('anchor')), 'nw')

    def test_window_resize_slack_grows_batch_queue_not_image_frame(self):
        """Regression: root row 1 (image_frame) had weight=1, so resizing the
        window taller dumped all the extra height into image_frame -- which
        (after the anchor='nw' fix above) just left dead space below the
        now-top-anchored preview images instead of doing anything useful.
        The batch queue listbox (batch_frame row 2, itself rowconfigure
        weight=1 internally) is the widget that should actually grow, so the
        slack belongs on root row 2, not root row 1."""
        self.assertEqual(self.gui.root.grid_rowconfigure(1)['weight'], 0)
        self.assertEqual(self.gui.root.grid_rowconfigure(2)['weight'], 1)
        info = self.gui.batch_frame.grid_info()
        self.assertIn('n', str(info['sticky']))
        self.assertIn('s', str(info['sticky']))


class TestTooltip(_GuiTestBase):

    def _event(self):
        ev = types.SimpleNamespace(widget=MagicMock())
        ev.widget.bbox.return_value = (0, 0, 0, 0)
        ev.widget.winfo_rootx.return_value = 100
        ev.widget.winfo_rooty.return_value = 100
        return ev

    def test_show_tooltip_creates_toplevel_with_text(self):
        self.gui.show_tooltip(self._event(), "hello world")
        self.assertIsInstance(self.gui.tooltip, tk.Toplevel)
        labels = [w for w in self.gui.tooltip.winfo_children()
                  if isinstance(w, ttk.Label)]
        self.assertTrue(labels)
        self.assertEqual(labels[0].cget('text'), "hello world")

    def test_hide_tooltip_destroys_window(self):
        self.gui.show_tooltip(self._event(), "bye")
        win = self.gui.tooltip
        self.gui.hide_tooltip()
        self.assertIsNone(self.gui.tooltip)
        self.assertFalse(win.winfo_exists())

    def test_show_tooltip_replaces_previous(self):
        self.gui.show_tooltip(self._event(), "first")
        first = self.gui.tooltip
        self.gui.show_tooltip(self._event(), "second")
        self.assertFalse(first.winfo_exists())
        self.assertTrue(self.gui.tooltip.winfo_exists())


class TestUserActions(_GuiTestBase):

    @patch('src.gui.filedialog.askopenfilename')
    def test_select_file_sets_paths_and_triggers_preview(self, mock_dialog):
        mock_dialog.return_value = 'movie.mp4'
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            self.gui.select_file()
        self.assertEqual(self.gui.input_path_var.get(), 'movie.mp4')
        self.assertEqual(self.gui.output_path_var.get(), 'movie_sdr.mp4')
        mock_update.assert_called_once()

    @patch('src.gui.filedialog.askopenfilename')
    def test_select_file_webm_output_redirected_to_mkv(self, mock_dialog):
        mock_dialog.return_value = 'movie.webm'
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.select_file()
        self.assertEqual(self.gui.output_path_var.get(), 'movie_sdr.mkv')

    @patch('src.gui.filedialog.askopenfilename', return_value='')
    def test_select_file_cancelled_does_nothing(self, _mock_dialog):
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            self.gui.select_file()
        self.assertEqual(self.gui.input_path_var.get(), '')
        mock_update.assert_not_called()

    def test_handle_file_drop_sets_paths(self):
        event = types.SimpleNamespace(data='{C:/videos/clip.mkv}')
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.handle_file_drop(event)
        self.assertEqual(self.gui.input_path_var.get(), 'C:/videos/clip.mkv')
        self.assertEqual(self.gui.output_path_var.get(), 'C:/videos/clip_sdr.mkv')

    def test_update_frame_preview_display_off_clears(self):
        self.gui.display_image_var.set(False)
        self.gui.input_path_var.set('')
        self.gui.update_frame_preview()  # must not call ffmpeg or raise
        self.assertEqual(tuple(self.root.minsize()), self.gui._min_window_size)
        self.assertEqual(self.gui.button_container.grid_info(), {})

    def test_custom_seek_sets_position_and_previews(self):
        self.gui.custom_time_var.set('0:00:10')
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            self.gui.on_custom_seek()
        self.assertAlmostEqual(self.gui.custom_time_position, 10.0)
        mock_update.assert_called_once()

    def test_custom_seek_invalid_shows_error(self):
        self.gui.custom_time_var.set('garbage')
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            self.gui.on_custom_seek()
        mock_update.assert_not_called()
        self.assertTrue(self.gui.error_label.cget('text'))

    def test_frame_button_click_clears_custom_seek(self):
        self.gui.custom_time_position = 33.0
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.on_frame_button_click(2)
        self.assertIsNone(self.gui.custom_time_position)

    def test_frame_button_click_updates_index_and_highlight(self):
        self.gui.original_image = 'cached'
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            self.gui.on_frame_button_click(4)
        self.assertEqual(self.gui.current_frame_index, 4)
        self.assertIsNone(self.gui.original_image)
        self.assertEqual(self.gui.frame_buttons[3].cget('style'),
                         'Selected.TButton')
        mock_update.assert_called_once()

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_gpu_toggle_unavailable_resets_and_warns(self, mock_cm, mock_mb):
        mock_cm.is_gpu_acceleration_available.return_value = False
        self.gui.gpu_accel_var.set(True)
        self.gui.check_gpu_acceleration()
        self.assertFalse(self.gui.gpu_accel_var.get())
        mock_mb.showwarning.assert_called_once()

    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    @patch('src.gui.conversion_manager')
    def test_convert_video_starts_and_shows_cancel(self, mock_cm, *_):
        self.gui.input_path_var.set('in.mkv')
        self.gui.output_path_var.set('out.mkv')
        self.gui.convert_video()
        mock_cm.start.assert_called_once()
        self.assertFalse(self.gui.drop_target_registered)  # unregistered
        self.assertNotEqual(self.gui.cancel_button.grid_info(), {})  # shown

    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    @patch('src.gui.conversion_manager')
    def test_convert_video_leaves_ui_usable_when_start_declines(self, mock_cm, *_):
        """start returning False means a guard rejected the file
        before ever launching ffmpeg (e.g. undetermined duration). Drag-and-
        drop must stay registered and Cancel must stay hidden -- otherwise
        the only way to recover is restarting the app."""
        mock_cm.start.return_value = False
        self.gui.input_path_var.set('in.mkv')
        self.gui.output_path_var.set('out.mkv')
        self.gui.convert_video()
        mock_cm.start.assert_called_once()
        self.assertTrue(self.gui.drop_target_registered)  # still registered
        self.assertEqual(self.gui.cancel_button.grid_info(), {})  # still hidden

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_cancel_button_invokes_cancel(self, mock_cm, _mb):
        self.gui.cancel_conversion()
        mock_cm.cancel_conversion.assert_called_once()

    def test_preview_loading_hides_titles_buttons_and_shows_spinner(self):
        # While frames extract, the spinner is up and titles/buttons are hidden.
        self.gui.display_image_var.set(True)
        self.gui.input_path_var.set('in.mkv')
        with patch.object(self.gui, 'display_frames'):  # don't spawn real ffmpeg
            self.gui.update_frame_preview()
        self.assertNotEqual(self.gui.loading_frame.grid_info(), {})   # spinner shown
        self.assertEqual(self.gui.original_title_label.grid_info(), {})  # title hidden
        self.assertEqual(self.gui.converted_title_label.grid_info(), {})
        self.assertEqual(self.gui.button_container.grid_info(), {})   # buttons hidden

    def test_render_reveals_titles_buttons_and_hides_spinner(self):
        from PIL import Image as PILImage
        tk._default_root = self.root
        self.gui.display_image_var.set(True)
        # Put the UI into the loading state first.
        self.gui._show_preview_loading()
        frame = PILImage.new('RGB', (960, 540), (40, 50, 60))

        self.gui._render_preview_images(frame, frame, time_position=5.0)

        self.assertEqual(self.gui.loading_frame.grid_info(), {})        # spinner gone
        self.assertNotEqual(self.gui.original_title_label.grid_info(), {})  # revealed
        self.assertNotEqual(self.gui.converted_title_label.grid_info(), {})
        self.assertNotEqual(self.gui.button_container.grid_info(), {})
        self.assertTrue(self.gui.converted_image_label.cget('image'))

    def test_gamma_trough_click_jumps_knob_to_position(self):
        # A click near the far right of the trough must jump the gamma value near
        # the maximum (3.0), not nudge it by a fixed step. The withdrawn window
        # isn't laid out, so stub the realized width for a deterministic mapping.
        slider = self.gui.gamma_slider
        event = types.SimpleNamespace(x=199, y=10)
        with patch.object(slider, 'identify', return_value='trough'), \
             patch.object(slider, 'winfo_width', return_value=200):
            self.gui._gamma_slider_jump(event)
        self.assertGreater(self.gui.gamma_var.get(), 2.5)  # real ttk.Scale variable

    def test_gamma_change_updates_preview_without_reextracting(self):
        # With a cached SDR frame, a gamma change is a pure PIL pass: it updates
        # the converted label and never falls back to ffmpeg re-extraction.
        from PIL import Image as PILImage
        # ImageTk.PhotoImage binds to tkinter._default_root; pin it to this test's
        # root so a stale default from another test doesn't break image creation.
        tk._default_root = self.root
        self.gui.display_image_var.set(True)
        self.gui._converted_preview_base = PILImage.new('RGB', (960, 540), (50, 60, 70))
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            self.gui.gamma_var.set(2.0)
            self.gui.on_gamma_change()
        mock_update.assert_not_called()
        self.assertTrue(self.gui.converted_image_label.cget('image'))


class TestInfoLabel(_GuiTestBase):
    """HDR metadata info strip shown below the output path once a file is loaded."""

    def test_info_label_exists(self):
        self.assertIsInstance(self.gui.info_label, ttk.Label)

    def test_info_label_hidden_before_file_load(self):
        self.assertEqual(self.gui.info_label.grid_info(), {})

    @patch('src.gui.get_maxcll', return_value=400.0)
    @patch('src.gui.get_video_properties')
    @patch('src.gui.filedialog.askopenfilename')
    def test_info_label_shown_after_file_select(self, mock_dialog, mock_props, _mock_maxcll):
        mock_dialog.return_value = 'movie.mkv'
        mock_props.return_value = {
            'width': 3840, 'height': 2160, 'frame_rate': 23.976,
            'codec_name': 'hevc', 'audio_codec': 'truehd',
            'color_primaries': 'bt2020', 'color_transfer': 'smpte2084',
        }
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.select_file()
        self.assertNotEqual(self.gui.info_label.grid_info(), {})
        self.assertIn('3840', self.gui.info_label.cget('text'))
        self.assertIn('HDR', self.gui.info_label.cget('text'))

    @patch('src.gui.get_video_properties', return_value=None)
    @patch('src.gui.filedialog.askopenfilename')
    def test_info_label_hidden_when_props_unavailable(self, mock_dialog, _mock_props):
        mock_dialog.return_value = 'movie.mkv'
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.select_file()
        self.assertEqual(self.gui.info_label.grid_info(), {})


class TestDropTargetAndClose(_GuiTestBase):

    def test_unregister_then_register_round_trip(self):
        self.gui.unregister_drop_target()
        self.assertFalse(self.gui.drop_target_registered)
        self.gui.register_drop_target()
        self.assertTrue(self.gui.drop_target_registered)

    @patch('src.gui.conversion_manager')
    def test_on_close_destroys_when_idle(self, mock_cm):
        # on_close() destroys self.root.  Use a dedicated temporary root so
        # the module-level _probe_root stays alive and Tcl remains initialized
        # for any tests that follow this one.  Creating a second Tk() while
        # _probe_root is alive is safe — the Tcl library is already loaded.
        with patch('src.gui.load_settings', return_value=dict(DEFAULTS)), \
             patch('src.gui.save_settings'):
            tmp_root = TkinterDnD.Tk()
            tmp_root.withdraw()
            tmp_gui = HDRConverterGUI(tmp_root, licensed=True)
        mock_cm.process = None
        tmp_gui.on_close()
        # Destroying the root tears down its Tcl interpreter, so any further
        # call on it raises TclError — that is the proof the window was destroyed.
        with self.assertRaises(tk.TclError):
            tmp_root.winfo_exists()


class _LicensingBase(unittest.TestCase):
    """Shared plumbing for licensing tests: patches load/save_settings at class level."""

    _class_patches: list = []
    _class_gui: 'HDRConverterGUI | None' = None

    @classmethod
    def _start_patches(cls) -> None:
        load_p = patch('src.gui.load_settings', return_value=dict(DEFAULTS))
        save_p = patch('src.gui.save_settings')
        load_p.start()
        save_p.start()
        cls._class_patches = [load_p, save_p]

    @classmethod
    def tearDownClass(cls) -> None:
        for p in cls._class_patches:
            p.stop()

    def setUp(self) -> None:
        self.gui = self.__class__._class_gui

    def tearDown(self) -> None:
        pass  # patches live at class level, not instance level


@unittest.skipUnless(_TK_OK, _SKIP)
class TestUnlicensedState(_LicensingBase):
    """Read-only checks on an unlicensed GUI — one construction shared across all tests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._start_patches()
        drain_after_timers(_probe_root)
        for w in _probe_root.winfo_children():
            w.destroy()
        cls._class_gui = HDRConverterGUI(_probe_root, licensed=False)

    def test_gpu_checkbox_enabled_when_unlicensed(self):
        # GPU acceleration is free; the checkbox must stay enabled without a license.
        self.assertFalse(self.gui.gpu_accel_checkbutton.instate(['disabled']))

    def test_disables_quality_controls(self):
        self.assertTrue(self.gui.quality_slider.instate(['disabled']))
        self.assertTrue(self.gui.quality_entry.instate(['disabled']))

    def test_disables_quality_mode_combobox(self):
        self.assertTrue(self.gui.quality_mode_combobox.instate(['disabled']))

    def test_disables_custom_seek(self):
        self.assertTrue(self.gui.custom_time_entry.instate(['disabled']))
        self.assertTrue(self.gui.custom_seek_button.instate(['disabled']))

    def test_restricts_format_to_mp4(self):
        self.assertEqual(list(self.gui.format_combobox['values']), ['MP4'])
        self.assertEqual(self.gui.format_var.get(), 'MP4')

    def test_disables_batch_buttons(self):
        self.assertTrue(self.gui.add_files_button.instate(['disabled']))
        self.assertTrue(self.gui.remove_batch_button.instate(['disabled']))
        self.assertTrue(self.gui.clear_batch_button.instate(['disabled']))
        self.assertTrue(self.gui.apply_settings_button.instate(['disabled']))

    def test_shows_pro_banner(self):
        self.assertNotEqual(self.gui._pro_banner.grid_info(), {})

    def test_excludes_premium_from_interactable_elements(self):
        # GPU is free, so gpu_accel_checkbutton IS included even when unlicensed.
        # 10-bit is free too, so bit_depth_10_radio is included; 12-bit is Pro.
        premium = [
            self.gui.quality_slider, self.gui.quality_entry, self.gui.quality_mode_combobox,
            self.gui.format_combobox, self.gui.custom_time_entry,
            self.gui.custom_seek_button, self.gui.add_files_button,
            self.gui.clear_batch_button, self.gui.remove_batch_button,
            self.gui.bit_depth_12_radio,
        ]
        for widget in premium:
            self.assertNotIn(widget, self.gui.interactable_elements,
                             msg=f'{widget} must not be in interactable_elements when unlicensed')
        self.assertIn(self.gui.gpu_accel_checkbutton, self.gui.interactable_elements)
        self.assertIn(self.gui.bit_depth_10_radio, self.gui.interactable_elements)

    def test_multifile_drop_blocked(self):
        event = MagicMock()
        event.data = '/file1.mp4 /file2.mkv'
        with patch('src.gui.messagebox.showinfo') as mock_info:
            self.gui.handle_file_drop(event)
        mock_info.assert_called_once()
        self.assertEqual(self.gui.batch_items, [])

    def test_selected_bit_depth_capped_at_ten_when_unlicensed(self):
        """Free tier gets 10-bit output for a high-bit-depth source -- 12-bit
        stays Pro-only, but is never silently downgraded all the way to 8."""
        self.gui._source_bit_depth = 12
        self.assertEqual(self.gui._selected_bit_depth(), 10)


@unittest.skipUnless(_TK_OK, _SKIP)
class TestLicensedState(_LicensingBase):
    """Read-only checks on a licensed GUI — one construction shared across all tests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._start_patches()
        drain_after_timers(_probe_root)
        for w in _probe_root.winfo_children():
            w.destroy()
        cls._class_gui = HDRConverterGUI(_probe_root, licensed=True)

    def test_enables_gpu_checkbox(self):
        self.assertFalse(self.gui.gpu_accel_checkbutton.instate(['disabled']))

    def test_enables_quality_controls(self):
        self.assertFalse(self.gui.quality_slider.instate(['disabled']))
        self.assertFalse(self.gui.quality_entry.instate(['disabled']))

    def test_enables_quality_mode_combobox(self):
        self.assertFalse(self.gui.quality_mode_combobox.instate(['disabled']))

    def test_enables_custom_seek(self):
        self.assertFalse(self.gui.custom_time_entry.instate(['disabled']))
        self.assertFalse(self.gui.custom_seek_button.instate(['disabled']))

    def test_shows_all_formats(self):
        self.assertEqual(list(self.gui.format_combobox['values']),
                         list(HDRConverterGUI._OUTPUT_FORMATS))

    def test_enables_batch_buttons(self):
        self.assertFalse(self.gui.add_files_button.instate(['disabled']))
        self.assertFalse(self.gui.remove_batch_button.instate(['disabled']))
        self.assertFalse(self.gui.clear_batch_button.instate(['disabled']))
        self.assertFalse(self.gui.apply_settings_button.instate(['disabled']))

    def test_hides_pro_banner(self):
        self.assertEqual(self.gui._pro_banner.grid_info(), {})

    def test_includes_premium_in_interactable_elements(self):
        premium = [
            self.gui.gpu_accel_checkbutton, self.gui.quality_slider, self.gui.quality_entry,
            self.gui.quality_mode_combobox,
            self.gui.format_combobox, self.gui.custom_time_entry,
            self.gui.custom_seek_button, self.gui.add_files_button,
            self.gui.clear_batch_button, self.gui.remove_batch_button,
            self.gui.bit_depth_10_radio, self.gui.bit_depth_12_radio,
        ]
        for widget in premium:
            self.assertIn(widget, self.gui.interactable_elements,
                          msg=f'{widget} must be in interactable_elements when licensed')

    def test_multifile_drop_allowed(self):
        event = MagicMock()
        event.data = '/file1.mp4 /file2.mkv'
        with patch.object(self.gui, 'add_batch_files') as mock_add:
            self.gui.handle_file_drop(event)
        mock_add.assert_called_once()

    def test_selected_bit_depth_ten_for_ten_bit_source(self):
        self.gui._source_bit_depth = 10
        self.assertEqual(self.gui._selected_bit_depth(), 10)

    def test_selected_bit_depth_eight_for_eight_bit_source(self):
        """No benefit to 10-bit output when the source has no extra precision."""
        self.gui._source_bit_depth = 8
        self.assertEqual(self.gui._selected_bit_depth(), 8)

    def test_selected_bit_depth_twelve_when_toggle_set(self):
        self.gui._source_bit_depth = 12
        self.gui._update_bit_depth_choice()
        self.gui.bit_depth_var.set('12-bit')
        self.assertEqual(self.gui._selected_bit_depth(), 12)

    def test_selected_bit_depth_defaults_to_ten_above_ten_bit_source(self):
        """The toggle defaults to 10-bit each time it (re)appears."""
        self.gui._source_bit_depth = 12
        self.gui._update_bit_depth_choice()
        self.assertEqual(self.gui._selected_bit_depth(), 10)


@unittest.skipUnless(_TK_OK, _SKIP)
class TestLicenseTransition(unittest.TestCase):
    """State-mutating tests — fresh GUI per test (unavoidable)."""

    def setUp(self) -> None:
        self._load_patch = patch('src.gui.load_settings', return_value=dict(DEFAULTS))
        self._save_patch = patch('src.gui.save_settings')
        self._load_patch.start()
        self._save_patch.start()
        drain_after_timers(_probe_root)
        for w in _probe_root.winfo_children():
            w.destroy()

    def tearDown(self) -> None:
        self._load_patch.stop()
        self._save_patch.stop()

    def _make_gui(self, licensed: bool) -> HDRConverterGUI:
        return HDRConverterGUI(_probe_root, licensed=licensed)

    def test_apply_license_state_unlocks_all_premium_features(self):
        # GPU stays enabled at all times; only quality/batch/format are Pro-gated.
        gui = self._make_gui(licensed=False)
        self.assertFalse(gui.gpu_accel_checkbutton.instate(['disabled']))
        gui._apply_license_state(True)
        self.assertFalse(gui.gpu_accel_checkbutton.instate(['disabled']))
        self.assertFalse(gui.quality_slider.instate(['disabled']))
        self.assertFalse(gui.quality_entry.instate(['disabled']))
        self.assertFalse(gui.add_files_button.instate(['disabled']))
        self.assertEqual(list(gui.format_combobox['values']),
                         list(HDRConverterGUI._OUTPUT_FORMATS))
        self.assertEqual(gui._pro_banner.grid_info(), {})

    def test_load_input_forces_mp4_when_unlicensed(self):
        gui = self._make_gui(licensed=False)
        with patch.object(gui, '_update_info_label'), \
             patch.object(gui, 'update_frame_preview'), \
             patch.object(gui, 'highlight_frame_button'), \
             patch.object(gui, '_reset_custom_seek'), \
             patch.object(gui, '_reset_preview_cache'):
            gui._load_input_file('/some/video.mkv')
        self.assertEqual(gui.format_var.get(), 'MP4')
        self.assertTrue(gui.output_path_var.get().endswith('.mp4'))

    def test_load_input_uses_native_format_when_licensed(self):
        gui = self._make_gui(licensed=True)
        with patch.object(gui, '_update_info_label'), \
             patch.object(gui, 'update_frame_preview'), \
             patch.object(gui, 'highlight_frame_button'), \
             patch.object(gui, '_reset_custom_seek'), \
             patch.object(gui, '_reset_preview_cache'):
            gui._load_input_file('/some/video.mkv')
        self.assertEqual(gui.format_var.get(), 'MKV')
        self.assertTrue(gui.output_path_var.get().endswith('.mkv'))


@unittest.skipUnless(_TK_OK, _SKIP)
class TestBitDepthToggle(unittest.TestCase):
    """The 10/12-bit toggle: appears only for >10-bit sources, labeled/enabled
    per license state, placed next to the tonemapper selector, and refreshes
    immediately on a mid-session license activation."""

    def setUp(self) -> None:
        self._load_patch = patch('src.gui.load_settings', return_value=dict(DEFAULTS))
        self._save_patch = patch('src.gui.save_settings')
        self._load_patch.start()
        self._save_patch.start()
        drain_after_timers(_probe_root)
        for w in _probe_root.winfo_children():
            w.destroy()

    def tearDown(self) -> None:
        self._load_patch.stop()
        self._save_patch.stop()

    def _make_gui(self, licensed: bool) -> HDRConverterGUI:
        return HDRConverterGUI(_probe_root, licensed=licensed)

    def test_hidden_for_le_ten_bit_source(self):
        gui = self._make_gui(licensed=True)
        gui._source_bit_depth = 10
        gui._update_bit_depth_choice()
        self.assertEqual(gui.bit_depth_frame.grid_info(), {})

    def test_visible_licensed_shows_cpu_only_and_enabled(self):
        gui = self._make_gui(licensed=True)
        gui._source_bit_depth = 12
        gui._update_bit_depth_choice()
        self.assertNotEqual(gui.bit_depth_frame.grid_info(), {})
        self.assertEqual(gui.bit_depth_var.get(), '10-bit')
        self.assertIn('CPU Only', gui.bit_depth_12_radio.cget('text'))
        self.assertFalse(gui.bit_depth_12_radio.instate(['disabled']))

    def test_visible_unlicensed_shows_pro_and_disabled(self):
        gui = self._make_gui(licensed=False)
        gui._source_bit_depth = 12
        gui._update_bit_depth_choice()
        self.assertNotEqual(gui.bit_depth_frame.grid_info(), {})
        self.assertIn('Pro', gui.bit_depth_12_radio.cget('text'))
        self.assertTrue(gui.bit_depth_12_radio.instate(['disabled']))

    def test_refreshes_on_mid_session_license_activation(self):
        gui = self._make_gui(licensed=False)
        gui._source_bit_depth = 12
        gui._update_bit_depth_choice()
        self.assertTrue(gui.bit_depth_12_radio.instate(['disabled']))

        gui._apply_license_state(True)

        self.assertFalse(gui.bit_depth_12_radio.instate(['disabled']))
        self.assertIn('CPU Only', gui.bit_depth_12_radio.cget('text'))

    def test_grid_placement_next_to_tonemapper(self):
        """The toggle lives inside the tonemapper's row frame (column 1, the
        stretchy column) -- NOT in control_frame's column 2, where its width
        would stretch the Browse/format/gamma widgets stacked above it."""
        gui = self._make_gui(licensed=True)
        gui._source_bit_depth = 12
        gui._update_bit_depth_choice()
        self.assertIs(gui.bit_depth_frame.master, gui.tonemap_frame)
        info = gui.bit_depth_frame.grid_info()
        combo_info = gui.tonemap_combobox.grid_info()
        self.assertEqual(int(info['row']), int(combo_info['row']))
        self.assertGreater(int(info['column']), int(combo_info['column']))

    @staticmethod
    def _column_req_width(gui, column: int) -> int:
        """Requested width of a control_frame grid column: the max reqwidth of
        the widgets managed in it alone (how grid sizes a weight-0 column --
        columnspan>1 widgets spread across columns and don't pin this one)."""
        gui.root.update_idletasks()
        widths = [w.winfo_reqwidth()
                  for w in gui.control_frame.grid_slaves(column=column)
                  if int(w.grid_info().get('columnspan', 1)) == 1]
        return max(widths) if widths else 0

    def test_showing_toggle_does_not_widen_browse_column(self):
        """Regression: the toggle used to be gridded into control_frame column
        2, so revealing it stretched the Browse button, format combobox and
        gamma entry (all sticky EW in that column) to the toggle's width."""
        gui = self._make_gui(licensed=True)
        before = self._column_req_width(gui, 2)

        gui._source_bit_depth = 12
        gui._update_bit_depth_choice()

        self.assertEqual(self._column_req_width(gui, 2), before)

    def _twelve_bit_props(self):
        return {
            'width': 1920, 'height': 1080, 'frame_rate': 24.0,
            'codec_name': 'hevc', 'audio_codec': 'aac',
            'color_primaries': 'bt2020', 'color_transfer': 'smpte2084',
            'bit_depth': 12,
        }

    def test_clicking_twelve_bit_radio_refreshes_info_label_live(self):
        """Toggling the radio must update the info strip immediately, without
        re-probing the file -- it reuses the cached probe results."""
        gui = self._make_gui(licensed=True)
        gui._source_bit_depth = 12
        gui._cached_props = self._twelve_bit_props()
        gui._cached_maxcll = 1000.0
        gui._update_bit_depth_choice()
        gui._refresh_info_label_text()
        self.assertIn('12-bit -> 10-bit', gui.info_label.cget('text'))

        gui.bit_depth_12_radio.invoke()

        text = gui.info_label.cget('text')
        self.assertIn('12-bit', text)
        self.assertNotIn('->', text)  # source now matches the chosen output

    def test_unlicensed_info_label_shows_pro_only_hint_for_high_bit_depth_source(self):
        gui = self._make_gui(licensed=False)
        gui._source_bit_depth = 12
        gui._cached_props = self._twelve_bit_props()
        gui._cached_maxcll = 1000.0
        gui._update_bit_depth_choice()
        gui._refresh_info_label_text()
        self.assertIn('12-bit -> 10-bit (Pro Only)', gui.info_label.cget('text'))

    def test_unload_hides_toggle_and_clears_cached_state(self):
        """Unloading the file (e.g. clearing the batch queue) must hide the
        toggle and drop the cached probe state, or the widget lingers for a
        file that's no longer loaded."""
        gui = self._make_gui(licensed=True)
        gui._source_bit_depth = 12
        gui._cached_props = self._twelve_bit_props()
        gui._cached_maxcll = 1000.0
        gui._update_bit_depth_choice()
        gui._refresh_info_label_text()
        self.assertNotEqual(gui.bit_depth_frame.grid_info(), {})

        with patch.object(gui, 'update_frame_preview'):
            gui._unload_input_file()

        self.assertEqual(gui.bit_depth_frame.grid_info(), {})
        self.assertEqual(gui._source_bit_depth, 8)
        self.assertIsNone(gui._cached_props)
        self.assertIsNone(gui._cached_maxcll)

    def test_license_activation_after_unload_does_not_resurrect_stale_state(self):
        """Activating a license after the file was unloaded must not re-show
        the info strip (or the toggle) from stale cached probe results."""
        gui = self._make_gui(licensed=False)
        gui._source_bit_depth = 12
        gui._cached_props = self._twelve_bit_props()
        gui._cached_maxcll = 1000.0
        gui._update_bit_depth_choice()
        gui._refresh_info_label_text()
        with patch.object(gui, 'update_frame_preview'):
            gui._unload_input_file()

        gui._apply_license_state(True)

        self.assertEqual(gui.info_label.cget('text'), '')
        self.assertEqual(gui.info_label.grid_info(), {})
        self.assertEqual(gui.bit_depth_frame.grid_info(), {})

    # Per-queue-item bit depth choice (batch_items-driven) moved to
    # src/pro/test/batch_test.py's TestBitDepthToggleBatchQueue -- it
    # exercises the real _BatchMixin (_update_bit_depth_choice reading a
    # matching queue item's stored choice, start_batch reloading it) -- see
    # task-6 of the Pro/private-repo split.


@unittest.skipUnless(_TK_OK, _SKIP)
class TestDropToQueue(unittest.TestCase):
    """Licensed single-file drops route through the batch queue (so dropping
    onto a populated queue adds to it instead of bypassing it); unlicensed
    drops keep the plain load-only behavior since batch is Pro.

    Only the unlicensed case lives here: it never touches the real
    _BatchMixin (batch_items stays empty, only the license-agnostic
    _load_input_file path runs). Every licensed case -- which exercises the
    real add_batch_files -- moved to src/pro/test/batch_test.py's
    TestDropToQueue -- see task-6 of the Pro/private-repo split.
    """

    def setUp(self) -> None:
        self._load_patch = patch('src.gui.load_settings', return_value=dict(DEFAULTS))
        self._save_patch = patch('src.gui.save_settings')
        self._load_patch.start()
        self._save_patch.start()
        drain_after_timers(_probe_root)
        for w in _probe_root.winfo_children():
            w.destroy()

    def tearDown(self) -> None:
        self._load_patch.stop()
        self._save_patch.stop()

    def _make_gui(self, licensed: bool) -> HDRConverterGUI:
        return HDRConverterGUI(_probe_root, licensed=licensed)

    def test_single_drop_unlicensed_only_loads(self):
        gui = self._make_gui(licensed=False)
        with patch.object(gui, '_load_input_file') as mock_load:
            gui.handle_file_drop(MagicMock(data='{C:/a.mkv}'))
        self.assertEqual(gui.batch_items, [])
        mock_load.assert_called_once_with('C:/a.mkv')


class TestCenterOverMaster(unittest.TestCase):
    """_center_over_master is the shared sizing/centering routine behind both
    _LicenseDialog and _UpdateDialog's __init__ -- they used to each inline
    their own copy differing only in the floor width/height."""

    def test_computes_size_and_centers_geometry(self):
        from src.dialogs import _center_over_master
        win = MagicMock()
        win.winfo_reqwidth.return_value = 300
        win.winfo_reqheight.return_value = 100
        master = MagicMock()
        master.winfo_rootx.return_value = 50
        master.winfo_rooty.return_value = 60
        master.winfo_width.return_value = 800
        master.winfo_height.return_value = 600

        _center_over_master(win, master, min_w=200, min_h=150)

        win.update_idletasks.assert_called_once()
        win.geometry.assert_called_once_with('340x150+280+285')
        win.grab_set.assert_called_once()
        win.focus_set.assert_called_once()

    def test_floors_at_min_dimensions_when_content_is_small(self):
        from src.dialogs import _center_over_master
        win = MagicMock()
        win.winfo_reqwidth.return_value = 10
        win.winfo_reqheight.return_value = 10
        master = MagicMock()
        master.winfo_rootx.return_value = 0
        master.winfo_rooty.return_value = 0
        master.winfo_width.return_value = 100
        master.winfo_height.return_value = 100

        _center_over_master(win, master, min_w=460, min_h=220)

        win.geometry.assert_called_once_with('460x220+-180+-60')


@unittest.skipUnless(_TK_OK, _SKIP)
class TestUpdateDialog(unittest.TestCase):
    """Tests for the _UpdateDialog Toplevel's changelog link."""

    def setUp(self) -> None:
        drain_after_timers(_probe_root)
        for w in _probe_root.winfo_children():
            w.destroy()

    def tearDown(self) -> None:
        for w in _probe_root.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    _RELEASE_URL = 'https://github.com/TORlN/HDR-to-SDR/releases'

    def _make_dialog(self):  # type: ignore[return]
        from src.gui import _UpdateDialog  # type: ignore[attr-defined]
        dlg = _UpdateDialog(_probe_root, '3.0.0', '4.0.0',
                             'https://example.com/setup.exe', self._RELEASE_URL)
        dlg.withdraw()
        return dlg

    def _start_download_sync(self, dlg) -> None:
        """Drive _start_download() with the worker thread replaced by a
        synchronous stand-in, then pump the Tk event queue so the
        self.after(0, ...) completion/error callback (real Tk) actually runs."""
        with patch('src.dialogs.threading.Thread', _SyncThread):
            dlg._start_download()
        dlg.update()

    def test_retry_after_failure_cleans_up_previous_temp_dir(self):
        """Each Retry click used to mint a fresh temp dir via mkdtemp without
        ever removing the previous failed attempt's -- an unbounded leak of
        empty (or partial-download) directories under the temp root."""
        dlg = self._make_dialog()
        with patch('updater.download_installer', side_effect=OSError('disk full')):
            self._start_download_sync(dlg)
        first_tmp_dir = dlg._tmp_dir
        self.assertIsNotNone(first_tmp_dir)
        self.assertTrue(os.path.isdir(first_tmp_dir))

        with patch('updater.download_installer', side_effect=OSError('disk full')):
            self._start_download_sync(dlg)
        second_tmp_dir = dlg._tmp_dir

        self.assertNotEqual(first_tmp_dir, second_tmp_dir)
        self.assertFalse(os.path.isdir(first_tmp_dir),
                         "previous attempt's temp dir must be cleaned up on retry")
        dlg.destroy()
        import shutil as _shutil
        _shutil.rmtree(second_tmp_dir, ignore_errors=True)

    def test_successful_download_temp_dir_is_not_touched_by_a_later_retry_path(self):
        """A successful download's temp dir holds the .exe the detached
        installer is about to run from -- _start_download must never delete
        it (only a subsequent _start_download call may clean up its own
        prior *failed* attempt)."""
        dlg = self._make_dialog()
        with patch('updater.download_installer'):  # succeeds, does nothing
            self._start_download_sync(dlg)
        tmp_dir = dlg._tmp_dir
        self.assertIsNotNone(tmp_dir)
        self.assertTrue(os.path.isdir(tmp_dir))
        dlg.destroy()
        import shutil as _shutil
        _shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_later_button_cleans_up_temp_dir_after_failed_download(self):
        """A failed download leaves _tmp_dir pointing at a (possibly partial)
        download directory; clicking Later must not leak it."""
        dlg = self._make_dialog()
        with patch('updater.download_installer', side_effect=OSError('disk full')):
            self._start_download_sync(dlg)
        tmp_dir = dlg._tmp_dir
        self.assertIsNotNone(tmp_dir)
        self.assertTrue(os.path.isdir(tmp_dir))
        dlg._later_btn.invoke()
        self.assertFalse(os.path.isdir(tmp_dir),
                         "failed download's temp dir must be cleaned up when Later is clicked")

    def test_window_close_cleans_up_temp_dir_after_failed_download(self):
        """Closing the dialog (the WM_DELETE_WINDOW path, re-armed after a
        failed download) must not leak the failed attempt's temp dir either."""
        dlg = self._make_dialog()
        with patch('updater.download_installer', side_effect=OSError('disk full')):
            self._start_download_sync(dlg)
        tmp_dir = dlg._tmp_dir
        self.assertIsNotNone(tmp_dir)
        self.assertTrue(os.path.isdir(tmp_dir))
        dlg.destroy()
        self.assertFalse(os.path.isdir(tmp_dir),
                         "failed download's temp dir must be cleaned up when the window is closed")

    def test_changelog_link_widget_exists(self):
        dlg = self._make_dialog()
        texts = [w.cget('text') for w in dlg.winfo_children()
                 if isinstance(w, tk.Label)]
        self.assertTrue(
            any('changelog' in t.lower() for t in texts),
            f"Expected a label mentioning 'changelog'; found: {texts}",
        )
        dlg.destroy()

    def test_changelog_link_opens_release_url(self):
        dlg = self._make_dialog()
        with patch('src.dialogs.webbrowser') as mock_wb:
            dlg._open_changelog()
        mock_wb.open.assert_called_once_with(self._RELEASE_URL)
        dlg.destroy()


class TestDolbyVisionInfoBarTag(_GuiTestBase):
    """Dolby Vision detection is folded into the real info-strip label as its
    own '|'-separated segment (no separate badge widget): absent on startup,
    present once a Dolby Vision file's metadata is loaded, gone again for
    non-DoVi files."""

    @staticmethod
    def _props(dovi=True):
        return {
            'width': 3840, 'height': 2160, 'frame_rate': 23.976,
            'codec_name': 'hevc', 'audio_codec': 'truehd',
            'color_primaries': 'bt2020', 'color_transfer': 'smpte2084',
            'bit_depth': 10, 'duration': 600.0, 'subtitle_streams': [],
            'is_dolby_vision': dovi, 'dovi_profile': 8 if dovi else None,
        }

    def _load_metadata(self, dovi):
        with patch('src.gui.get_video_properties', return_value=self._props(dovi)), \
             patch('src.gui.get_maxcll', return_value=1000.0):
            self.gui._update_info_label('movie.mkv')

    def test_no_tag_on_startup(self):
        self.assertNotIn('Dolby Vision', self.gui.info_label.cget('text'))

    def test_tag_renders_on_dovi_import(self):
        self._load_metadata(dovi=True)
        self.assertEqual(self.gui.info_label.winfo_manager(), 'grid')
        self.assertIn('Dolby Vision', self.gui.info_label.cget('text'))

    def test_tag_hides_again_for_non_dovi_file(self):
        self._load_metadata(dovi=True)
        self._load_metadata(dovi=False)
        self.assertNotIn('Dolby Vision', self.gui.info_label.cget('text'))


class TestFeedbackLink(_GuiTestBase):
    """A footer link opens the GitHub issues page for bug reports/feature
    requests, anchored bottom-right so the rest of the footer row stays free
    for future additions."""

    def test_link_widget_exists_bottom_right(self):
        info = self.gui.feedback_link.grid_info()
        self.assertEqual(str(info['sticky']), 'e')
        self.assertIn('issue', self.gui.feedback_link.cget('text').lower())

    def test_click_opens_github_issues_page(self):
        with patch('src.gui.webbrowser') as mock_wb:
            self.gui._open_issues_page()
        mock_wb.open.assert_called_once_with('https://github.com/TORlN/HDR-to-SDR/issues')


class TestFixturesDoNotEscapeTheirMocks(_GuiTestBase):
    """Guards on the suite itself: no test may reach ffprobe or the network.

    Both escapes were real and both were invisible, because the production code
    swallows the resulting errors -- the suite stayed green while shelling out
    to ffprobe on every file-select test and opening 40+ TLS connections to
    GitHub per run. Offline, those connections turn into DNS timeouts.

    These deliberately use the shared _GuiTestBase fixture and add no mocks of
    their own, so they fail if that fixture ever stops covering the seams.
    """

    def test_fixture_setup_drains_the_auto_update_timer(self):
        """HDRConverterGUI.__init__ arms root.after(3000, _start_update_check),
        whose worker fetches the releases feed over HTTPS. The timer outlives
        the test that armed it, so without draining it fires inside whichever
        *later* test first pumps this shared root -- which is why the real
        network hits landed on the update-dialog tests that carefully mock
        download_installer, and why stale '_start_update_check' Tcl scripts
        were erroring against destroyed widgets."""
        # setUp already built a GUI, which armed the timer.
        self.assertTrue(
            [s for s in pending_after_scripts(self.root)
             if '_start_update_check' in s],
            'construction is expected to arm the update timer; if it no longer '
            'does, this guard tests nothing and should be removed')

        drain_after_timers(self.root)  # what every GUI fixture setUp now does

        self.assertEqual(
            [s for s in pending_after_scripts(self.root)
             if '_start_update_check' in s], [],
            'a drained event queue must not leave the update check armed')

    def test_preview_pool_is_built_at_the_hardware_scaled_width(self):
        """The preview pool must stay hardware-scaled (max(1, cpu_count // 4)),
        introduced in d7274a6 to replace a single daemon thread.

        Observed on the real, fully-constructed GUI rather than by re-deriving
        the formula: characterization_test asserts the *constant* is correct,
        which would still pass if this call site were changed to a fixed
        max_workers. _max_workers is private but stable across CPython
        versions, and it is the only way to read back an executor's width.
        """
        from src.gui import _PREVIEW_POOL_WORKERS
        self.assertEqual(self.gui._preview_pool._max_workers,
                         _PREVIEW_POOL_WORKERS)
        self.assertEqual(_PREVIEW_POOL_WORKERS,
                         max(1, (os.cpu_count() or 1) // 4))

    def test_select_file_does_not_spawn_ffprobe(self):
        with no_real_subprocess('select_file'), no_real_dialogs('select_file'), \
                patch('src.gui.filedialog.askopenfilename', return_value='movie.mp4'), \
                patch.object(self.gui, 'update_frame_preview'):
            self.gui.select_file()

    def test_handle_file_drop_does_not_spawn_ffprobe(self):
        event = types.SimpleNamespace(data='{C:/videos/clip.mkv}')
        with no_real_subprocess('handle_file_drop'), \
                no_real_dialogs('handle_file_drop'), \
                patch.object(self.gui, 'update_frame_preview'):
            self.gui.handle_file_drop(event)


if __name__ == '__main__':
    unittest.main()
