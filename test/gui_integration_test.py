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
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import tkinter as tk
from tkinter import ttk
from tkinterdnd2 import TkinterDnD

from src.gui import HDRConverterGUI, DEFAULT_MIN_SIZE
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
        self.assertEqual(self.gui.filter_var.get(), 'Dynamic')
        self.assertEqual(self.gui.tonemap_var.get(), 'Mobius')
        self.assertFalse(self.gui.gpu_accel_var.get())
        self.assertTrue(self.gui.display_image_var.get())
        self.assertEqual(self.gui.progress_var.get(), 0)

    def test_filter_combobox_values_and_readonly(self):
        self.assertEqual(tuple(self.gui.filter_combobox.cget('values')),
                         ('Static', 'Dynamic'))
        self.assertEqual(str(self.gui.filter_combobox.cget('state')), 'readonly')

    def test_tonemap_combobox_matches_constant(self):
        self.assertEqual(tuple(self.gui.tonemap_combobox.cget('values')),
                         tuple(TONEMAP))
        self.assertEqual(str(self.gui.tonemap_combobox.cget('state')), 'readonly')

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
            self.gui.gpu_accel_checkbutton,
            self.gui.quality_slider, self.gui.format_combobox,
            self.gui.custom_time_entry, self.gui.custom_seek_button,
            self.gui.add_files_button, self.gui.clear_batch_button,
            self.gui.remove_batch_button,
        }
        self.assertEqual(set(self.gui.interactable_elements), expected)

    def test_drop_target_registered_on_start(self):
        self.assertTrue(self.gui.drop_target_registered)


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

    def test_add_batch_files_populates_listbox(self):
        with patch.object(self.gui, 'update_frame_preview'):  # don't spawn ffmpeg
            self.gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mkv'])
        self.assertEqual(self.gui.batch_listbox.size(), 2)
        self.assertIn('a.mp4', self.gui.batch_listbox.get(0))

    def test_clear_batch_empties_listbox(self):
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        self.gui.clear_batch_queue()
        self.assertEqual(self.gui.batch_listbox.size(), 0)
        self.assertEqual(self.gui.batch_items, [])

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

    def test_batch_skips_item_when_output_already_exists(self):
        """_start_next_batch_item must not silently overwrite an existing output
        file. When the output path already exists the item is marked Failed and
        start_conversion is never called."""
        with patch.object(self.gui, 'update_frame_preview'):
            self.gui.add_batch_files(['C:/v/a.mp4'])
        item = self.gui.batch_items[0]

        with patch('src.gui.os.path.isfile', return_value=True), \
             patch('src.gui.os.path.exists', return_value=True), \
             patch('src.gui.conversion_manager.start_conversion') as mock_conv, \
             patch.object(self.gui, '_load_input_file'), \
             patch.object(self.gui, '_finish_batch'):
            self.gui._start_next_batch_item()

        mock_conv.assert_not_called()
        self.assertIn(item['status'], ('Failed', 'Skipped'))

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


class TestStateAndLayout(_GuiTestBase):

    def test_disable_ui_sets_widgets_disabled(self):
        self.gui.disable_ui(self.gui.interactable_elements)
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

    @patch('src.gui.get_video_properties')
    @patch('src.gui.filedialog.askopenfilename')
    def test_info_label_shown_after_file_select(self, mock_dialog, mock_props):
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

    def test_disables_gpu_checkbox(self):
        self.assertTrue(self.gui.gpu_accel_checkbutton.instate(['disabled']))

    def test_disables_quality_slider(self):
        self.assertTrue(self.gui.quality_slider.instate(['disabled']))

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

    def test_shows_pro_banner(self):
        self.assertNotEqual(self.gui._pro_banner.grid_info(), {})

    def test_excludes_premium_from_interactable_elements(self):
        premium = [
            self.gui.gpu_accel_checkbutton, self.gui.quality_slider,
            self.gui.format_combobox, self.gui.custom_time_entry,
            self.gui.custom_seek_button, self.gui.add_files_button,
            self.gui.clear_batch_button, self.gui.remove_batch_button,
        ]
        for widget in premium:
            self.assertNotIn(widget, self.gui.interactable_elements,
                             msg=f'{widget} must not be in interactable_elements when unlicensed')

    def test_multifile_drop_blocked(self):
        event = MagicMock()
        event.data = '/file1.mp4 /file2.mkv'
        with patch('src.gui.messagebox.showinfo') as mock_info:
            self.gui.handle_file_drop(event)
        mock_info.assert_called_once()
        self.assertEqual(self.gui.batch_items, [])


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

    def test_hides_pro_banner(self):
        self.assertEqual(self.gui._pro_banner.grid_info(), {})

    def test_includes_premium_in_interactable_elements(self):
        premium = [
            self.gui.gpu_accel_checkbutton, self.gui.quality_slider,
            self.gui.format_combobox, self.gui.custom_time_entry,
            self.gui.custom_seek_button, self.gui.add_files_button,
            self.gui.clear_batch_button, self.gui.remove_batch_button,
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
        gui = self._make_gui(licensed=False)
        self.assertTrue(gui.gpu_accel_checkbutton.instate(['disabled']))
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
        with patch('src.gui.activate_license'):
            dlg._submit()
        self.assertTrue(dlg._activated)

    def test_submit_invalid_key_shows_error(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('BAD-KEY')
        with patch('src.gui.activate_license', side_effect=_gm.InvalidKeyError('bad')):
            dlg._submit()
        self.assertIn('invalid', dlg._status_var.get().lower())
        self.assertFalse(dlg._activated)
        dlg.destroy()

    def test_submit_device_limit_shows_error(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('LIMIT-KEY')
        with patch('src.gui.activate_license', side_effect=_gm.DeviceLimitError('limit')):
            dlg._submit()
        self.assertIn('limit', dlg._status_var.get().lower())
        dlg.destroy()

    def test_submit_network_error_shows_error(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('NET-KEY')
        with patch('src.gui.activate_license', side_effect=_gm.NetworkError('offline')):
            dlg._submit()
        self.assertIn('connection', dlg._status_var.get().lower())
        dlg.destroy()

    def test_submit_generic_license_error_shows_message(self):
        import src.gui as _gm
        dlg = self._make_dialog()
        dlg._key_var.set('ERR-KEY')
        with patch('src.gui.activate_license', side_effect=_gm.LicenseError('custom message')):
            dlg._submit()
        self.assertIn('custom message', dlg._status_var.get())
        dlg.destroy()

    def test_manage_activations_link_opens_lemon_squeezy(self):
        """_open_manage_url must open the LS orders page in the default browser."""
        dlg = self._make_dialog()
        with patch('src.gui.webbrowser') as mock_wb:
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


if __name__ == '__main__':
    unittest.main()
