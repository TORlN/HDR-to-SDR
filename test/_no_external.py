"""Guards that assert a test performed no real external work.

Tests mock ffmpeg/ffprobe and the network. These context managers turn a test
that quietly shells out to ffprobe -- or opens a TLS connection to GitHub --
from invisible-but-slow into a loud failure naming the call.

Both guards *record* every violation as well as raising, because the code
under test frequently catches broad exceptions (``get_video_properties`` and
``_nvidia_present`` both swallow ``OSError``). A raise alone would be absorbed
and the test would pass anyway; the recorded list is re-checked on exit.

Deliberate exceptions do not use these guards: ``smoke_test.py`` runs real HDR
encodes against the committed fixtures, and
``performance_test.TestExtractionPerfAudit`` times the real decode path. Both
say so in their docstrings.
"""
import os
import socket
import subprocess
import sys
from contextlib import contextmanager

# Every GUI test loads this module, which makes it the highest-value place to
# arm the blocking-dialog traps for a `discover -s ./test` run: that
# invocation never imports test/__init__.py, so the traps armed there are
# absent. Importing _dialog_trap more than once is a no-op by design.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _dialog_trap  # noqa: E402,F401

_LOOPBACK = frozenset(('127.0.0.1', '::1', 'localhost', ''))


class RealExternalCall(AssertionError):
    """Raised at the call site that escaped its mocks."""


@contextmanager
def no_real_subprocess(what='this test'):
    """Fail if the block spawns a real process (ffmpeg, ffprobe, nvidia-smi)."""
    violations = []
    real_init = subprocess.Popen.__init__

    def guarded(self, args, *a, **kw):
        argv = args[0] if isinstance(args, (list, tuple)) and args else args
        name = os.path.basename(str(argv))
        violations.append(name)
        raise RealExternalCall(
            f'{what} spawned a real subprocess ({name}). Mock the seam that '
            f'reaches it -- get_video_properties/get_maxcll for ffprobe, '
            f'detect_gpu_encoder/vulkan_libplacebo_available for ffmpeg.')

    subprocess.Popen.__init__ = guarded
    try:
        yield violations
    finally:
        subprocess.Popen.__init__ = real_init
    if violations:
        raise RealExternalCall(
            f'{what} spawned real subprocesses: {sorted(set(violations))}')


@contextmanager
def no_real_network(what='this test'):
    """Fail if the block opens a non-loopback socket."""
    violations = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(addr):
        host = addr[0] if isinstance(addr, tuple) and addr else addr
        if str(host) in _LOOPBACK:
            return False
        violations.append(str(addr))
        return True

    def guarded_connect(self, addr):
        if _check(addr):
            raise RealExternalCall(
                f'{what} opened a real network connection to {addr}. The test '
                f'suite must never reach the internet -- it is slow, flaky, '
                f'and fails outright on an offline machine.')
        return real_connect(self, addr)

    def guarded_connect_ex(self, addr):
        if _check(addr):
            raise RealExternalCall(
                f'{what} opened a real network connection to {addr}.')
        return real_connect_ex(self, addr)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    try:
        yield violations
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
    if violations:
        raise RealExternalCall(
            f'{what} opened real network connections: {sorted(set(violations))}')


@contextmanager
def no_real_dialogs(what='this test'):
    """Fail if the block opens a real modal dialog.

    A real messagebox BLOCKS the whole run until a human clicks it -- worse
    than any slow subprocess, because an unattended CI run or a build gate
    simply hangs. The stubs return a safe default rather than raising, so the
    dialog never appears even for code that ignores the return value; the
    recorded list is what fails the test.
    """
    import tkinter.filedialog as filedialog
    import tkinter.messagebox as messagebox

    violations = []
    saved = {}

    def install(module, name, result):
        saved[(module, name)] = getattr(module, name)

        def stub(*_a, **_kw):
            violations.append(f'{module.__name__}.{name}')
            return result
        setattr(module, name, stub)

    for name, result in (('showerror', 'ok'), ('showwarning', 'ok'),
                         ('showinfo', 'ok'), ('askyesno', False),
                         ('askokcancel', False), ('askretrycancel', False),
                         ('askquestion', 'no'), ('askyesnocancel', False)):
        install(messagebox, name, result)
    for name in ('askopenfilename', 'askopenfilenames', 'asksaveasfilename',
                 'askdirectory'):
        install(filedialog, name, '')

    try:
        yield violations
    finally:
        for (module, name), original in saved.items():
            setattr(module, name, original)
    if violations:
        raise RealExternalCall(
            f'{what} opened real modal dialogs: {sorted(set(violations))}. A '
            f'modal blocks the run until someone clicks it -- mock it.')


def pending_after_scripts(root):
    """Names of the Tcl scripts currently queued on *root*'s ``after`` timers.

    A queued timer is not inert: it fires the moment any later test pumps this
    shared root (``dlg.update()``, ``wait_window``, ...), so a timer armed by
    one test runs its callback during an unrelated one.
    """
    scripts = []
    for timer_id in root.tk.splitlist(root.tk.call('after', 'info')):
        try:
            scripts.append(str(root.tk.call('after', 'info', timer_id)))
        except Exception:
            pass
    return scripts


def drain_after_timers(root):
    """Cancel every pending ``after`` timer on *root*.

    The GUI suites share one Tk interpreter across the whole module, so timers
    survive the test that armed them. ``HDRConverterGUI.__init__`` arms
    ``after(3000, _start_update_check)``, whose worker fetches the releases
    feed over HTTPS -- it fires inside whichever later test first pumps the
    event loop, producing real network traffic attributed to an innocent test
    (and, once the widget is gone, the "invalid command name
    ..._start_update_check" Tcl noise). Draining in setUp gives each test a
    clean event queue.
    """
    for timer_id in root.tk.splitlist(root.tk.call('after', 'info')):
        try:
            root.after_cancel(timer_id)
        except Exception:
            pass
