# -*- mode: python ; coding: utf-8 -*-

import os

# PyInstaller injects SPECPATH (this file's directory) when it executes the
# spec. Building every path from it keeps the spec working from any checkout --
# it previously hardcoded one developer's Desktop and built nowhere else.
# test/build_spec_test.py enforces this.
def _p(*parts):
    return os.path.join(SPECPATH, *parts)


# build_installer.bat activates .venv before invoking PyInstaller, so the
# bundled Tk/PIL support files come from the same interpreter that resolves
# the imports.
def _site_packages(*parts):
    return _p('.venv', 'Lib', 'site-packages', *parts)


a = Analysis(
    [_p('src', 'main.pyw')],
    pathex=[],
    binaries=[
        (_p('src', 'ffmpeg.exe'), '.'),
        (_p('src', 'ffprobe.exe'), '.'),
    ],
    datas=[
        (_p('logo', 'icon.ico'), '.'),
        (_p('src', 'luts'), 'luts'),
        (_site_packages('tkinterdnd2'), 'tkinterdnd2'),
        (_site_packages('PIL'), 'PIL'),
    ],
    # Must stay in sync with the --hidden-import flags in build_installer.bat
    # (Step 3, CLI branch): that branch ignores this spec entirely, so a module
    # added to one and not the other ships fine unobfuscated but silently drops
    # out of obfuscated builds. build_installer.bat's post-build PYZ-00.toc
    # check catches it at build time; test/build_spec_test.py catches the drift
    # in CI, before a build is ever attempted.
    hiddenimports=['pro.licensing', 'pro.batch', 'pro.license_dialog', 'pro._secrets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'scipy', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HDR_to_SDR_Converter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_p('logo', 'icon.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HDR_to_SDR_Converter',
)
