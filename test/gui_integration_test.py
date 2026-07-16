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
import threading
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import tkinter as tk
from tkinter import ttk
from tkinterdnd2 import TkinterDnD

from src.gui import HDRConverterGUI, DEFAULT_MIN_SIZE
from src.conversion import conversion_manager
from src.utils import TONEMAP
from src.settings import DEFAULTS


# One Tk instance shared across the entire module.  Creating and destroying a
# Tk() per test causes Tcl to deinit/reinit its library on each cycle, which
# is unreliable when the system's Tcl installation is incomplete (e.g. a
# Python 3.13 install missing init.tcl at the expected path).  Keeping one
# interpreter alive for the whole run avoids all reinit.
_probe_root: "TkinterDnD.Tk | None" = None


def _tk_available() -> bool:
    global _probe_root
    try:
        _probe_root = TkinterDnD.Tk()
        _probe_root.withdraw()
        return True
    except Exception:
        return False


_TK_OK = _tk_available()
_SKIP = "no Tk display available (need a desktop session or xvfb)"


class _SyncThread:
    """Stand-in for threading.Thread that runs its target immediately, inline,
    instead of on a real worker thread -- lets tests assert on the result of
    a backgrounded call without sleeping/polling for a real thread to finish."""

    def __init__(self, target=None, daemon=None, **_kw):
        self._target = target

    def start(self) -> None:
        self._target()


@unittest.skipUnless(_TK_OK, _SKIP)
class _GuiTestBase(unittest.TestCase):
    def setUp(self):
        # Isolate tests from any on-disk settings file so default-value assertions
        # are deterministic regardless of what the user has saved.
        self._load_patch = patch('src.gui.load_settings', return_value=dict(DEFAULTS))
        self._save_patch = patch('src.gui.save_settings')
        self._load_patch.start()
        self._save_patch.start()
        # Reuse the module-level Tk — never destroy it between tests.
        # Destroying and recreating Tk forces Tcl to deinit/reinit, which is
        # unreliable on broken system Tcl installs.  Instead, destroy only the
        # child widgets so HDRConverterGUI can build a fresh tree on the same root.
        self.root = _probe_root
        for w in self.root.winfo_children():
            w.destroy()
        self.gui = HDRConverterGUI(self.root, licensed=True)

    def tearDown(self):
        self._load_patch.stop()
        self._save_patch.stop()


