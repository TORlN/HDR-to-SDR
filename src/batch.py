"""Batch queue logic for HDRConverterGUI."""
from __future__ import annotations

import logging
import os
import re
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING
import tkinter as tk

from conversion import conversion_manager

if TYPE_CHECKING:
    from tkinter import ttk


class _BatchMixin:
    """Mixin that provides batch-queue methods for HDRConverterGUI.

    Attributes accessed via ``self`` are provided by HDRConverterGUI.__init__;
    they are declared below inside ``if TYPE_CHECKING:`` so static analysis
    tools can resolve them without creating runtime instance variables here.
    """

    if TYPE_CHECKING:
        batch_items: list[dict]  # type: ignore[type-arg]
        _current_batch_item: dict | None  # type: ignore[type-arg]
        batch_listbox: tk.Listbox
        input_path_var: tk.StringVar
        output_path_var: tk.StringVar
        format_var: tk.StringVar
        gamma_var: tk.DoubleVar
        gpu_accel_var: tk.BooleanVar
        tonemap_var: tk.StringVar
        quality_var: tk.IntVar
        quality_mode_var: tk.StringVar
        bitrate_var: tk.IntVar
        open_after_conversion_var: tk.BooleanVar
        progress_var: tk.DoubleVar
        interactable_elements: list  # type: ignore[type-arg]
        cancel_button: ttk.Button
        drop_target_registered: bool
        _licensed: bool

    _STATUS_ICONS: dict[str, str] = {
        'Pending': '•', 'Converting': '▶', 'Done': '✓', 'Failed': '✗',
    }

    @staticmethod
    def _parse_drop_paths(data: str) -> list[str]:
        """Split a tkdnd drop payload into individual file paths."""
        tokens = re.findall(r'\{[^}]*\}|\S+', data or '')
        return [t.strip('{}') for t in tokens if t.strip('{}')]

    # ── Queue management ───────────────────────────────────────────────────────

    def browse_batch_files(self) -> None:
        """Open a multi-select dialog and add the chosen files to the queue."""
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("All Video Files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
                ("All files", "*.*"),
            ]
        )
        if paths:
            self.add_batch_files(paths)

    def add_batch_files(self, paths: object) -> None:
        """Append video files to the batch queue, building each output path.

        Paths already queued are skipped: a duplicate entry would only fail at
        convert time anyway (its output already exists), and single-file drops
        route through here too, so re-dropping a file to preview it must not
        stack up copies."""
        queued = {it['input'] for it in self.batch_items}
        for path in paths:  # type: ignore[union-attr]
            if not path or path in queued:
                continue
            queued.add(path)
            fmt = self._format_for_input(path)  # type: ignore[attr-defined]
            base = os.path.splitext(path)[0]
            output_path = self._output_path_with_format(f"{base}_sdr", fmt)  # type: ignore[attr-defined]
            self.batch_items.append({
                'input': path, 'output': output_path, 'format': fmt, 'status': 'Pending',
                'settings': self._current_settings_dict(),  # type: ignore[attr-defined]
            })
        self._refresh_batch_list()
        if (self.batch_items and hasattr(self, 'input_path_var')
                and not self.input_path_var.get()):
            self._load_input_file(self.batch_items[0]['input'])  # type: ignore[attr-defined]

    def remove_selected_batch_item(self) -> None:
        """Remove the highlighted queue entries."""
        if not hasattr(self, 'batch_listbox'):
            return
        selected = sorted(self.batch_listbox.curselection(), reverse=True)
        removed_inputs = [self.batch_items[i]['input'] for i in selected]
        for index in selected:
            del self.batch_items[index]
        self._refresh_batch_list()
        self._resync_preview_after_queue_change(removed_inputs)

    def clear_batch_queue(self) -> None:
        """Empty the batch queue."""
        removed_inputs = [it['input'] for it in self.batch_items]
        self.batch_items = []
        self._refresh_batch_list()
        self._resync_preview_after_queue_change(removed_inputs)

    def apply_settings_to_all_batch_items(self) -> None:
        """Copy the currently-displayed settings onto every queued item.
        Safe across mixed sources: each item re-validates its own copy
        against its own file's metadata the next time it's loaded or
        converted (see _restore_settings_dict / _update_bit_depth_choice)."""
        current = self._current_settings_dict()  # type: ignore[attr-defined]
        for item in self.batch_items:
            item['settings'] = dict(current)
        self._refresh_batch_list()

    def _resync_preview_after_queue_change(self, removed_inputs: list[str]) -> None:
        """Keep the preview consistent after queue entries are removed/cleared."""
        if not hasattr(self, 'input_path_var'):
            return
        if self.input_path_var.get() not in removed_inputs:
            return
        if self.batch_items:
            self._load_input_file(self.batch_items[0]['input'])  # type: ignore[attr-defined]
        else:
            self._unload_input_file()  # type: ignore[attr-defined]

    def _batch_item_for_current_input(self) -> dict | None:  # type: ignore[type-arg]
        """The queue entry matching the currently loaded input file, if any."""
        if not hasattr(self, 'input_path_var'):
            return None
        current = self.input_path_var.get()
        if not current:
            return None
        for item in getattr(self, 'batch_items', None) or []:
            if item['input'] == current:
                return item
        return None

    def on_batch_item_select(self, event: object = None) -> None:
        """Preview the queue entry the user clicks."""
        if not hasattr(self, 'batch_listbox') or not hasattr(self, 'input_path_var'):
            return
        selection = self.batch_listbox.curselection()
        if not selection:
            return
        item = self.batch_items[selection[0]]
        if self.input_path_var.get() == item['input']:
            return
        self._load_input_file(item['input'])  # type: ignore[attr-defined]

    def _refresh_batch_list(self) -> None:
        """Redraw the queue listbox from batch_items with per-file status
        icons and a "*" marker on any item whose stored settings differ from
        what the control panel currently shows -- i.e. "if the batch started
        right now, this file would convert differently than what's on
        screen." Never marks the currently-loaded item, since its settings
        equal the live panel by construction (see _restore_settings_dict /
        _write_back_current_settings)."""
        if not hasattr(self, 'batch_listbox'):
            return
        selected_input = self._batch_item_for_current_input()
        self.batch_listbox.delete(0, tk.END)
        current_live = (self._current_settings_dict()  # type: ignore[attr-defined]
                        if hasattr(self, 'gamma_var') else None)
        for index, item in enumerate(self.batch_items):
            icon = self._STATUS_ICONS.get(item['status'], '•')
            marker = ''
            if current_live is not None and item.get('settings') not in (None, current_live):
                marker = '  *'
            self.batch_listbox.insert(
                tk.END, f"{icon}  {os.path.basename(item['input'])}{marker}")
            if item is selected_input:
                self.batch_listbox.selection_set(index)
                self.batch_listbox.activate(index)

    # ── Batch execution ────────────────────────────────────────────────────────

    def start_batch(self) -> bool:
        """Begin converting the queued files one after another."""
        if not self.batch_items:
            return False
        if not any(it['status'] == 'Pending' for it in self.batch_items):
            for it in self.batch_items:
                it['status'] = 'Pending'
            self._refresh_batch_list()
        if self.drop_target_registered:
            self.unregister_drop_target()  # type: ignore[attr-defined]
        self.cancel_button.grid()
        return self._start_next_batch_item()

    def _start_next_batch_item(self) -> bool:
        """Start the next Pending item, or finish the batch if none remain."""
        item = next((it for it in self.batch_items if it['status'] == 'Pending'), None)
        if item is None:
            self._finish_batch()
            return False

        input_path = os.path.normpath(item['input'])
        if not os.path.isfile(input_path):
            logging.error(f"Batch input not found, skipping: {input_path}")
            item['status'] = 'Failed'
            self._refresh_batch_list()
            return self._start_next_batch_item()

        output_path = os.path.normpath(item['output'])
        if os.path.exists(output_path):
            logging.warning(f"Batch output already exists, skipping: {output_path}")
            item['status'] = 'Failed'
            self._refresh_batch_list()
            return self._start_next_batch_item()

        item['status'] = 'Converting'
        self._current_batch_item = item
        self._refresh_batch_list()
        self.progress_var.set(0)

        current = self.input_path_var.get() if hasattr(self, 'input_path_var') else None
        if current != item['input']:
            self._load_input_file(item['input'])  # type: ignore[attr-defined]

        gamma = self.gamma_var.get()
        use_gpu = self.gpu_accel_var.get()
        tonemapper = self.tonemap_var.get().lower()
        quality_mode = self._QUALITY_MODE_TO_INTERNAL.get(  # type: ignore[attr-defined]
            self.quality_mode_var.get(), 'cq')
        quality = (int(self.bitrate_var.get()) if quality_mode == 'bitrate'
                   else int(self.quality_var.get()))
        bit_depth = self._selected_bit_depth()  # type: ignore[attr-defined]

        try:
            conversion_manager.start_conversion(
                input_path, output_path, gamma, use_gpu,
                self.progress_var, self.interactable_elements, self,
                self.open_after_conversion_var.get(), self.cancel_button,
                tonemapper=tonemapper, quality=quality, quality_mode=quality_mode,
                bit_depth=bit_depth, licensed=self._licensed,
                on_complete=self._on_batch_item_complete
            )
        except Exception as e:
            logging.error(f"Batch item failed to start ({input_path}): {e}")
            item['status'] = 'Failed'
            self._refresh_batch_list()
            return self._start_next_batch_item()
        return True

    def _on_batch_item_complete(self, success: bool) -> None:
        """Mark the finished item and advance the queue (runs on the main thread)."""
        if self._current_batch_item is not None:
            self._current_batch_item['status'] = 'Done' if success else 'Failed'
        self._current_batch_item = None
        self._refresh_batch_list()
        if not conversion_manager.cancelled:
            self._start_next_batch_item()

    def _finish_batch(self) -> None:
        """Re-enable the UI and report a one-line summary once the queue drains."""
        done = sum(1 for it in self.batch_items if it['status'] == 'Done')
        failed = sum(1 for it in self.batch_items if it['status'] == 'Failed')
        for element in self.interactable_elements:
            element.config(state='normal')
        self.cancel_button.grid_remove()
        if hasattr(self, 'register_drop_target'):
            self.register_drop_target()  # type: ignore[attr-defined]
        messagebox.showinfo(
            "Batch Complete", f"Batch finished: {done} succeeded, {failed} failed.")
