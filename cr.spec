import platform, os

codesign_identity = None
strip = False

# If this is being run in a release pipeline, sign the executable with
# codesign_identity for macos.
if os.environ.get("CR_RELEASE", "").lower() == "true":
   codesign_identity = "Developer ID Application: CodeRed LLC (26334S6DB6)"

# Apply symbol table stripping from the executable on Linux,
# to reduce file size.
if platform.system() == "Linux":
   strip = True

block_cipher = None

a = Analysis(
    ["cr/cli.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("cr/templates", "cr/templates"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="cr",
    icon="icon/cr.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=strip,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=codesign_identity,
    entitlements_file=None,
)
