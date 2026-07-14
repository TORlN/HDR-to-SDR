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
        _batch_conflict_groups: list[list[dict]] | None  # type: ignore[type-arg]
        _batch_conflict_selection: dict[int, bool]
        batch_listbox: tk.Listbox
        batch_hint_label: ttk.Label
        batch_review_cancel_button: ttk.Button
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
        'Pending': '•', 'Converting': '▶', 'Done': '✓', 'Failed': '✗', 'Skipped': '−',
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
        self._cancel_batch_conflict_review()
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
        self._cancel_batch_conflict_review()
        removed_inputs = [self.batch_items[i]['input'] for i in selected]
        for index in selected:
            del self.batch_items[index]
        self._refresh_batch_list()
        self._resync_preview_after_queue_change(removed_inputs)

    def clear_batch_queue(self) -> None:
        """Empty the batch queue."""
        self._cancel_batch_conflict_review()
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

    @staticmethod
    def _settings_relevant_for_comparison(settings: dict) -> dict:  # type: ignore[type-arg]
        """Reduce a stored settings dict to the fields that actually affect
        the resulting ffmpeg command for its own quality_mode. quality_mode's
        two field groups (quality vs. bitrate/bitrate_fraction/
        bitrate_customized) are mutually exclusive at conversion time, so an
        inactive mode's leftover value (e.g. a stale CQ 'quality' on an item
        that's actually in Target Bitrate mode) must not affect equality --
        two items that would produce byte-identical ffmpeg commands must
        compare equal even if that leftover field differs."""
        mode = settings.get('quality_mode', 'cq')
        relevant = {
            'gamma': settings.get('gamma'),
            'quality_mode': mode,
            'tonemapper': settings.get('tonemapper'),
            'gpu_accel': settings.get('gpu_accel'),
            'bit_depth_choice': settings.get('bit_depth_choice'),
        }
        if mode == 'bitrate':
            relevant['bitrate_customized'] = settings.get('bitrate_customized', False)
            relevant['bitrate_fraction'] = settings.get('bitrate_fraction')
        else:
            relevant['quality'] = settings.get('quality')
        return relevant

    def _refresh_batch_list(self) -> None:
        """Redraw the queue listbox from batch_items. Normally this shows a
        per-file status icon and a "*" marker on any item whose stored
        settings differ from what the control panel currently shows. While a
        conflict review is in progress (self._batch_conflict_groups is not
        None), rows belonging to a conflict group instead render a
        checkbox and an explanatory note; every other row is unaffected.
        Never marks the currently-loaded item with "*", since its settings
        equal the live panel by construction (see _restore_settings_dict /
        _write_back_current_settings). May run from a debounced timer (see
        _schedule_batch_list_refresh), which can still be pending after the
        window is torn down -- winfo_exists() guards against that."""
        if not hasattr(self, 'batch_listbox') or not self.batch_listbox.winfo_exists():
            return
        selected_input = self._batch_item_for_current_input()
        self.batch_listbox.delete(0, tk.END)
        current_live = (self._current_settings_dict()  # type: ignore[attr-defined]
                        if hasattr(self, 'gamma_var') else None)
        current_live_relevant = (
            self._settings_relevant_for_comparison(current_live)
            if current_live is not None else None)
        conflict_groups = getattr(self, '_batch_conflict_groups', None)
        conflict_notes = self._batch_conflict_row_notes(conflict_groups) if conflict_groups else {}
        for index, item in enumerate(self.batch_items):
            note = conflict_notes.get(id(item))
            if note is not None:
                checked = self._batch_conflict_selection.get(id(item), False)
                box = '☑' if checked else '☐'
                self.batch_listbox.insert(
                    tk.END, f"{box}  {os.path.basename(item['input'])}  {note}")
            else:
                icon = self._STATUS_ICONS.get(item['status'], '•')
                marker = ''
                stored = item.get('settings')
                if current_live_relevant is not None and (
                        stored is None
                        or self._settings_relevant_for_comparison(stored) != current_live_relevant):
                    marker = '  *'
                self.batch_listbox.insert(
                    tk.END, f"{icon}  {os.path.basename(item['input'])}{marker}")
            if item is selected_input:
                self.batch_listbox.selection_set(index)
                self.batch_listbox.activate(index)

    def _batch_conflict_row_notes(self, groups: list[list[dict]]) -> dict[int, str]:  # type: ignore[type-arg]
        """Build the '(already exists...)' / '(same output as ...)' note for
        each item currently in conflict review, keyed by id(item)."""
        notes: dict[int, str] = {}
        for group in groups:
            path = os.path.normpath(group[0]['output'])
            exists = os.path.exists(path)
            for item in group:
                parts = []
                if exists:
                    parts.append('already exists')
                if len(group) > 1:
                    others = ', '.join(
                        os.path.basename(other['input'])
                        for other in group if other is not item)
                    parts.append(f'same output as {others}')
                notes[id(item)] = f"({'; '.join(parts)})"
        return notes

    def _detect_batch_conflicts(self) -> list[list[dict]]:  # type: ignore[type-arg]
        """Group Pending items by resolved output path; return only the groups
        that need user resolution (path exists on disk, and/or 2+ items target
        it). Only Pending items are considered, since those are what's about
        to run."""
        groups: dict[str, list[dict]] = {}
        for item in self.batch_items:
            if item['status'] != 'Pending':
                continue
            key = os.path.normpath(item['output'])
            groups.setdefault(key, []).append(item)
        return [group for path, group in groups.items()
                if len(group) > 1 or os.path.exists(path)]

    def _toggle_batch_conflict_item(self, item: dict) -> None:  # type: ignore[type-arg]
        """Flip item's checked state; if it shares a conflict group with other
        items, checking it unchecks every other item in that group so at most
        one item can ever win a shared output path."""
        groups = getattr(self, '_batch_conflict_groups', None)
        if not groups:
            return
        group = next((g for g in groups if item in g), None)
        if group is None:
            return
        now_checked = not self._batch_conflict_selection.get(id(item), False)
        self._batch_conflict_selection[id(item)] = now_checked
        if now_checked:
            for other in group:
                if other is not item:
                    self._batch_conflict_selection[id(other)] = False
        self._refresh_batch_list()

    def _enter_batch_conflict_review_ui(self) -> None:
        """Swap the batch hint label to conflict-count guidance and reveal
        the review-cancel button so the user can back out of review."""
        count = sum(len(g) for g in self._batch_conflict_groups)
        self.batch_hint_label.config(
            text=f"{count} output conflicts found — click a row to choose which "
                 "file keeps that path, then click Convert to start.")
        self.batch_review_cancel_button.grid()

    def _exit_batch_conflict_review_ui(self) -> None:
        """Restore the batch hint label and hide the review-cancel button."""
        self.batch_hint_label.config(
            text="Add or drop multiple files to convert them in sequence.")
        self.batch_review_cancel_button.grid_remove()

    def _cancel_batch_conflict_review(self) -> None:
        """Back out of conflict review without changing any item's status,
        as if the user had never clicked Convert."""
        if getattr(self, '_batch_conflict_groups', None) is None:
            return
        self._batch_conflict_groups = None
        self._batch_conflict_selection = {}
        self._exit_batch_conflict_review_ui()
        self._refresh_batch_list()

    # ── Batch execution ────────────────────────────────────────────────────────

    def start_batch(self) -> bool:
        """Begin converting the queued files one after another. When any
        output path already exists on disk or is targeted by more than one
        queued item, the first call enters conflict-review mode (checkbox
        rows appear in batch_listbox; nothing starts) instead of running; a
        second call, after the user has resolved every conflict via the
        listbox, finalizes those choices -- marking every unchecked
        conflicting item 'Skipped' -- and starts the batch."""
        if not self.batch_items:
            return False
        if not any(it['status'] == 'Pending' for it in self.batch_items):
            for it in self.batch_items:
                it['status'] = 'Pending'
            self._refresh_batch_list()

        if getattr(self, '_batch_conflict_groups', None) is None:
            conflicts = self._detect_batch_conflicts()
            if conflicts:
                self._batch_conflict_groups = conflicts
                self._batch_conflict_selection = {}
                self._refresh_batch_list()
                self._enter_batch_conflict_review_ui()
                return False
        else:
            for group in self._batch_conflict_groups:
                for item in group:
                    if not self._batch_conflict_selection.get(id(item), False):
                        item['status'] = 'Skipped'
            self._batch_conflict_groups = None
            self._batch_conflict_selection = {}
            self._exit_batch_conflict_review_ui()
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
