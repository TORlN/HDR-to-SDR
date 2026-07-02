"""A color-based dark theme for ttk, built on the ``clam`` engine.

Unlike image-based themes (sv_ttk/azure/forest), every widget here is drawn
from solid colors, so nothing has to be re-rendered from PNG assets on each
window resize. That keeps resizing smooth (~23ms/resize vs ~84ms for sv_ttk)
while still presenting a flat, modern dark UI.

Call :func:`apply_dark_theme(root)` once, ideally *before* the widgets are
created so classic Tk widgets (Listbox, the Combobox dropdown) inherit the dark
colors from the option database too.
"""

from tkinter import ttk

# Flat dark palette (VS Code-ish).
BG        = '#1e1e1e'   # window / frame background
SURFACE   = '#252526'   # raised surfaces (label frames)
FIELD     = '#2d2d30'   # entry / combobox / scale trough
FG        = '#e4e4e4'   # primary text
MUTED     = '#9a9a9a'   # hint text
DISABLED  = '#6a6a6a'   # disabled text
BORDER    = '#3c3c3c'   # widget borders
BTN       = '#333337'   # button face
BTN_HOVER = '#3e3e44'   # button hover
ACCENT    = '#0e639c'   # selection / progress / pressed
ACCENT_HI = '#1f7fc4'   # accent hover


def apply_dark_theme(root):
    """Apply the dark clam theme to ``root`` and all ttk widgets under it."""
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(background=BG)

    # Base: every ttk widget inherits these unless overridden below.
    style.configure('.',
                    background=BG, foreground=FG, fieldbackground=FIELD,
                    troughcolor=FIELD, bordercolor=BORDER,
                    lightcolor=BG, darkcolor=BG,
                    focuscolor=ACCENT, insertcolor=FG, arrowcolor=FG,
                    relief='flat')
    style.map('.',
              foreground=[('disabled', DISABLED)],
              fieldbackground=[('disabled', BG)])

    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG, foreground=FG)

    style.configure('TLabelframe', background=BG, bordercolor=BORDER, relief='solid')
    style.configure('TLabelframe.Label', background=BG, foreground=MUTED)

    # Buttons -- flat, subtle hover, accent when pressed/active.
    style.configure('TButton', background=BTN, foreground=FG,
                    bordercolor=BORDER, focusthickness=1, padding=(8, 4), relief='flat')
    style.map('TButton',
              background=[('pressed', ACCENT), ('active', BTN_HOVER)],
              foreground=[('disabled', DISABLED)],
              bordercolor=[('focus', ACCENT)])

    # The "selected frame" button (sunken in sv_ttk) -> accent fill here.
    style.configure('Selected.TButton', background=ACCENT, foreground=FG, relief='flat')
    style.map('Selected.TButton', background=[('active', ACCENT_HI), ('pressed', ACCENT)])

    style.configure('TCheckbutton', background=BG, foreground=FG, focuscolor=BG)
    style.map('TCheckbutton',
              background=[('active', BG)],
              indicatorcolor=[('selected', ACCENT), ('!selected', FIELD)],
              foreground=[('disabled', DISABLED)])

    style.configure('TRadiobutton', background=BG, foreground=FG, focuscolor=BG)
    style.map('TRadiobutton',
              background=[('active', BG)],
              indicatorcolor=[('selected', ACCENT), ('!selected', FIELD)],
              foreground=[('disabled', DISABLED)])

    style.configure('TEntry', fieldbackground=FIELD, foreground=FG,
                    bordercolor=BORDER, insertcolor=FG, padding=4)
    style.map('TEntry', bordercolor=[('focus', ACCENT)])

    style.configure('TCombobox', fieldbackground=FIELD, background=BTN,
                    foreground=FG, arrowcolor=FG, bordercolor=BORDER, padding=4)
    style.map('TCombobox',
              fieldbackground=[('readonly', FIELD)],
              foreground=[('readonly', FG), ('disabled', DISABLED)],
              bordercolor=[('focus', ACCENT)],
              selectbackground=[('readonly', FIELD)],
              selectforeground=[('readonly', FG)])

    # Sliders: dark trough, a single solid-color accent knob. clam normally
    # bevels the knob (light/dark edges over the fill), which read as "blue
    # edges, dark middle"; pin fill + border + both bevel colors to the same
    # accent so the knob is one flat color.
    for orient in ('Horizontal', 'Vertical'):
        style.configure(f'{orient}.TScale', background=ACCENT, troughcolor=FIELD,
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
        style.map(f'{orient}.TScale',
                  background=[('active', ACCENT_HI)],
                  bordercolor=[('active', ACCENT_HI)],
                  lightcolor=[('active', ACCENT_HI)],
                  darkcolor=[('active', ACCENT_HI)])

    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=FIELD,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)

    style.configure('TScrollbar', background=BTN, troughcolor=BG,
                    bordercolor=BG, arrowcolor=FG)
    style.map('TScrollbar', background=[('active', BTN_HOVER)])

    # Classic Tk widgets (Listbox, the Combobox dropdown) aren't styled by ttk;
    # push dark colors through the option database so they pick them up too.
    root.option_add('*Listbox.background', FIELD)
    root.option_add('*Listbox.foreground', FG)
    root.option_add('*Listbox.selectBackground', ACCENT)
    root.option_add('*Listbox.selectForeground', FG)
    root.option_add('*Listbox.highlightThickness', '0')
    root.option_add('*Listbox.borderWidth', '0')
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', FG)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
    root.option_add('*TCombobox*Listbox.selectForeground', FG)

    return style
