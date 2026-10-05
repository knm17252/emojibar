# PyInstaller spec for EmojiBar.
# Put EmojiBar.icns and emojibar_1b.py next to this file, then:
#   pip3 install -U pyinstaller
#   PyInstaller --noconfirm EmojiBar.spec
#   codesign --force --deep --sign - dist/EmojiBar.app
a = Analysis(
    ["emojibar_1b.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["objc", "AppKit", "Foundation", "Quartz",
                   "ApplicationServices", "CoreFoundation"],
    # add "websockets" above if you use the optional CDP mode
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="EmojiBar",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="EmojiBar")
app = BUNDLE(
    coll,
    name="EmojiBar.app",
    icon="EmojiBar.icns",
    bundle_identifier="ua.mk.emojibar",  # keep in sync with BUNDLE_ID
    info_plist={
        "CFBundleName": "EmojiBar",
        "CFBundleDisplayName": "EmojiBar",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1B",
        "LSUIElement": True,  # menu bar only; the Dock setting toggles this at runtime
    },
)