class TestConstruction(_GuiTestBase):

    def test_window_title_and_minsize(self):
        self.assertEqual(self.root.title(), "HDR to SDR Converter")
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
            self.gui.quality_slider, self.gui.quality_mode_combobox, self.gui.format_combobox,
            self.gui.custom_time_entry, self.gui.custom_seek_button,
            self.gui.add_files_button, self.gui.clear_batch_button,
            self.gui.remove_batch_button,
            self.gui.bit_depth_10_radio, self.gui.bit_depth_12_radio,
            self.gui.apply_settings_button,
        }
        self.assertEqual(set(self.gui.interactable_elements), expected)

    def test_batch_listbox_disabled_during_conversion(self):
        # Regression: batch_listbox was never gated by disable_ui, so clicking
        # a different queue row mid-conversion could overwrite input/output
        # path vars while a GPU->CPU retry was about to re-read them fresh,
        # corrupting which file gets converted to which path.
        conversion_manager.disable_ui(self.gui.interactable_elements)
        self.assertEqual(str(self.gui.batch_listbox.cget('state')), 'disabled')

    def test_drop_target_registered_on_start(self):
        self.assertTrue(self.gui.drop_target_registered)

    def test_quality_mode_combobox_grid_position(self):
        info = self.gui.quality_mode_frame.grid_info()
        self.assertEqual(int(info['row']), 4)
        self.assertEqual(int(info['column']), 1)

    def test_quality_mode_combobox_values_and_readonly(self):
        self.assertEqual(tuple(self.gui.quality_mode_combobox.cget('values')),
                         ('Constant Quality', 'Target Bitrate'))
        self.assertEqual(str(self.gui.quality_mode_combobox.cget('state')), 'readonly')

    def test_quality_value_label_shows_formatted_display_var(self):
        self.assertEqual(self.gui.quality_value_label.cget('textvariable'),
                         str(self.gui.quality_display_var))

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
    """Real-widget checks for the batch (multi-file) queue panel."""

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

    def test_add_batch_files_populates_listbox(self):
        with patch.object(self.gui, 'update_frame_preview'):  # don't spawn ffmpeg
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mkv'])
        self.assertEqual(self.gui.batch_listbox.size(), 2)
        self.assertIn('a.mp4', self.gui.batch_listbox.get(0))

    def test_add_batch_files_seeds_settings_from_live_controls(self):
        self.gui.gamma_var.set(1.7)
        self.gui.gpu_accel_var.set(False)
        self.gui.tonemap_var.set('Hable')
        self.gui.quality_mode_var.set('Constant Quality')
        # Drive the slider itself (not just the backing IntVar): quality_var
        # is only ever kept in sync with what the widget actually displays
        # via the slider's own -command callback, exactly like a real drag --
        # setting quality_var directly here would leave the widget still
        # showing its old position, which _apply_quality_range's knob-
        # preserving remap (run when this file loads) would then use as the
        # source of truth and silently revert.
        self.gui.quality_slider.set(21)
        self.gui.bitrate_var.set(6000)
        self.gui.bit_depth_var.set('10-bit')
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        settings = self.gui.batch_items[0]['settings']
        self.assertEqual(settings, {
            'gamma': 1.7, 'quality_mode': 'cq', 'quality': 21,
            'tonemapper': 'Hable', 'gpu_accel': False, 'bit_depth_choice': '10-bit',
            'bitrate_customized': False, 'bitrate_fraction': 0.75,
        })

    def test_clear_batch_empties_listbox(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        self.gui.clear_batch_queue()
        self.assertEqual(self.gui.batch_listbox.size(), 0)
        self.assertEqual(self.gui.batch_items, [])

    def test_clear_queue_then_add_file_does_not_inherit_stale_customized_bitrate(self):
        """A deliberately customized Target Bitrate on file A must not
        survive into an unrelated file B added after clearing the queue --
        otherwise B gets seeded from a bogus fraction (A's leftover kbps
        divided by the unknown-source 8,000 kbps fallback, since B hasn't
        been probed yet) and clamped to 100% of its own real bitrate on
        restore, instead of getting the normal 50% default."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])  # auto-loads A
            self.gui.quality_mode_var.set('Target Bitrate')
            self.gui._on_quality_change('20000')  # a real user drag: marks customized

            self.gui.clear_batch_queue()
            self.gui.add_batch_files(['C:/v/b.mp4'])

        self.assertFalse(self.gui.batch_items[0]['settings']['bitrate_customized'])

    @patch('src.gui.filedialog.askopenfilenames')
    def test_browse_batch_files_adds_selection(self, mock_dialog):
        mock_dialog.return_value = ('C:/v/a.mkv', 'C:/v/b.mkv')
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.browse_batch_files()
        self.assertEqual(self.gui.batch_listbox.size(), 2)

    def test_add_batch_files_loads_top_file_into_preview(self):
        # Adding files also loads the first one into the input/output boxes and
        # runs the preview, as if it had been selected directly.
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mkv'])
        self.assertEqual(self.gui.input_path_var.get(), 'C:/v/a.mp4')
        self.assertEqual(self.gui.output_path_var.get(), 'C:/v/a_sdr.mp4')
        mock_update.assert_called_once()

    def test_start_next_batch_item_no_longer_prompts_for_existing_output(self):
        """_start_next_batch_item must not check for or prompt about an
        existing output file -- output-path conflicts are now resolved
        earlier, by start_batch's two-click checkbox review flow, before
        this method ever runs."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        item = self.gui.batch_items[0]

        with patch('src.gui.os.path.isfile', return_value=True), \
             patch('src.gui.os.path.exists', return_value=True), \
             patch('src.gui.conversion_manager.start_conversion') as mock_conv, \
             patch.object(self.gui, '_load_input_file'):
            self.gui._start_next_batch_item()

        mock_conv.assert_called_once()
        self.assertEqual(item['status'], 'Converting')

    def test_batch_proceeds_when_output_does_not_exist(self):
        """When the output does not exist, _start_next_batch_item should start
        the conversion normally."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])

        with patch('src.gui.os.path.isfile', return_value=True), \
             patch('src.gui.os.path.exists', return_value=False), \
             patch('src.gui.conversion_manager.start_conversion') as mock_conv, \
             patch.object(self.gui, '_load_input_file'):
            self.gui._start_next_batch_item()

        mock_conv.assert_called_once()

    def test_finish_batch_restores_comboboxes_to_readonly_not_normal(self):
        """format_combobox/quality_mode_combobox are built 'readonly' so users
        can't type into them -- format_var.get() flows straight into the
        output filename. _finish_batch's blanket state='normal' must not
        make them freely editable again."""
        self.gui.format_combobox.config(state='disabled')
        self.gui.quality_mode_combobox.config(state='disabled')

        with patch('src.batch.messagebox'):
            self.gui._finish_batch()

        self.assertEqual(str(self.gui.format_combobox.cget('state')), 'readonly')
        self.assertEqual(str(self.gui.quality_mode_combobox.cget('state')), 'readonly')

    def test_batch_item_that_fails_to_start_does_not_stall_the_queue(self):
        """When start_conversion bails out synchronously (e.g. a guard like
        missing duration) it now calls on_complete(False) instead of just
        returning -- this must mark the item Failed (not leave it stuck at
        'Converting') and advance to the next item rather than stalling."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4'])

        def fake_start_conversion(*args, **kwargs):
            kwargs['on_complete'](False)  # simulates a guard firing synchronously
            return False

        with patch('src.batch.conversion_manager') as mock_cm, \
             patch('src.batch.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=False), \
             patch.object(self.gui, '_load_input_file'), \
             patch.object(self.gui, '_finish_batch') as mock_finish:
            mock_cm.start_conversion.side_effect = fake_start_conversion
            mock_cm.cancelled = False
            self.gui.start_batch()

        self.assertEqual(self.gui.batch_items[0]['status'], 'Failed')
        self.assertEqual(self.gui.batch_items[1]['status'], 'Failed')
        mock_finish.assert_called_once()  # queue drained instead of stalling

    def test_batch_proceeds_when_user_checks_the_conflicting_item(self):
        """Checking the conflicting row and clicking Convert again lets the
        conversion proceed, even though the output path already exists."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        item = self.gui.batch_items[0]

        with patch('src.gui.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=True), \
             patch('src.gui.conversion_manager.start_conversion') as mock_conv, \
             patch.object(self.gui, '_load_input_file'):
            self.gui.start_batch()  # first click: enters review
            self.gui._toggle_batch_conflict_item(item)
            self.gui.start_batch()  # second click: confirmed

        mock_conv.assert_called_once()
        self.assertEqual(item['status'], 'Converting')

    def test_declining_a_conflict_marks_the_item_skipped_not_failed(self):
        """Leaving a conflicting row unchecked and confirming skips that
        item for this run -- it is not treated as an error."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        item = self.gui.batch_items[0]

        with patch('src.gui.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=True), \
             patch('src.gui.conversion_manager.start_conversion') as mock_conv, \
             patch.object(self.gui, '_finish_batch'):
            self.gui.start_batch()  # enters review, item left unchecked
            self.gui.start_batch()  # confirm with nothing checked

        mock_conv.assert_not_called()
        self.assertEqual(item['status'], 'Skipped')

    def test_clicking_a_conflict_row_toggles_its_checkbox(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        item = self.gui.batch_items[0]

        with patch('src.gui.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=True):
            self.gui.start_batch()  # enters review

        self.gui.batch_listbox.update_idletasks()
        bbox = self.gui.batch_listbox.bbox(0)
        y = bbox[1] + bbox[3] // 2
        self.gui._on_batch_listbox_click(types.SimpleNamespace(y=y))

        self.assertTrue(self.gui._batch_conflict_selection[id(item)])

    def test_restarting_batch_enters_review_before_reconverting_item_whose_output_exists(self):
        """Re-running Start Batch after a prior run finished must enter
        conflict review before redoing the conversion, since the item's own
        output file from that prior successful run is still sitting on disk
        at the same path. The first click only re-queues and reviews;
        checking the item and clicking again finalizes and redoes it."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        item = self.gui.batch_items[0]

        with patch('src.batch.conversion_manager'), \
             patch('src.batch.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=False), \
             patch.object(self.gui, '_load_input_file'):
            self.gui.start_batch()
        item['status'] = 'Done'  # simulate the prior run completing successfully

        with patch('src.batch.conversion_manager') as mock_cm, \
             patch('src.batch.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=True), \
             patch.object(self.gui, '_load_input_file'):
            result = self.gui.start_batch()  # first click: enters review

        self.assertFalse(result)
        mock_cm.start_conversion.assert_not_called()
        self.assertEqual(item['status'], 'Pending')
        self.assertEqual(self.gui._batch_conflict_groups, [[item]])

        self.gui._toggle_batch_conflict_item(item)  # user picks it to redo

        with patch('src.batch.conversion_manager') as mock_cm, \
             patch('src.batch.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=True), \
             patch.object(self.gui, '_load_input_file'):
            result = self.gui.start_batch()  # second click: confirmed

        self.assertTrue(result)
        mock_cm.start_conversion.assert_called_once()
        self.assertEqual(item['status'], 'Converting')

    def test_selecting_queue_item_restores_its_own_settings(self):
        with patch.object(self.gui, 'update_frame_preview'):
            # Item A auto-loads on add (it's the first file); stamp its own
            # gamma/tonemapper onto its stored settings.
            self.gui.add_batch_files(['C:/v/a.mp4'])
            self.gui.gamma_var.set(2.4)
            self.gui.tonemap_var.set('Hable')
            self.gui.batch_items[0]['settings'] = self.gui._current_settings_dict()

            # Item B is queued but NOT auto-loaded (a file is already loaded),
            # so selecting it below is what actually triggers _load_input_file.
            self.gui.add_batch_files(['C:/v/b.mp4'])
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()
            self.gui.gamma_var.set(0.6)
            self.gui.tonemap_var.set('Reinhard')
            self.gui.batch_items[1]['settings'] = self.gui._current_settings_dict()

            # Re-selecting A must restore A's own values, not leave B's live.
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(0)
            self.gui.on_batch_item_select()

        self.assertAlmostEqual(self.gui.gamma_var.get(), 2.4)
        self.assertEqual(self.gui.tonemap_var.get(), 'Hable')

    def test_selecting_queue_item_keeps_it_highlighted(self):
        """Regression: _load_input_file ends with _write_back_current_settings,
        which calls _refresh_batch_list -- and that rebuilds the listbox with
        delete(0, END) + reinsert, silently dropping whatever row Tk had just
        selected on click. Only the already-loaded item (which short-circuits
        before ever reaching _load_input_file) kept its highlight; every other
        queue item appeared to select then immediately lose its highlight."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])  # auto-loads A
            self.gui.add_batch_files(['C:/v/b.mp4'])  # queued, not auto-loaded

            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()  # loads B

        self.assertEqual(self.gui.batch_listbox.curselection(), (1,))

    def test_selecting_queue_item_restores_its_own_quality_in_constant_quality_mode(self):
        """Two queued items both in Constant Quality mode with different
        quality values: selecting B must show B's OWN quality, not a value
        clobbered by A's still-physically-in-place slider widget position
        via _apply_quality_range's knob-preserving remap (see
        .superpowers/sdd/task-10-report.md, "Third interaction effect
        found")."""
        with patch.object(self.gui, 'update_frame_preview'):
            # Item A auto-loads on add; drag its quality slider for real
            # (moves both the widget and quality_var, like an actual user
            # drag) and pin the mode explicitly to Constant Quality.
            self.gui.add_batch_files(['C:/v/a.mp4'])
            self.gui.quality_mode_var.set('Constant Quality')
            self.gui.quality_slider.set(18)
            self.gui.batch_items[0]['settings'] = self.gui._current_settings_dict()

            # Item B is queued but NOT auto-loaded (A is already loaded), so
            # its own quality is stamped directly onto its stored settings
            # (as if set on an earlier visit) rather than via the live
            # slider -- the widget itself is left showing A's position.
            self.gui.add_batch_files(['C:/v/b.mp4'])
            self.gui.batch_items[1]['settings']['quality_mode'] = 'cq'
            self.gui.batch_items[1]['settings']['quality'] = 26

            # Selecting B must restore B's OWN quality (26), not a value
            # derived from A's stale slider position (18).
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()

        self.assertEqual(self.gui.quality_var.get(), 26)

    def test_selecting_queue_item_restores_quality_despite_differing_gpu_range(self):
        """Same corruption as above, but triggered by a *different* GPU
        setting rather than a stale drag: item A (GPU off, CRF range 28-17)
        is loaded, then item B (GPU on, CQ range 30-15, quality=20 saved) is
        selected. Since quality_mode doesn't change (both 'cq'),
        _apply_quality_mode used to fall into _apply_quality_range's
        knob-preserving remap, which read the slider's *stale* CRF bounds
        instead of B's own CQ bounds and fractionally distorted B's saved 20
        into a different value (~19)."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])  # auto-loads A
            self.gui.quality_mode_var.set('Constant Quality')
            self.gui.gpu_accel_var.set(False)
            self.gui.quality_slider.set(22)
            self.gui.batch_items[0]['settings'] = self.gui._current_settings_dict()

            self.gui.add_batch_files(['C:/v/b.mp4'])  # queued, not auto-loaded
            self.gui.batch_items[1]['settings']['quality_mode'] = 'cq'
            self.gui.batch_items[1]['settings']['gpu_accel'] = True
            self.gui.batch_items[1]['settings']['quality'] = 20

            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()

        self.assertEqual(self.gui.quality_var.get(), 20)

    def test_gamma_change_writes_back_to_selected_queue_item(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
            self.gui.gamma_var.set(1.9)
            self.gui.on_gamma_change()
        self.assertAlmostEqual(self.gui.batch_items[0]['settings']['gamma'], 1.9)

    def test_output_path_edit_writes_back_to_selected_queue_item(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
            self.gui.output_path_var.set('C:/custom/a_custom.mkv')
            self.gui._on_output_path_change()
        self.assertEqual(self.gui.batch_items[0]['output'], 'C:/custom/a_custom.mkv')

    def test_gpu_toggle_writes_back_to_selected_queue_item(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        self.gui.gpu_accel_var.set(True)
        with patch.object(conversion_manager, 'is_gpu_acceleration_available', return_value=True):
            self.gui.check_gpu_acceleration()
        self.assertTrue(self.gui.batch_items[0]['settings']['gpu_accel'])

    def test_quality_mode_change_writes_back_internal_form(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        self.gui.quality_mode_var.set('Target Bitrate')
        self.gui._on_quality_mode_selected()
        self.assertEqual(self.gui.batch_items[0]['settings']['quality_mode'], 'bitrate')

    def test_quality_slider_drag_writes_back(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        self.gui._on_quality_change('19')
        self.assertEqual(self.gui.batch_items[0]['settings']['quality'], 19)

    def test_batch_run_uses_each_items_own_gamma(self):
        """Two queued files with different gammas must each convert with
        their own value, not whichever was showing last."""
        with patch('src.gui.get_video_properties', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'):
            self.gui.add_batch_files(['C:/v/a.mp4'])  # auto-loads A
            self.gui.gamma_var.set(1.2)
            self.gui.on_gamma_change()  # writes back onto A

            self.gui.add_batch_files(['C:/v/b.mp4'])  # does NOT auto-load (A is loaded)
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()  # actually loads B
            self.gui.gamma_var.set(0.7)
            self.gui.on_gamma_change()  # writes back onto B, not A

        with patch('src.gui.get_video_properties', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'), \
             patch('src.batch.conversion_manager') as mock_cm, \
             patch('src.batch.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=False):
            self.gui.start_batch()
            first_gamma = mock_cm.start_conversion.call_args.args[2]
            self.gui.batch_items[0]['status'] = 'Done'
            self.gui._current_batch_item = None
            self.gui._start_next_batch_item()
            second_gamma = mock_cm.start_conversion.call_args.args[2]

        self.assertAlmostEqual(first_gamma, 1.2)
        self.assertAlmostEqual(second_gamma, 0.7)

    def test_batch_run_uses_edited_output_path_for_selected_item(self):
        """Editing the Output File box for a queued item must not be
        silently discarded when the batch actually runs."""
        with patch('src.gui.get_video_properties', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4'])
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()
            self.gui.output_path_var.set('C:/custom/b_custom.mkv')
            self.gui._on_output_path_change()

        self.assertEqual(self.gui.batch_items[1]['output'], 'C:/custom/b_custom.mkv')

        with patch('src.gui.get_video_properties', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'), \
             patch('src.batch.conversion_manager') as mock_cm, \
             patch('src.batch.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=False):
            self.gui.batch_items[0]['status'] = 'Done'  # skip straight to item 2
            self.gui._start_next_batch_item()
            output_path = mock_cm.start_conversion.call_args.args[1]

        self.assertEqual(output_path, os.path.normpath('C:/custom/b_custom.mkv'))

    def test_copied_gpu_only_tonemapper_falls_back_when_target_lacks_gpu(self):
        """A settings dict carrying a GPU-only tonemapper (e.g. via a future
        Apply-to-All copy) must not reach ffmpeg for an item that ends up
        loading with GPU accel off -- _apply_tonemap_choices' existing
        fallback-to-Mobius logic must still run on restore."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        self.gui.batch_items[0]['settings']['tonemapper'] = 'BT.2390'
        self.gui.batch_items[0]['settings']['gpu_accel'] = False

        with patch.object(self.gui, 'update_frame_preview'):
            self.gui._restore_settings_dict(self.gui.batch_items[0]['settings'])

        self.assertEqual(self.gui.tonemap_var.get(), 'Mobius')

    def test_restore_settings_dict_does_not_leak_writeback_onto_a_different_item(self):
        """_restore_settings_dict must guard itself against its own internal
        slider moves triggering a premature write-back, rather than relying
        on its only production caller (_load_input_file) to wrap it. Without
        a self-guard, calling it directly to restore item B's settings while
        input_path_var still points at item A lets the intermediate
        _apply_quality_mode() slider move fire _write_back_current_settings,
        which stamps B's (still mid-restore) values onto item A instead."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4'])  # a.mp4 auto-loads
        a_settings_before = dict(self.gui.batch_items[0]['settings'])

        b_settings = dict(self.gui.batch_items[1]['settings'])
        b_settings['quality_mode'] = 'cq'
        b_settings['gpu_accel'] = True
        b_settings['quality'] = 20

        self.gui._restore_settings_dict(b_settings)  # called directly, not via _load_input_file

        self.assertEqual(self.gui.batch_items[0]['settings'], a_settings_before)

    def test_apply_to_all_button_exists(self):
        self.assertIsInstance(self.gui.apply_settings_button, ttk.Button)

    def test_apply_to_all_copies_current_settings_to_every_item(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4', 'C:/v/c.mp4'])
            self.gui.gamma_var.set(1.8)
            self.gui.on_gamma_change()  # writes back onto item 0 (the loaded one)

        self.gui.apply_settings_to_all_batch_items()

        for item in self.gui.batch_items:
            self.assertAlmostEqual(item['settings']['gamma'], 1.8)

    def test_apply_to_all_gives_each_item_an_independent_dict(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4'])
        self.gui.apply_settings_to_all_batch_items()

        self.gui.batch_items[0]['settings']['gamma'] = 99.0
        self.assertNotEqual(self.gui.batch_items[1]['settings']['gamma'], 99.0)

    def test_selecting_queue_item_refreshes_markers_to_match_restored_settings(self):
        """Bug #1: the listbox '*' markers must be recomputed against the
        settings that were just restored into the panel by selecting a queue
        item, not left over from whatever the panel showed before the
        selection."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])  # auto-loads A
            self.gui.gamma_var.set(2.4)
            self.gui.on_gamma_change()  # A's stored gamma == live (2.4) -> unmarked

            self.gui.add_batch_files(['C:/v/b.mp4'])  # queued only, not auto-loaded
            self.gui.batch_items[1]['settings']['gamma'] = 0.5  # now differs from live
            self.gui._refresh_batch_list()
            # Sanity: before selecting B, B is marked (differs) and A is not.
            self.assertIn('*', self.gui.batch_listbox.get(1))
            self.assertNotIn('*', self.gui.batch_listbox.get(0))

            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()  # restores B's gamma (0.5) into the panel

        # The panel now shows B's own settings (gamma 0.5): B should be
        # unmarked (matches live) and A -- whose stored gamma is still 2.4 --
        # should now be marked, since it differs from what's on screen.
        self.assertNotIn('*', self.gui.batch_listbox.get(1))
        self.assertIn('*', self.gui.batch_listbox.get(0))

    def test_marker_ignores_inactive_mode_leftover_field(self):
        """Two items in Target Bitrate mode with the identical operative
        bitrate must not be marked '*' just because their leftover, INACTIVE
        Constant-Quality 'quality' field differs -- both would produce
        byte-identical ffmpeg commands."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])  # auto-loads A
            self.gui.quality_mode_var.set('Target Bitrate')
            self.gui._on_quality_change('5000')  # marks customized, writes back onto A

            self.gui.add_batch_files(['C:/v/b.mp4'])  # queued only, not auto-loaded

        # Give B the SAME Target Bitrate settings as the live panel, but a
        # different inactive CQ 'quality' left over from before it switched
        # modes.
        current_live = self.gui._current_settings_dict()
        self.gui.batch_items[1]['settings'] = dict(current_live)
        self.gui.batch_items[1]['settings']['quality'] = current_live['quality'] + 5

        self.gui._refresh_batch_list()

        self.assertNotIn('*', self.gui.batch_listbox.get(1))

    def test_refresh_batch_list_tolerates_a_destroyed_listbox(self):
        """A debounced refresh (see _schedule_batch_list_refresh) can still
        be pending when the window is torn down -- it must not raise once
        the underlying Tk listbox no longer exists."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        self.gui.batch_listbox.destroy()
        self.gui._refresh_batch_list()  # must not raise

    @staticmethod
    def _props_for(bit_rate):
        return {
            'width': 1920, 'height': 1080, 'frame_rate': 24.0,
            'codec_name': 'hevc', 'audio_codec': 'aac',
            'color_primaries': 'bt2020', 'color_transfer': 'smpte2084',
            'bit_depth': 10, 'bit_rate': bit_rate,
        }

    def test_target_bitrate_reseeds_per_file_until_bitrate_is_customized(self):
        """Bug #2: Target Bitrate's 50%-of-source reseed must fire per file
        (each file gets its own source's 50%, not a stale value carried over
        from whichever file was probed last) -- UNTIL the user has
        deliberately dragged that specific file's bitrate slider, at which
        point their choice must be remembered for that file, surviving
        reselection even after visiting other files in between."""
        props_by_path = {
            'C:/v/a.mp4': self._props_for(4_000_000),   # 4,000 kbps -> 2,000 kbps seed
            'C:/v/b.mp4': self._props_for(10_000_000),  # 10,000 kbps -> 5,000 kbps seed
        }

        def fake_probe(path):
            return props_by_path.get(path)

        with patch('src.gui.get_video_properties', side_effect=fake_probe), \
             patch('src.gui.get_maxcll', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4'])  # auto-loads A
            self.gui.quality_mode_var.set('Target Bitrate')
            self.gui._on_quality_mode_selected()  # writes mode+reseed back onto A

            self.assertEqual(self.gui.bitrate_var.get(), 2000)  # A's own 50%

            # B was seeded in Constant Quality mode at add time; force it into
            # Target Bitrate too so selecting it below exercises the reseed
            # path (mirrors how test_copied_gpu_only_tonemapper_... directly
            # edits a stored settings dict rather than driving the UI for it).
            self.gui.batch_items[1]['settings']['quality_mode'] = 'bitrate'

            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()

            self.assertEqual(self.gui.bitrate_var.get(), 5000)  # B's OWN 50%, not A's

            # Deliberately customize B's bitrate.
            self.gui._on_quality_change('3500')
            self.assertEqual(self.gui.bitrate_var.get(), 3500)

            # Visit A (a different file) and back to B.
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(0)
            self.gui.on_batch_item_select()
            self.assertEqual(self.gui.bitrate_var.get(), 2000)  # A still reseeds fresh

            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()

        self.assertEqual(self.gui.bitrate_var.get(), 3500)  # B's customization survives

    def test_apply_to_all_propagates_bitrate_as_fraction_of_each_files_own_source(self):
        """Apply to All must scale a customized Target Bitrate to each file's
        own source, not copy the literal kbps chosen on the file it was set
        on. Covers both directions: a file with a lower source than the one
        Apply to All was clicked from, and one with a higher source."""
        props_by_path = {
            'C:/v/a.mp4': self._props_for(8_000_000),   # 8,000 kbps source
            'C:/v/b.mp4': self._props_for(4_000_000),   # 4,000 kbps source (lower)
            'C:/v/c.mp4': self._props_for(20_000_000),  # 20,000 kbps source (higher)
        }

        def fake_probe(path):
            return props_by_path.get(path)

        with patch('src.gui.get_video_properties', side_effect=fake_probe), \
             patch('src.gui.get_maxcll', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4', 'C:/v/c.mp4'])  # auto-loads A

            self.gui.quality_mode_var.set('Target Bitrate')
            self.gui._on_quality_mode_selected()
            self.gui._on_quality_change('8000')  # A's own ceiling: 100% of its source
            self.assertEqual(self.gui.bitrate_var.get(), 8000)

            self.gui.apply_settings_to_all_batch_items()

            # B: lower source (4,000 kbps) -> its OWN 100%, not A's literal 8,000.
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()
            self.assertEqual(self.gui.bitrate_var.get(), 4000)

            # C: higher source (20,000 kbps) -> its OWN 100%, not A's literal 8,000.
            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(2)
            self.gui.on_batch_item_select()
            self.assertEqual(self.gui.bitrate_var.get(), 20000)

    def test_apply_to_all_propagates_a_partial_customized_bitrate_fraction(self):
        """A non-maximum customized bitrate (e.g. ~65% of source) must also
        scale proportionally via Apply to All, not just the 100% case."""
        props_by_path = {
            'C:/v/a.mp4': self._props_for(10_000_000),  # 10,000 kbps source
            'C:/v/b.mp4': self._props_for(20_000_000),  # 20,000 kbps source
        }

        def fake_probe(path):
            return props_by_path.get(path)

        with patch('src.gui.get_video_properties', side_effect=fake_probe), \
             patch('src.gui.get_maxcll', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4'])  # auto-loads A

            self.gui.quality_mode_var.set('Target Bitrate')
            self.gui._on_quality_mode_selected()
            self.gui._on_quality_change('6500')  # 65% of A's 10,000 kbps source
            self.assertEqual(self.gui.bitrate_var.get(), 6500)

            self.gui.apply_settings_to_all_batch_items()

            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()

            # 65% of B's own 20,000 kbps source, rounded to the nearest 500 kbps
            # step -- not A's literal 6,500.
            self.assertEqual(self.gui.bitrate_var.get(), 13000)

    def test_apply_to_all_leaves_uncustomized_items_free_to_reseed_their_own_bitrate(self):
        """If the currently displayed file's Target Bitrate is still on its
        auto-reseeded value (never deliberately customized), Apply to All
        must not stamp a stale bitrate/fraction onto other items -- each
        item stays free to reseed to its own 50% when it's next loaded.

        A's source (3,500 kbps) is deliberately NOT a clean multiple of the
        500 kbps rounding step, so its reseed fraction (0.5714) differs from
        a genuine 0.5 -- this is what actually exercises the un-customized
        restore branch. A clean-multiple source would make the customized
        and un-customized branches numerically indistinguishable and pass
        even if the un-customized branch were broken."""
        props_by_path = {
            'C:/v/a.mp4': self._props_for(3_500_000),   # odd multiple -> 2,000 kbps seed (57.1%)
            'C:/v/b.mp4': self._props_for(10_000_000),  # 10,000 kbps -> 5,000 kbps seed (50%)
        }

        def fake_probe(path):
            return props_by_path.get(path)

        with patch('src.gui.get_video_properties', side_effect=fake_probe), \
             patch('src.gui.get_maxcll', return_value=None), \
             patch.object(self.gui, 'update_frame_preview'), \
             patch.object(self.gui, 'highlight_frame_button'):
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mp4'])  # auto-loads A
            self.gui.quality_mode_var.set('Target Bitrate')
            self.gui._on_quality_mode_selected()  # A reseeds to 2,000, never customized

            self.assertFalse(self.gui._bitrate_customized_for_current_item)
            self.assertFalse(self.gui.batch_items[0]['settings']['bitrate_customized'])

            self.gui.apply_settings_to_all_batch_items()

            self.gui.batch_listbox.selection_clear(0, tk.END)
            self.gui.batch_listbox.selection_set(1)
            self.gui.on_batch_item_select()

            self.assertEqual(self.gui.bitrate_var.get(), 5000)  # B's own 50%, not a 5,500 skew from A's

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

    def test_enter_review_ui_shows_cancel_button_and_updates_hint(self):
        item = {'input': 'a.mp4', 'output': 'a_sdr.mp4', 'status': 'Pending'}
        self.gui._batch_conflict_groups = [[item]]
        self.gui._enter_batch_conflict_review_ui()
        self.assertNotEqual(self.gui.batch_review_cancel_button.grid_info(), {})
        self.assertIn('output conflicts found', self.gui.batch_hint_label.cget('text'))

    def test_exit_review_ui_restores_hint_and_hides_button(self):
        self.gui.batch_review_cancel_button.grid()
        self.gui._exit_batch_conflict_review_ui()
        self.assertEqual(self.gui.batch_review_cancel_button.grid_info(), {})
        self.assertEqual(
            self.gui.batch_hint_label.cget('text'),
            "Add or drop multiple files to convert them in sequence.")

    def test_cancel_review_is_noop_when_not_reviewing(self):
        self.gui._batch_conflict_groups = None
        self.gui._cancel_batch_conflict_review()
        self.assertEqual(self.gui.batch_review_cancel_button.grid_info(), {})

    def test_cancel_review_clears_state_and_restores_ui(self):
        item = {'input': 'a.mp4', 'output': 'a_sdr.mp4', 'status': 'Pending'}
        self.gui._batch_conflict_groups = [[item]]
        self.gui._batch_conflict_selection = {}
        self.gui._enter_batch_conflict_review_ui()
        self.gui._cancel_batch_conflict_review()
        self.assertIsNone(self.gui._batch_conflict_groups)
        self.assertEqual(self.gui.batch_review_cancel_button.grid_info(), {})

    def test_add_batch_files_cancels_an_in_progress_review(self):
        item = {'input': 'a.mp4', 'output': 'a_sdr.mp4', 'status': 'Pending'}
        self.gui._batch_conflict_groups = [[item]]
        self.gui._enter_batch_conflict_review_ui()
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/z.mp4'])
        self.assertIsNone(self.gui._batch_conflict_groups)
        self.assertEqual(self.gui.batch_review_cancel_button.grid_info(), {})

    def test_clear_batch_queue_cancels_an_in_progress_review(self):
        item = {'input': 'a.mp4', 'output': 'a_sdr.mp4', 'status': 'Pending'}
        self.gui.batch_items = [item]
        self.gui._batch_conflict_groups = [[item]]
        self.gui._enter_batch_conflict_review_ui()
        self.gui.clear_batch_queue()
        self.assertIsNone(self.gui._batch_conflict_groups)

    def test_remove_selected_batch_item_cancels_an_in_progress_review(self):
        item = {'input': 'a.mp4', 'output': 'a_sdr.mp4', 'status': 'Pending'}
        self.gui.batch_items = [item]
        self.gui._refresh_batch_list()  # populate the row so it can actually be selected below
        self.gui.batch_listbox.selection_clear(0, tk.END)
        self.gui.batch_listbox.selection_set(0)
        self.gui._batch_conflict_groups = [[item]]
        self.gui._enter_batch_conflict_review_ui()
        self.gui.remove_selected_batch_item()
        self.assertIsNone(self.gui._batch_conflict_groups)
        # The selected row must still have actually been removed -- cancelling
        # the review rebuilds the listbox, which must not happen before the
        # current selection is read, or nothing would ever get deleted while
        # a review is in progress.
        self.assertEqual(self.gui.batch_items, [])


class TestStateAndLayout(_GuiTestBase):

    def test_disable_ui_sets_widgets_disabled(self):
        conversion_manager.disable_ui(self.gui.interactable_elements)
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
        mock_cm.start_conversion.assert_called_once()
        self.assertFalse(self.gui.drop_target_registered)  # unregistered
        self.assertNotEqual(self.gui.cancel_button.grid_info(), {})  # shown

    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    @patch('src.gui.conversion_manager')
    def test_convert_video_leaves_ui_usable_when_start_conversion_declines(self, mock_cm, *_):
        """start_conversion returning False means a guard rejected the file
        before ever launching ffmpeg (e.g. undetermined duration). Drag-and-
        drop must stay registered and Cancel must stay hidden -- otherwise
        the only way to recover is restarting the app."""
        mock_cm.start_conversion.return_value = False
        self.gui.input_path_var.set('in.mkv')
        self.gui.output_path_var.set('out.mkv')
        self.gui.convert_video()
        mock_cm.start_conversion.assert_called_once()
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


class TestBuildInfoText(unittest.TestCase):
    """_build_info_text formats properties into a human-readable one-liner."""

    def test_hdr_bt2020_smpte2084_tagged_hdr(self):
        props = {'width': 3840, 'height': 2160, 'frame_rate': 23.976,
                 'codec_name': 'hevc', 'audio_codec': 'truehd',
                 'color_primaries': 'bt2020', 'color_transfer': 'smpte2084'}
        text = HDRConverterGUI._build_info_text(props)
        self.assertIn('HDR', text)

    def test_hlg_arib_std_b67_tagged_hdr(self):
        props = {'width': 1920, 'height': 1080, 'frame_rate': 50.0,
                 'codec_name': 'hevc', 'audio_codec': 'aac',
                 'color_primaries': 'bt2020', 'color_transfer': 'arib-std-b67'}
        text = HDRConverterGUI._build_info_text(props)
        self.assertIn('HDR', text)

    def test_bt709_tagged_sdr(self):
        props = {'width': 1920, 'height': 1080, 'frame_rate': 30.0,
                 'codec_name': 'h264', 'audio_codec': 'aac',
                 'color_primaries': 'bt709', 'color_transfer': 'bt709'}
        text = HDRConverterGUI._build_info_text(props)
        self.assertIn('SDR', text)

    def test_no_color_info_tagged_sdr(self):
        props = {'width': 1920, 'height': 1080, 'frame_rate': 30.0,
                 'codec_name': 'h264', 'audio_codec': 'aac',
                 'color_primaries': '', 'color_transfer': ''}
        text = HDRConverterGUI._build_info_text(props)
        self.assertIn('SDR', text)

    def test_includes_resolution_fps_codec_audio(self):
        props = {'width': 1920, 'height': 1080, 'frame_rate': 29.970,
                 'codec_name': 'h264', 'audio_codec': 'aac',
                 'color_primaries': '', 'color_transfer': ''}
        text = HDRConverterGUI._build_info_text(props)
        self.assertIn('1920', text)
        self.assertIn('1080', text)
        self.assertIn('H264', text)
        self.assertIn('AAC', text)


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
        for w in _probe_root.winfo_children():
            w.destroy()
        cls._class_gui = HDRConverterGUI(_probe_root, licensed=False)

    def test_gpu_checkbox_enabled_when_unlicensed(self):
        # GPU acceleration is free; the checkbox must stay enabled without a license.
        self.assertFalse(self.gui.gpu_accel_checkbutton.instate(['disabled']))

    def test_disables_quality_slider(self):
        self.assertTrue(self.gui.quality_slider.instate(['disabled']))

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
            self.gui.quality_slider, self.gui.quality_mode_combobox,
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
        for w in _probe_root.winfo_children():
            w.destroy()
        cls._class_gui = HDRConverterGUI(_probe_root, licensed=True)

    def test_enables_gpu_checkbox(self):
        self.assertFalse(self.gui.gpu_accel_checkbutton.instate(['disabled']))

    def test_enables_quality_slider(self):
        self.assertFalse(self.gui.quality_slider.instate(['disabled']))

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
            self.gui.gpu_accel_checkbutton, self.gui.quality_slider,
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

    # ── Per-queue-item bit depth choice ──────────────────────────────────────

    @staticmethod
    def _queued(path, **settings_overrides):
        item = {'input': path, 'output': f"{path.rsplit('.', 1)[0]}_sdr.mkv",
                'format': 'MKV', 'status': 'Pending', 'settings': {}}
        item['settings'].update(settings_overrides)
        return item

    def test_toggle_stores_choice_on_matching_queue_item(self):
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv')]
        gui.input_path_var.set('C:/a.mkv')
        gui._source_bit_depth = 12
        gui._update_bit_depth_choice()

        gui.bit_depth_12_radio.invoke()
        self.assertEqual(gui.batch_items[0]['settings'].get('bit_depth_choice'), '12-bit')

        gui.bit_depth_10_radio.invoke()
        self.assertEqual(gui.batch_items[0]['settings'].get('bit_depth_choice'), '10-bit')

    def test_update_bit_depth_choice_restores_stored_queue_choice(self):
        """Re-loading a queued file (batch runs, queue clicks) must restore
        that item's stored 10/12-bit choice instead of resetting to 10-bit."""
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv', bit_depth_choice='12-bit')]
        gui.input_path_var.set('C:/a.mkv')
        gui._source_bit_depth = 12
        gui._update_bit_depth_choice()
        self.assertEqual(gui.bit_depth_var.get(), '12-bit')

    def test_batch_list_marks_items_whose_settings_differ_from_live_panel(self):
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv', bit_depth_choice='12-bit'),
                           self._queued('C:/b.mkv')]
        gui.batch_items[1]['settings'] = gui._current_settings_dict()
        gui._refresh_batch_list()
        self.assertIn('*', gui.batch_listbox.get(0))
        self.assertNotIn('*', gui.batch_listbox.get(1))

    def _run_one_item_batch(self, gui):
        """Drive start_batch with probing/preview mocked; return start_conversion kwargs."""
        with patch('src.gui.get_video_properties', return_value=self._twelve_bit_props()), \
             patch('src.gui.get_maxcll', return_value=1000.0), \
             patch.object(gui, 'update_frame_preview'), \
             patch.object(gui, 'highlight_frame_button'), \
             patch('src.batch.conversion_manager') as mock_cm, \
             patch('src.batch.os.path.isfile', return_value=True), \
             patch('src.batch.os.path.exists', return_value=False):
            gui.start_batch()
        return mock_cm.start_conversion.call_args.kwargs

    def test_batch_honors_stored_twelve_bit_choice_after_reload(self):
        """The batch runner reloads each item (which resets the live toggle);
        the item's stored choice must survive that reload and reach ffmpeg."""
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv', bit_depth_choice='12-bit')]
        kwargs = self._run_one_item_batch(gui)
        self.assertEqual(kwargs['bit_depth'], 12)

    def test_batch_defaults_to_ten_bit_without_stored_choice(self):
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv')]
        kwargs = self._run_one_item_batch(gui)
        self.assertEqual(kwargs['bit_depth'], 10)


@unittest.skipUnless(_TK_OK, _SKIP)
class TestDropToQueue(unittest.TestCase):
    """Licensed single-file drops route through the batch queue (so dropping
    onto a populated queue adds to it instead of bypassing it); unlicensed
    drops keep the plain load-only behavior since batch is Pro."""

    def setUp(self) -> None:
        self._load_patch = patch('src.gui.load_settings', return_value=dict(DEFAULTS))
        self._save_patch = patch('src.gui.save_settings')
        self._load_patch.start()
        self._save_patch.start()
        for w in _probe_root.winfo_children():
            w.destroy()

    def tearDown(self) -> None:
        self._load_patch.stop()
        self._save_patch.stop()

    def _make_gui(self, licensed: bool) -> HDRConverterGUI:
        return HDRConverterGUI(_probe_root, licensed=licensed)

    @staticmethod
    def _queued(path):
        return {'input': path, 'output': f"{path.rsplit('.', 1)[0]}_sdr.mkv",
                'format': 'MKV', 'status': 'Pending'}

    def test_single_drop_licensed_adds_to_queue_and_previews(self):
        gui = self._make_gui(licensed=True)
        with patch.object(gui, '_load_input_file') as mock_load:
            gui.handle_file_drop(MagicMock(data='{C:/videos/new.mkv}'))
        self.assertEqual([it['input'] for it in gui.batch_items], ['C:/videos/new.mkv'])
        mock_load.assert_any_call('C:/videos/new.mkv')

    def test_single_drop_licensed_appends_to_existing_queue(self):
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv')]
        gui.input_path_var.set('C:/a.mkv')
        with patch.object(gui, '_load_input_file') as mock_load:
            gui.handle_file_drop(MagicMock(data='{C:/b.mkv}'))
        self.assertEqual([it['input'] for it in gui.batch_items],
                         ['C:/a.mkv', 'C:/b.mkv'])
        mock_load.assert_called_once_with('C:/b.mkv')

    def test_single_drop_unlicensed_only_loads(self):
        gui = self._make_gui(licensed=False)
        with patch.object(gui, '_load_input_file') as mock_load:
            gui.handle_file_drop(MagicMock(data='{C:/a.mkv}'))
        self.assertEqual(gui.batch_items, [])
        mock_load.assert_called_once_with('C:/a.mkv')

    def test_duplicate_single_drop_does_not_duplicate_queue_entry(self):
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv')]
        gui.input_path_var.set('C:/other.mkv')
        with patch.object(gui, '_load_input_file') as mock_load:
            gui.handle_file_drop(MagicMock(data='{C:/a.mkv}'))
        self.assertEqual([it['input'] for it in gui.batch_items], ['C:/a.mkv'])
        mock_load.assert_called_once_with('C:/a.mkv')  # still previews it

    def test_add_batch_files_skips_already_queued_paths(self):
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv')]
        gui.input_path_var.set('C:/a.mkv')
        gui.add_batch_files(['C:/a.mkv', 'C:/b.mkv'])
        self.assertEqual([it['input'] for it in gui.batch_items],
                         ['C:/a.mkv', 'C:/b.mkv'])

    def test_add_batch_files_skips_case_variant_of_already_queued_path(self):
        # NTFS is case-insensitive: 'C:/A.mkv' and 'C:/a.mkv' are the same
        # file on Windows, so queuing both must not create two queue entries
        # whose outputs could silently overwrite each other.
        gui = self._make_gui(licensed=True)
        gui.batch_items = [self._queued('C:/a.mkv')]
        gui.input_path_var.set('C:/a.mkv')
        gui.add_batch_files(['C:/A.mkv', 'C:/b.mkv'])
        self.assertEqual([it['input'] for it in gui.batch_items],
                         ['C:/a.mkv', 'C:/b.mkv'])


@unittest.skipUnless(_TK_OK, _SKIP)
class TestLicenseDialog(unittest.TestCase):
    """Tests for the _LicenseDialog Toplevel."""

    def setUp(self) -> None:
        for w in _probe_root.winfo_children():
            w.destroy()

    def tearDown(self) -> None:
        for w in _probe_root.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    def _make_dialog(self):  # type: ignore[return]
        from src.gui import _LicenseDialog  # type: ignore[attr-defined]
        dlg = _LicenseDialog(_probe_root)
        dlg.withdraw()
        return dlg

    def _submit_sync(self, dlg) -> None:
        """Drive _submit() with the worker thread replaced by a synchronous
        stand-in, then pump the Tk event queue so the self.after(0, ...)
        completion callback (still real Tk) actually runs."""
        with patch('src.dialogs.threading.Thread', _SyncThread):
            dlg._submit()
        dlg.update()

    def test_initial_activated_is_false(self):
        dlg = self._make_dialog()
        self.assertFalse(dlg.activated)
        dlg.destroy()

    def test_submit_empty_key_shows_prompt(self):
        dlg = self._make_dialog()
        dlg._key_var.set('')
        dlg._submit()
        self.assertIn('enter', dlg._status_var.get().lower())
        self.assertFalse(dlg.activated)
        dlg.destroy()

    def test_on_close_sets_activated_false(self):
        dlg = self._make_dialog()
        dlg._on_close()
        self.assertFalse(dlg._activated)

    def test_submit_valid_key_sets_activated_true(self):
        dlg = self._make_dialog()
        dlg._key_var.set('VALID-KEY-1234')
        with patch('src.dialogs.activate_license'):
            self._submit_sync(dlg)
        self.assertTrue(dlg._activated)

    def test_submit_invalid_key_shows_error(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('BAD-KEY')
        with patch('src.dialogs.activate_license', side_effect=_gm.InvalidKeyError('bad')):
            self._submit_sync(dlg)
        self.assertIn('invalid', dlg._status_var.get().lower())
        self.assertFalse(dlg._activated)
        dlg.destroy()

    def test_submit_device_limit_shows_error(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('LIMIT-KEY')
        with patch('src.dialogs.activate_license', side_effect=_gm.DeviceLimitError('limit')):
            self._submit_sync(dlg)
        self.assertIn('limit', dlg._status_var.get().lower())
        dlg.destroy()

    def test_submit_network_error_shows_error(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('NET-KEY')
        with patch('src.dialogs.activate_license', side_effect=_gm.NetworkError('offline')):
            self._submit_sync(dlg)
        self.assertIn('connection', dlg._status_var.get().lower())
        dlg.destroy()

    def test_submit_generic_license_error_shows_message(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('ERR-KEY')
        with patch('src.dialogs.activate_license', side_effect=_gm.LicenseError('custom message')):
            self._submit_sync(dlg)
        self.assertIn('custom message', dlg._status_var.get())
        dlg.destroy()

    def test_submit_runs_activate_license_off_the_main_thread(self):
        """activate_license is a blocking HTTP round-trip -- running it
        directly on the Tk main thread would freeze the whole UI for the
        duration of the request/timeout. It must run on a worker thread."""
        dlg = self._make_dialog()
        dlg._key_var.set('VALID-KEY-1234')
        seen_thread: list = []
        created_threads: list = []
        real_thread_cls = threading.Thread

        def capturing_thread(*args, **kwargs):
            t = real_thread_cls(*args, **kwargs)
            created_threads.append(t)
            return t

        def fake_activate(key):
            seen_thread.append(threading.current_thread())

        # Calling real Tk .after() from a background thread needs an active
        # mainloop() to be thread-safe (it isn't running one here) -- swap it
        # for a plain mock so this test proves the threading behavior without
        # depending on that.
        dlg.after = MagicMock()
        with patch('src.dialogs.activate_license', side_effect=fake_activate), \
             patch('src.dialogs.threading.Thread', side_effect=capturing_thread):
            dlg._submit()
            self.assertEqual(len(created_threads), 1)
            created_threads[0].join(timeout=2.0)

        self.assertEqual(len(seen_thread), 1)
        self.assertIsNot(seen_thread[0], threading.main_thread())
        dlg.after.assert_called_once()
        dlg.destroy()

    def test_submit_disables_entry_and_button_while_validating(self):
        """Once the request is backgrounded, a double-click/double-Enter must
        not fire a second concurrent activate_license call."""
        dlg = self._make_dialog()
        dlg._key_var.set('VALID-KEY-1234')
        with patch('src.dialogs.threading.Thread'):  # never actually runs the worker
            dlg._submit()
        self.assertEqual(str(dlg._entry.cget('state')), 'disabled')
        self.assertEqual(str(dlg._activate_btn.cget('state')), 'disabled')
        dlg.destroy()

    def test_submit_reenables_entry_and_button_on_error(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('BAD-KEY')
        with patch('src.dialogs.activate_license', side_effect=_gm.InvalidKeyError('bad')):
            self._submit_sync(dlg)
        self.assertEqual(str(dlg._entry.cget('state')), 'normal')
        self.assertEqual(str(dlg._activate_btn.cget('state')), 'normal')
        dlg.destroy()

    def test_manage_activations_link_opens_lemon_squeezy(self):
        """_open_manage_url must open the LS orders page in the default browser."""
        dlg = self._make_dialog()
        with patch('src.dialogs.webbrowser') as mock_wb:
            dlg._open_manage_url()
        mock_wb.open.assert_called_once_with('https://app.lemonsqueezy.com/my-orders')
        dlg.destroy()

    def test_manage_activations_link_widget_exists(self):
        """The dialog must contain a label whose text mentions 'machine slot'."""
        dlg = self._make_dialog()
        texts = [w.cget('text') for w in dlg.winfo_children()
                 if isinstance(w, tk.Label)]
        self.assertTrue(
            any('machine slot' in t.lower() for t in texts),
            f"Expected a label mentioning 'machine slot'; found: {texts}",
        )
        dlg.destroy()


@unittest.skipUnless(_TK_OK, _SKIP)
class TestUpdateDialog(unittest.TestCase):
    """Tests for the _UpdateDialog Toplevel's changelog link."""

    def setUp(self) -> None:
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


if __name__ == '__main__':
    unittest.main()
