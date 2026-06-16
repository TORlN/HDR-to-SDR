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


def _tk_available():
    try:
        r = TkinterDnD.Tk()
        r.withdraw()
        r.destroy()
        return True
    except Exception:
        return False


_TK_OK = _tk_available()
_SKIP = "no Tk display available (need a desktop session or xvfb)"


@unittest.skipUnless(_TK_OK, _SKIP)
class _GuiTestBase(unittest.TestCase):
    def setUp(self):
        self.root = TkinterDnD.Tk()
        self.root.withdraw()
        self.gui = HDRConverterGUI(self.root)

    def tearDown(self):
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except tk.TclError:
            pass


class TestConstruction(_GuiTestBase):

    def test_window_title_and_minsize(self):
        self.assertEqual(self.root.title(), "HDR to SDR Converter")
        self.assertEqual(tuple(self.root.minsize()), DEFAULT_MIN_SIZE)

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

    def test_five_numbered_frame_buttons(self):
        self.assertEqual(len(self.gui.frame_buttons), 5)
        self.assertEqual([b.cget('text') for b in self.gui.frame_buttons],
                         ['1', '2', '3', '4', '5'])

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
        }
        self.assertEqual(set(self.gui.interactable_elements), expected)

    def test_drop_target_registered_on_start(self):
        self.assertTrue(self.gui.drop_target_registered)


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
        self.assertEqual(tuple(self.root.minsize()), DEFAULT_MIN_SIZE)
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
        self.assertEqual(tuple(self.root.minsize()), DEFAULT_MIN_SIZE)
        self.assertEqual(self.gui.button_container.grid_info(), {})

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
        mock_cm.is_gpu_available.return_value = False
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


class TestDropTargetAndClose(_GuiTestBase):

    def test_unregister_then_register_round_trip(self):
        self.gui.unregister_drop_target()
        self.assertFalse(self.gui.drop_target_registered)
        self.gui.register_drop_target()
        self.assertTrue(self.gui.drop_target_registered)

    @patch('src.gui.conversion_manager')
    def test_on_close_destroys_when_idle(self, mock_cm):
        mock_cm.process = None
        self.gui.on_close()
        # Destroying the main root tears down the interpreter app, so any further
        # Tk call raises -- that exception is the proof the window was destroyed.
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()


if __name__ == '__main__':
    unittest.main()
