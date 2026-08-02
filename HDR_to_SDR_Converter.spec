# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\Torin\\Desktop\\HDR to SDR\\src\\main.pyw'],
    pathex=[],
    binaries=[('C:\\Users\\Torin\\Desktop\\HDR to SDR\\src\\ffmpeg.exe', '.'), ('C:\\Users\\Torin\\Desktop\\HDR to SDR\\src\\ffprobe.exe', '.')],
    datas=[('C:\\Users\\Torin\\Desktop\\HDR to SDR\\logo\\icon.ico', '.'), ('C:\\Users\\Torin\\Desktop\\HDR to SDR\\src\\luts', 'luts'), ('C:\\Users\\Torin\\Desktop\\HDR to SDR\\.venv\\Lib\\site-packages\\tkinterdnd2', 'tkinterdnd2'), ('C:\\Users\\Torin\\Desktop\\HDR to SDR\\.venv\\Lib\\site-packages\\PIL', 'PIL')],
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
    icon=['C:\\Users\\Torin\\Desktop\\HDR to SDR\\logo\\icon.ico'],
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
