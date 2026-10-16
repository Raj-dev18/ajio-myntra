# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect all resources from playwright, supabase, and PySide6
pw_datas, pw_binaries, pw_hiddenimports = collect_all('playwright')
sb_datas, sb_binaries, sb_hiddenimports = collect_all('supabase')
pg_datas, pg_binaries, pg_hiddenimports = collect_all('postgrest')

datas = [
    ('scrapers', 'scrapers'),
    ('.env', '.')
] + pw_datas + sb_datas + pg_datas

binaries = pw_binaries + sb_binaries + pg_binaries

hiddenimports = [
    'playwright',
    'playwright.async_api',
    'playwright._impl._driver',
    'supabase',
    'postgrest',
    'realtime',
    'storage3',
    'dotenv',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets'
] + pw_hiddenimports + sb_hiddenimports + pg_hiddenimports

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ProductScraper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # Keep console open so scraper logs and diagnostics can be monitored
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ProductScraper',
)
