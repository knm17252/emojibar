#!/usr/bin/env python3
"""EmojiBar 1B
1B: left-click on the menu bar icon toggles the 5th icon on/off (the menu item for it is
gone); right-click / ctrl-click still opens the menu.

1A1:
1A1: left-click the menu bar icon opens the picker; right-click (or ctrl-click) opens
the menu.

1A:
1A: "Show in Dock" setting; About window shows the real app icon when running as a
bundle; launch-at-login works for the .app bundle. Package with setup.py (py2app).

0u:
0u: About window metrics matched to DarkBar's; Preferences window titled "Settings".

0t:
0t: renamed EmojiBar; menu rearranged (header, About, Preferences, Show 5th icon,
Extras submenu, Quit); About window.

0s:
0s: menu bar glyph is cropped to its visible pixels before scaling (MENUBAR_ICON_SIZE).

0r:
0r: menu bar glyph keeps aspect ratio; default shortcuts Ctrl+E (picker) and
Ctrl+Cmd+E (toggle 5th icon); settings version resets old shortcuts once.

0q:
0q: "Show 5th icon" is a checkmark item in the menu bar menu; Settings has a recordable
shortcut to toggle it (plus the picker shortcut); menu bar glyph 16pt.

0p:
0p: settings window (launch, 5th icon, recordable picker shortcut); white tray icon
redrawn explicitly; menu bar icon = stock picker icon.

0o:
0o: bg 54, white icon, debug menu items removed, Settings submenu (start at launch,
5th icon on/off, global hotkey Ctrl+Opt+E).

0n:
0n: flat gray tray button (TRAY_BG_WHITE / TRAY_CORNER); verified insertion for all
apps where the focused field's AX value is readable, with automatic learning of
apps that need focus-paste (saved in ~/.emojitray_focus_apps.json); menu items to
mark/forget apps manually.

0m
0m: borderless tray button; optional DevTools-Protocol insertion for Electron apps
(no app switch, no kickout); AX gives up on an app after 2 failures.

CDP setup (one time, only if you want it):
  pip3 install websockets
  quit Claude/Discord fully, then relaunch:
    open -a Claude  --args --remote-debugging-port=9333
    open -a Discord --args --remote-debugging-port=9334
  other apps: EMOJITRAY_CDP=bundle.id=port,bundle.id2=port2
Note: any local process can talk to an open debugging port.

0l (based on 0j; sticky mode dropped)
0l: verified AX insertion with Chromium accessibility enabled for focus apps;
fallback is a fast blink (popover not dismissed) instead of close/reopen.
Batch mode still available in the menu (default off).

0j:
0j: Discord added to focus-paste list; focus-paste apps default to batch mode
(picker stays open, emoji queued, pasted when the picker closes).

0i:
0i: no focus-steal-back after you switch apps; focus-paste path for apps that
need real focus (Electron etc.); tray icon taken from the stock picker item.

0h:
Control Strip button -> stock macOS emoji picker (Touch Bar), inserted into the
app you were typing in.

How it works:
 - an invisible key panel + NSTextView gives the picker a real input target
 - the text view's own touch bar hosts the picker item; showPopover: opens it
 - emoji arrive in the hidden text view, then are inserted into the previous
   app via the Accessibility API (fallback: key event posted to its pid)
 - when the popover closes, focus goes back to the previous app

Run:   python3 -u emojitray_0h.py
Needs: pip3 install pyobjc-framework-Cocoa pyobjc-framework-Quartz \
                    pyobjc-framework-ApplicationServices
Accessibility permission for your terminal/Python is needed for insertion.
Menu bar item: Show / Dismiss / [x] host via modal bar / Quit. Ctrl+C quits.
"""
import ctypes
import faulthandler
import json
import os
import signal
import sys
import time
import traceback
import urllib.request

try:
    faulthandler.enable()
except Exception:  # no stderr in a windowed bundle
    pass


def log(*a):
    print("[1B]", *a, flush=True)


import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
    NSBitmapImageRep, NSButton, NSColor, NSEvent, NSRectFillUsingOperation,
    NSFont, NSImageView, NSTextField, NSView, NSWindow, NSCustomTouchBarItem, NSImage, NSMenu, NSMenuItem, NSPanel,
    NSPasteboard, NSPasteboardTypeString, NSStatusBar, NSTextView, NSTouchBar,
    NSTouchBarItem, NSVariableStatusItemLength, NSWorkspace,
)
from Foundation import NSBundle, NSObject, NSTimer

try:
    from Quartz import (CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString,
                        CGEventPost, CGEventPostToPid, CGEventSetFlags,
                        kCGEventFlagMaskCommand, kCGHIDEventTap)
except Exception:
    CGEventPostToPid = None
    CGEventPost = None
    log("Quartz import failed; key-event fallback disabled")

try:
    from ApplicationServices import (
        AXIsProcessTrusted, AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication, AXUIElementSetAttributeValue,
        kAXFocusedUIElementAttribute, kAXSelectedTextAttribute,
        kAXValueAttribute)
    from CoreFoundation import kCFBooleanTrue
    AX_OK = True
except Exception:
    AX_OK = False
    log("ApplicationServices import failed; AX insertion disabled "
        "(pip3 install pyobjc-framework-ApplicationServices)")

TRAY_ID = "ua.mk.emojibar.tray"
PICKER_ID = "NSTouchBarItemIdentifierCharacterPicker"
NONACTIVATING_PANEL = 1 << 7
TRAY_BG_WHITE = 54 / 255   # gray level of the tray button background (0-1)
VERSION = "1.0"
BUILD = "1B"
BUNDLE_ID = "ua.mk.emojibar"  # must match EmojiBar.spec
MENUBAR_ICON_SIZE = 17   # pt, longer side of the visible glyph
TRAY_CORNER = 0        # corner radius of the tray button
DEFAULT_HOTKEY = {"code": 14, "flags": 1 << 18, "label": "\u2303E"}
DEFAULT_ICON_HOTKEY = {"code": 14, "flags": (1 << 18) | (1 << 20), "label": "\u2303\u2318E"}
MOD_MASK = (1 << 17) | (1 << 18) | (1 << 19) | (1 << 20)  # shift ctrl opt cmd
FLASH_PRESS = 0.12   # seconds after activating target before Cmd+V
FLASH_BACK = 0.30    # seconds after activating target before returning
CDP_PORTS = {"com.anthropic.claudefordesktop": 9333, "com.hnc.Discord": 9334}
for _pair in os.environ.get("EMOJITRAY_CDP", "").split(","):
    if "=" in _pair:
        _b, _p = _pair.rsplit("=", 1)
        try:
            CDP_PORTS[_b.strip()] = int(_p)
        except ValueError:
            pass
# apps that need real focus to accept input; extend with EMOJITRAY_FOCUS_PASTE
FOCUS_PASTE_BUNDLES = {"com.anthropic.claudefordesktop", "com.hnc.Discord"} | {
    b for b in os.environ.get("EMOJITRAY_FOCUS_PASTE", "").split(",") if b}
PRESENT = "presentSystemModalTouchBar_placement_systemTrayItemIdentifier_"

dfr = ctypes.CDLL(
    "/System/Library/PrivateFrameworks/DFRFoundation.framework/DFRFoundation")
dfr.DFRElementSetControlStripPresenceForIdentifier.argtypes = [
    ctypes.c_void_p, ctypes.c_bool]
dfr.DFRSystemModalShowsCloseBoxWhenFrontMost.argtypes = [ctypes.c_bool]

_libobjc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
_libobjc.objc_getClass.restype = ctypes.c_void_p
_libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
_libobjc.sel_registerName.restype = ctypes.c_void_p
_libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
_msg = ctypes.cast(_libobjc.objc_msgSend, ctypes.c_void_p).value


def make_nsstring_ptr(text):
    """Retained (never released) raw NSString* as an int."""
    cls = _libobjc.objc_getClass(b"NSString")
    alloc = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p)(_msg)
    init = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_char_p)(_msg)
    obj = alloc(cls, _libobjc.sel_registerName(b"alloc"))
    return init(obj, _libobjc.sel_registerName(b"initWithUTF8String:"),
                text.encode())


# ---- insertion into another app ------------------------------------------
def insert_via_ax(text, pid):
    if not AX_OK:
        return False
    if not AXIsProcessTrusted():
        log("AX: not trusted (grant Accessibility to your terminal)")
        return False
    app = AXUIElementCreateApplication(pid)
    err, el = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute, None)
    if err != 0 or el is None:
        log("AX: no focused element, err", err)
        return False
    err = AXUIElementSetAttributeValue(el, kAXSelectedTextAttribute, text)
    if err != 0:
        log("AX: set selected text failed, err", err)
        return False
    return True


_ax_enabled = set()


def _ax_verified_once(text, pid):
    """AX insert that checks the field value really changed. Turns on
    Chromium/Electron accessibility first (stays on until that app restarts)."""
    if not AX_OK or not AXIsProcessTrusted():
        return False
    app = AXUIElementCreateApplication(pid)
    if pid not in _ax_enabled:
        for attr in ("AXManualAccessibility", "AXEnhancedUserInterface"):
            AXUIElementSetAttributeValue(app, attr, kCFBooleanTrue)
        _ax_enabled.add(pid)
        time.sleep(0.15)  # let the accessibility tree build
    err, el = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute, None)
    if err != 0 or el is None:
        log("AX(v): no focused element, err", err)
        return False
    err, before = AXUIElementCopyAttributeValue(el, kAXValueAttribute, None)
    if err != 0 or not isinstance(before, str):
        log("AX(v): can't read field value, err", err)
        return False
    err = AXUIElementSetAttributeValue(el, kAXSelectedTextAttribute, text)
    if err != 0:
        log("AX(v): set selected text failed, err", err)
        return False
    time.sleep(0.1)
    err, after = AXUIElementCopyAttributeValue(el, kAXValueAttribute, None)
    ok = err == 0 and isinstance(after, str) and after != before
    log("AX(v): verified" if ok else "AX(v): value unchanged (app ignored it)")
    return ok


_ax_fails = {}

_LEARN_FILE = os.path.expanduser("~/.emojitray_focus_apps.json")
_FOCUS_DEFAULTS = set(FOCUS_PASTE_BUNDLES)


def load_learned():
    try:
        with open(_LEARN_FILE) as f:
            FOCUS_PASTE_BUNDLES.update(json.load(f))
    except Exception:
        pass


def save_learned():
    try:
        with open(_LEARN_FILE, "w") as f:
            json.dump(sorted(FOCUS_PASTE_BUNDLES - _FOCUS_DEFAULTS), f)
    except Exception:
        traceback.print_exc()


load_learned()

_SETTINGS_FILE = os.path.expanduser("~/.emojitray_settings.json")
LAUNCH_PLIST = os.path.expanduser(
    "~/Library/LaunchAgents/ua.mk.emojibar.launcher.plist")
LEGACY_PLIST = os.path.expanduser(
    "~/Library/LaunchAgents/com.example.emojitray.plist")  # from early test builds


def load_settings():
    d = {"launch": False, "icon": True, "dock": False, "hotkey": dict(DEFAULT_HOTKEY),
         "icon_hotkey": None}
    try:
        with open(_SETTINGS_FILE) as f:
            d.update(json.load(f))
    except Exception:
        pass
    if not isinstance(d["hotkey"], dict):  # older settings file
        d["hotkey"] = dict(DEFAULT_HOTKEY) if d["hotkey"] else None
    if d.get("ver") != 2:  # new default shortcuts
        d["hotkey"] = dict(DEFAULT_HOTKEY)
        d["icon_hotkey"] = dict(DEFAULT_ICON_HOTKEY)
        d["ver"] = 2
    return d


def save_settings(d):
    try:
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(d, f)
    except Exception:
        traceback.print_exc()


def set_launch_agent(on):
    import plistlib
    try:
        os.remove(LEGACY_PLIST)
    except FileNotFoundError:
        pass
    if on:
        os.makedirs(os.path.dirname(LAUNCH_PLIST), exist_ok=True)
        b = NSBundle.mainBundle()
        if str(b.bundleIdentifier()) == BUNDLE_ID:
            args = ["/usr/bin/open", str(b.bundlePath())]
        else:
            args = [sys.executable, os.path.abspath(__file__)]
        with open(LAUNCH_PLIST, "wb") as f:
            plistlib.dump({"Label": "ua.mk.emojibar.launcher",
                           "ProgramArguments": args,
                           "RunAtLoad": True}, f)
    else:
        try:
            os.remove(LAUNCH_PLIST)
        except FileNotFoundError:
            pass


def ax_focused_value(pid):
    """(element, value string) of the target's focused element, or (None, None)
    if Accessibility can't read it."""
    if not AX_OK or not AXIsProcessTrusted():
        return None, None
    app = AXUIElementCreateApplication(pid)
    err, el = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute, None)
    if err != 0 or el is None:
        return None, None
    err, val = AXUIElementCopyAttributeValue(el, kAXValueAttribute, None)
    if err != 0 or not isinstance(val, str):
        return None, None
    return el, str(val)


def ax_changed(el, before, wait=0.2):
    time.sleep(wait)
    err, val = AXUIElementCopyAttributeValue(el, kAXValueAttribute, None)
    return err == 0 and isinstance(val, str) and str(val) != before


def insert_via_ax_verified(text, pid):
    if _ax_fails.get(pid, 0) >= 2:
        return False
    ok = _ax_verified_once(text, pid)
    if not ok:
        _ax_fails[pid] = _ax_fails.get(pid, 0) + 1
    return ok


_cdp_warned = set()


def cdp_insert(text, port):
    """Insert via Chrome DevTools Protocol into the focused editable element,
    using focus emulation so the page behaves as if its window were key."""
    try:
        from websockets.sync.client import connect
    except Exception:
        log("CDP: pip3 install websockets")
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % port,
                                    timeout=0.4) as r:
            targets = json.load(r)
    except Exception:
        return False
    check = ("(()=>{const a=document.activeElement;return !!a&&"
             "(a.isContentEditable||/^(INPUT|TEXTAREA)$/.test(a.tagName))})()")
    for t in targets:
        url = t.get("webSocketDebuggerUrl")
        if t.get("type") != "page" or not url:
            continue
        try:
            with connect(url, open_timeout=1, max_size=None) as ws:
                n = [0]

                def call(method, params=None):
                    n[0] += 1
                    ws.send(json.dumps({"id": n[0], "method": method,
                                        "params": params or {}}))
                    while True:
                        msg = json.loads(ws.recv(timeout=2))
                        if msg.get("id") == n[0]:
                            return msg

                call("Emulation.setFocusEmulationEnabled", {"enabled": True})
                res = call("Runtime.evaluate",
                           {"expression": check, "returnByValue": True})
                editable = res.get("result", {}).get("result", {}).get("value") is True
                if editable:
                    call("Input.insertText", {"text": text})
                call("Emulation.setFocusEmulationEnabled", {"enabled": False})
                if editable:
                    return True
        except Exception:
            traceback.print_exc()
    return False


def insert_via_cdp(text, bundle):
    port = CDP_PORTS.get(bundle)
    if port is None:
        return False
    ok = cdp_insert(text, port)
    if not ok and bundle not in _cdp_warned:
        _cdp_warned.add(bundle)
        log("CDP: nothing inserted via port", port,
            "(is the app launched with --remote-debugging-port=%d?)" % port)
    return ok


def type_via_event(text, pid):
    if CGEventPostToPid is None:
        return False
    n = len(text.encode("utf-16-le")) // 2
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, 0, down)
        CGEventKeyboardSetUnicodeString(ev, n, text)
        CGEventPostToPid(pid, ev)
    return True


def deliver(text, pid):
    if insert_via_ax(text, pid):
        log("inserted via AX:", repr(text))
    elif type_via_event(text, pid):
        log("typed via key event:", repr(text))
    else:
        log("could not insert", repr(text))


def white_image(img):
    """Pure-white copy of img, keeping its alpha."""
    size = img.size()
    out = NSImage.alloc().initWithSize_(size)
    out.lockFocus()
    img.drawInRect_fromRect_operation_fraction_(
        ((0, 0), size), ((0, 0), (0, 0)), 2, 1.0)
    NSColor.whiteColor().set()
    NSRectFillUsingOperation(((0, 0), size), 5)  # SourceAtop
    out.unlockFocus()
    out.setTemplate_(False)
    return out


def trimmed_template(img, target):
    """Crop transparent margins, scale so the longer side is `target` pt,
    return as a template image (None if nothing visible)."""
    rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
    pw, ph = int(rep.pixelsWide()), int(rep.pixelsHigh())
    step = 1 if pw * ph <= 40000 else 3
    x0, x1, y0, y1 = pw, -1, ph, -1
    for y in range(0, ph, step):
        for x in range(0, pw, step):
            c = rep.colorAtX_y_(x, y)
            if c is not None and c.alphaComponent() > 0.1:
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    if x1 < 0:
        return None
    k = img.size().width / pw
    cw, ch = (x1 - x0 + 1) * k, (y1 - y0 + 1) * k
    ox, oy = x0 * k, (ph - 1 - y1) * k  # bitmap origin is top-left
    f = target / max(cw, ch)
    out = NSImage.alloc().initWithSize_((cw * f, ch * f))
    out.lockFocus()
    img.drawInRect_fromRect_operation_fraction_(
        ((0, 0), (cw * f, ch * f)), ((ox, oy), (cw, ch)), 2, 1.0)
    out.unlockFocus()
    out.setTemplate_(True)
    return out


class KeyPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return True


class Controller(NSObject):
    def applicationDidFinishLaunching_(self, note):
        self.prev_app = None
        self.picker_item = None
        self.session = False
        self.seen_presented = False
        self.t0 = 0.0
        self.ticks = 0
        self.use_modal = False
        self.settings = load_settings()
        self.hotkey_monitor = None
        self.win = None
        self.about = None
        self.stock_img = None
        self.rec_monitor = None
        self.batch = False
        self.pending = []
        self.flashing = False
        for label, fn in (("menu bar", self.setup_menubar),
                          ("panel", self.setup_panel),
                          ("tray", self.setup_tray)):
            try:
                fn()
                log("ok  :", label)
            except Exception:
                log("FAIL:", label)
                traceback.print_exc()
        self.apply_hotkey()
        self.apply_dock()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.25, True, lambda t: self.poll())
        log("ready; AX available:", AX_OK,
            "trusted:", AXIsProcessTrusted() if AX_OK else None)

    # ---- menu bar -------------------------------------------------------
    def setup_menubar(self):
        self.status = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength)
        self.status.button().setTitle_("\U0001F600")
        menu = NSMenu.alloc().init()

        def add(m, title, action, key="", state=None):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, action, key)
            if action is not None:
                it.setTarget_(self)
            if state is not None:
                it.setState_(1 if state else 0)
            m.addItem_(it)
            return it

        add(menu, "EmojiBar " + VERSION, None).setEnabled_(False)
        menu.addItem_(NSMenuItem.separatorItem())
        add(menu, "About EmojiBar\u2026", b"showAbout:")
        add(menu, "Preferences\u2026", b"showSettings:", ",")
        extras = NSMenu.alloc().init()
        add(extras, "Host via modal bar (slower fallback)", b"toggleModal:", "",
            self.use_modal)
        add(extras, "Focus-paste apps: batch until picker closes",
            b"toggleBatch:", "", self.batch)
        extras.addItem_(NSMenuItem.separatorItem())
        add(extras, "Use focus-paste for the last target app", b"markLast:")
        add(extras, "Forget learned focus-paste apps", b"forgetLearned:")
        parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Extras", None, "")
        parent.setSubmenu_(extras)
        menu.addItem_(parent)
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit EmojiBar", b"terminate:", "q"))
        self.menu = menu
        btn = self.status.button()
        btn.setTarget_(self)
        btn.setAction_(b"statusClicked:")
        btn.sendActionOn_((1 << 2) | (1 << 4))  # left + right mouse up

    def statusClicked_(self, sender):
        ev = NSApplication.sharedApplication().currentEvent()
        if ev is not None and (ev.type() == 4 or int(ev.modifierFlags()) & (1 << 18)):
            # right-click / ctrl-click: show the menu
            self.status.setMenu_(self.menu)
            self.status.button().performClick_(None)
            self.status.setMenu_(None)
        else:
            self.toggle_icon()

    def showAbout_(self, sender):
        if self.about is None:
            self.build_about()
        self.about.center()
        self.about.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def build_about(self):
        W, H = 520, 268
        w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (W, H)), 3, NSBackingStoreBuffered, False)
        w.setTitle_("About EmojiBar")
        w.setReleasedWhenClosed_(False)
        c = w.contentView()
        app_icon = None
        if str(NSBundle.mainBundle().bundleIdentifier()) == BUNDLE_ID:
            app_icon = NSApplication.sharedApplication().applicationIconImage()
        if app_icon is not None:
            iv0 = NSImageView.alloc().initWithFrame_(((40, 77), (136, 136)))
            iv0.setImage_(app_icon)
            iv0.setImageScaling_(3)
            c.addSubview_(iv0)
        else:
            tile = NSView.alloc().initWithFrame_(((40, 77), (136, 136)))
            tile.setWantsLayer_(True)
            tile.layer().setBackgroundColor_(
                NSColor.colorWithWhite_alpha_(TRAY_BG_WHITE, 1.0).CGColor())
            tile.layer().setCornerRadius_(30)
            img = self.stock_img or NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "face.smiling", None)
            glyph = None
            try:
                glyph = trimmed_template(img, 82)
            except Exception:
                traceback.print_exc()
            iv = NSImageView.alloc().initWithFrame_(((27, 27), (82, 82)))
            iv.setImage_(white_image(glyph if glyph is not None else img))
            iv.setImageScaling_(3)  # proportionally up or down
            tile.addSubview_(iv)
            c.addSubview_(tile)

        def label(text, frame, size=13, color=None, wrap=False, align=None):
            f = (NSTextField.wrappingLabelWithString_ if wrap
                 else NSTextField.labelWithString_)(text)
            f.setFrame_(frame)
            f.setFont_(NSFont.systemFontOfSize_(size))
            if color is not None:
                f.setTextColor_(color)
            if align is not None:
                f.setAlignment_(align)
            c.addSubview_(f)

        label("EmojiBar", ((217, 184), (280, 42)), 34)
        label("v%s, build %s" % (VERSION, BUILD), ((217, 167), (280, 16)), 12,
              NSColor.secondaryLabelColor())
        label("A small utility that adds the emoji picker to your Mac's "
              "Control Strip.", ((217, 122), (283, 36)), 13, None, True)
        label("Emoji icon: Apple (macOS Touch Bar)", ((217, 93), (283, 17)))
        label("Made in 2026 by m.k", ((0, 20), (W, 17)), 13,
              None, False, 2)
        self.about = w

    def toggleModal_(self, sender):
        self.use_modal = not self.use_modal
        sender.setState_(1 if self.use_modal else 0)
        log("host via modal bar:", self.use_modal)

    def toggleSetting_(self, sender):
        key = {0: "launch", 1: "dock"}[sender.tag()]
        self.settings[key] = bool(sender.state())
        save_settings(self.settings)
        if key == "launch":
            set_launch_agent(self.settings["launch"])
        else:
            self.apply_dock()

    def apply_dock(self):
        # 0 = Regular (Dock icon + Cmd-Tab), 1 = Accessory (menu bar only)
        NSApplication.sharedApplication().setActivationPolicy_(
            0 if self.settings["dock"] else 1)

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
        self.showSettings_(None)  # clicking the Dock icon opens Settings
        return True

    def toggle_icon(self):
        self.settings["icon"] = not self.settings["icon"]
        save_settings(self.settings)
        self.assert_presence()

    def hotkey_label(self, key):
        hk = self.settings.get(key)
        return hk["label"] if hk else "Click to set"

    def showSettings_(self, sender):
        if self.win is None:
            self.build_settings()
        self.cb_launch.setState_(1 if self.settings["launch"] else 0)
        self.cb_dock.setState_(1 if self.settings["dock"] else 0)
        for key, btn in self.rec_buttons.items():
            btn.setTitle_(self.hotkey_label(key))
        self.win.center()
        self.win.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def build_settings(self):
        W, H = 360, 184
        self.win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (W, H)), 3, NSBackingStoreBuffered, False)  # titled|closable
        self.win.setTitle_("Settings")
        self.win.setReleasedWhenClosed_(False)
        content = self.win.contentView()
        for text, y in (("Open on launch:", 134), ("Show in Dock:", 100),
                        ("Picker shortcut:", 66),
                        ("Toggle 5th icon shortcut:", 32)):
            f = NSTextField.labelWithString_(text)
            f.setFrame_(((20, y), (190, 20)))
            content.addSubview_(f)
        self.cb_launch = NSButton.checkboxWithTitle_target_action_(
            "", self, b"toggleSetting:")
        self.cb_launch.setFrame_(((220, 134), (30, 20)))
        content.addSubview_(self.cb_launch)
        self.cb_dock = NSButton.checkboxWithTitle_target_action_(
            "", self, b"toggleSetting:")
        self.cb_dock.setTag_(1)
        self.cb_dock.setFrame_(((220, 100), (30, 20)))
        content.addSubview_(self.cb_dock)
        self.rec_buttons = {}
        for key, tag, y in (("hotkey", 10, 62), ("icon_hotkey", 11, 28)):
            btn = NSButton.buttonWithTitle_target_action_(
                "", self, b"recordShortcut:")
            btn.setTag_(tag)
            btn.setFrame_(((216, y), (124, 28)))
            content.addSubview_(btn)
            self.rec_buttons[key] = btn

    def recordShortcut_(self, sender):
        self.rec_key = {10: "hotkey", 11: "icon_hotkey"}[sender.tag()]
        for key, btn in self.rec_buttons.items():
            btn.setTitle_("Press keys\u2026" if key == self.rec_key
                          else self.hotkey_label(key))
        if self.rec_monitor is None:
            self.rec_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                1 << 10, lambda e: self.recHandler_(e))

    def recHandler_(self, event):
        key = self.rec_key
        code = int(event.keyCode())
        flags = int(event.modifierFlags()) & MOD_MASK
        if code == 53:                       # Esc: cancel
            hk = self.settings.get(key)
        elif code == 51 and not flags:       # Delete: clear
            hk = None
        elif flags & ((1 << 18) | (1 << 19) | (1 << 20)):
            label = (("\u2303" if flags & (1 << 18) else "")
                     + ("\u2325" if flags & (1 << 19) else "")
                     + ("\u21e7" if flags & (1 << 17) else "")
                     + ("\u2318" if flags & (1 << 20) else "")
                     + str(event.charactersIgnoringModifiers()).upper())
            hk = {"code": code, "flags": flags, "label": label}
        else:
            return None                      # needs a modifier; keep waiting
        self.settings[key] = hk
        save_settings(self.settings)
        NSEvent.removeMonitor_(self.rec_monitor)
        self.rec_monitor = None
        self.rec_buttons[key].setTitle_(self.hotkey_label(key))
        self.apply_hotkey()
        return None

    def apply_hotkey(self):
        if self.hotkey_monitor is not None:
            NSEvent.removeMonitor_(self.hotkey_monitor)
            self.hotkey_monitor = None
        hks = [(h, f) for h, f in (
            (self.settings.get("hotkey"), lambda: self.showPicker_(None)),
            (self.settings.get("icon_hotkey"), self.toggle_icon)) if h]
        if not hks:
            return

        def handler(event):
            code = int(event.keyCode())
            flags = int(event.modifierFlags()) & MOD_MASK
            for h, f in hks:
                if code == h["code"] and flags == h["flags"]:
                    f()

        self.hotkey_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            1 << 10, handler)

    def set_menubar_icon(self, img):
        try:
            m = None
            try:
                m = trimmed_template(img, MENUBAR_ICON_SIZE)
            except Exception:
                traceback.print_exc()
            if m is None:
                m = img.copy()
                sz = img.size()
                m.setSize_((sz.width * MENUBAR_ICON_SIZE / sz.height
                            if sz.height else MENUBAR_ICON_SIZE,
                            MENUBAR_ICON_SIZE))
                m.setTemplate_(True)
            log("menu bar icon size:", m.size())
            b = self.status.button()
            b.setTitle_("")
            b.setImage_(m)
        except Exception:
            traceback.print_exc()

    def toggleBatch_(self, sender):
        self.batch = not self.batch
        sender.setState_(1 if self.batch else 0)
        log("batch mode:", self.batch)

    # ---- hidden responder -----------------------------------------------
    def setup_panel(self):
        self.panel = KeyPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (10, 10)), NONACTIVATING_PANEL, NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setAlphaValue_(0.0)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setOpaque_(False)
        self.tv = NSTextView.alloc().initWithFrame_(((0, 0), (10, 10)))
        self.tv.setDelegate_(self)
        self.panel.setContentView_(self.tv)

    def textDidChange_(self, note):
        text = str(self.tv.string())
        if not text:
            return
        self.tv.setString_("")
        if self.prev_app is None:
            log("picker delivered", repr(text), "but no previous app")
            return
        bid = str(self.prev_app.bundleIdentifier())
        pid = self.prev_app.processIdentifier()
        if bid in FOCUS_PASTE_BUNDLES:
            self.focus_insert(text, bid, pid)
        else:
            self.smart_insert(text, bid, pid)

    def flush_pending(self):
        text = "".join(self.pending)
        self.pending = []
        if not text or CGEventPost is None:
            return
        log("pasting queued", repr(text))
        pb = NSPasteboard.generalPasteboard()
        old = pb.stringForType_(NSPasteboardTypeString)
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)

        def press(t):
            for down in (True, False):
                ev = CGEventCreateKeyboardEvent(None, 9, down)
                CGEventSetFlags(ev, kCGEventFlagMaskCommand)
                CGEventPost(kCGHIDEventTap, ev)

        def restore(t):
            if old is not None:
                pb.clearContents()
                pb.setString_forType_(old, NSPasteboardTypeString)

        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.3, False, press)
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.7, False, restore)

    def focus_insert(self, text, bid, pid):
        """Apps that need real focus: CDP -> verified AX -> batch / blink."""
        if insert_via_cdp(text, bid):
            log("inserted via CDP:", repr(text))
        elif insert_via_ax_verified(text, pid):
            log("inserted via verified AX:", repr(text))
        elif self.batch:
            self.pending.append(text)
            log("queued", repr(text), "- pasted when picker closes")
        else:
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.0, False, lambda t: self.fast_flash(text))

    def smart_insert(self, text, bid, pid):
        """Normal apps: AX set, then key event, each verified when the field's
        value is readable. If neither works, learn the app as focus-paste."""
        el, before = ax_focused_value(pid)
        if el is None:
            deliver(text, pid)
            log("(could not verify insertion into", bid + ")")
            return
        err = AXUIElementSetAttributeValue(el, kAXSelectedTextAttribute, text)
        if err == 0 and ax_changed(el, before):
            log("inserted via AX (verified):", repr(text))
            return
        type_via_event(text, pid)
        if ax_changed(el, before):
            log("typed via key event (verified):", repr(text))
            return
        log("insertion not confirmed for", bid, "- learning as focus-paste app")
        self.learn_focus_app(bid)
        self.focus_insert(text, bid, pid)

    def learn_focus_app(self, bid):
        if bid and bid not in FOCUS_PASTE_BUNDLES:
            FOCUS_PASTE_BUNDLES.add(bid)
            save_learned()
            log("learned focus-paste app:", bid)

    def markLast_(self, sender):
        if self.prev_app is None:
            log("no last target app")
            return
        self.learn_focus_app(str(self.prev_app.bundleIdentifier()))

    def forgetLearned_(self, sender):
        FOCUS_PASTE_BUNDLES.intersection_update(_FOCUS_DEFAULTS)
        save_learned()
        log("forgot learned apps")

    def fast_flash(self, text):
        """Blink to the target app, Cmd+V, blink back. The popover is NOT
        dismissed, so it usually reappears as soon as this app is frontmost."""
        if CGEventPost is None or self.prev_app is None:
            log("fast-flash unavailable")
            return
        log("fast-flash", repr(text))
        self.flashing = True
        pb = NSPasteboard.generalPasteboard()
        old = pb.stringForType_(NSPasteboardTypeString)
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        self.prev_app.activateWithOptions_(1 << 1)

        def press(t):
            for down in (True, False):
                ev = CGEventCreateKeyboardEvent(None, 9, down)
                CGEventSetFlags(ev, kCGEventFlagMaskCommand)
                CGEventPost(kCGHIDEventTap, ev)

        def back(t):
            if old is not None:
                pb.clearContents()
                pb.setString_forType_(old, NSPasteboardTypeString)
            self.panel.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self.panel.makeFirstResponder_(self.tv)
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.2, False, lambda t2: self.after_flash())

        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            FLASH_PRESS, False, press)
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            FLASH_BACK, False, back)

    def after_flash(self):
        try:
            if self.picker_item is None or not self.picker_item.isPresented():
                log("popover gone after blink; reopening")
                self.open_popover()
        except Exception:
            traceback.print_exc()
        self.t0 = time.time()
        self.seen_presented = False
        self.flashing = False

    def focus_paste(self, text):
        """For apps that need real focus (Electron etc.): go back to the app,
        Cmd+V, restore the clipboard (plain text only), reopen the picker."""
        if CGEventPost is None:
            log("focus-paste needs Quartz")
            return
        log("focus-paste", repr(text))
        pb = NSPasteboard.generalPasteboard()
        old = pb.stringForType_(NSPasteboardTypeString)
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        self.session = False
        try:
            if self.picker_item is not None and self.picker_item.isPresented():
                self.picker_item.dismissPopover_(None)
        except Exception:
            traceback.print_exc()
        self.panel.orderOut_(None)
        self.prev_app.activateWithOptions_(1 << 1)

        def press(t):
            for down in (True, False):
                ev = CGEventCreateKeyboardEvent(None, 9, down)
                CGEventSetFlags(ev, kCGEventFlagMaskCommand)
                CGEventPost(kCGHIDEventTap, ev)

        def restore(t):
            if old is not None:
                pb.clearContents()
                pb.setString_forType_(old, NSPasteboardTypeString)
            self.showPicker_(None)

        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.25, False, press)
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.6, False, restore)

    # ---- touch bar ------------------------------------------------------
    def make_bar(self):
        manual = NSTouchBar.alloc().init()
        manual.setDefaultItemIdentifiers_([PICKER_ID])
        try:
            bar = self.tv.touchBar()
        except Exception:
            traceback.print_exc()
            return manual
        if bar is None:
            log("text view gave no touch bar; using manual bar")
            return manual
        ids = [str(i) for i in bar.defaultItemIdentifiers()]
        if PICKER_ID not in ids:
            log("picker id not in text view bar:", ids)
            return manual
        bar.setDefaultItemIdentifiers_([PICKER_ID])
        return bar

    def setup_tray(self):
        self.ident_ptr = make_nsstring_ptr(TRAY_ID)
        self.item = NSCustomTouchBarItem.alloc().initWithIdentifier_(TRAY_ID)
        self.bar = self.make_bar()
        img = None
        try:
            stock = self.bar.itemForIdentifier_(PICKER_ID)
            if stock is not None:
                img = stock.collapsedRepresentationImage()
                log("stock icon:", img, "label:",
                    stock.collapsedRepresentationLabel())
        except Exception:
            traceback.print_exc()
        if img is None:
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "face.smiling", None)
        if img is None:
            img = NSImage.imageNamed_("NSTouchBarComposeTemplate")
        self.set_menubar_icon(img)
        self.stock_img = img
        self.button = NSButton.buttonWithImage_target_action_(
            white_image(img), self, b"showPicker:")
        self.button.setBordered_(False)   # no rounded bezel
        self.button.setImagePosition_(1)  # NSImageOnly
        self.button.setContentTintColor_(NSColor.whiteColor())
        self.button.setWantsLayer_(True)
        layer = self.button.layer()
        layer.setBackgroundColor_(
            NSColor.colorWithWhite_alpha_(TRAY_BG_WHITE, 1.0).CGColor())
        layer.setCornerRadius_(TRAY_CORNER)
        self.item.setView_(self.button)
        NSTouchBarItem.addSystemTrayItem_(self.item)
        self.assert_presence()
        dfr.DFRSystemModalShowsCloseBoxWhenFrontMost(True)

    def assert_presence(self):
        dfr.DFRElementSetControlStripPresenceForIdentifier(
            self.ident_ptr, bool(self.settings["icon"]))

    # ---- session --------------------------------------------------------
    def showPicker_(self, sender):
        if self.session:
            self.end_session()
            return
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None and front.processIdentifier() != os.getpid():
            self.prev_app = front
        log("start; previous app:",
            self.prev_app.localizedName() if self.prev_app else None,
            "bundle:", self.prev_app.bundleIdentifier() if self.prev_app else None)
        self.panel.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.makeFirstResponder_(self.tv)
        if self.use_modal:
            getattr(NSTouchBar, PRESENT)(self.bar, 1, TRAY_ID)
        self.session = True
        self.seen_presented = False
        self.t0 = time.time()
        delay = 0.4 if self.use_modal else 0.12
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            delay, False, lambda t: self.open_popover())

    def open_popover(self):
        try:
            item = self.bar.itemForIdentifier_(PICKER_ID)
            if item is None:
                log("no picker item")
                self.end_session()
                return
            item.setShowsCloseButton_(True)
            self.picker_item = item
            item.showPopover_(None)
            log("popover opened", round(time.time() - self.t0, 2), "s after tap")
            if self.use_modal:
                NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                    0.3, False, lambda t: self.dismiss_modal())
        except Exception:
            traceback.print_exc()
            self.end_session()

    def dismiss_modal(self):
        if NSTouchBar.respondsToSelector_(b"dismissSystemModalTouchBar:"):
            NSTouchBar.dismissSystemModalTouchBar_(self.bar)

    def dismissPicker_(self, sender):
        self.end_session()

    def end_session(self):
        log("end session")
        self.session = False
        try:
            if self.picker_item is not None and self.picker_item.isPresented():
                self.picker_item.dismissPopover_(None)
        except Exception:
            traceback.print_exc()
        self.dismiss_modal() if self.use_modal else None
        self.panel.orderOut_(None)
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None or front.processIdentifier() == os.getpid():
            if self.prev_app is not None:
                self.prev_app.activateWithOptions_(1 << 1)  # IgnoringOtherApps
                self.flush_pending()
        else:
            if self.pending:
                log("discarding queued emoji:", repr("".join(self.pending)))
            self.pending = []
            log("not restoring focus; frontmost is", front.localizedName())
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.5, False, lambda t: self.reassert())

    def reassert(self):
        try:
            NSTouchBarItem.addSystemTrayItem_(self.item)
            self.assert_presence()
        except Exception:
            traceback.print_exc()

    def poll(self):
        self.ticks += 1
        if self.ticks % 8 == 0 and not self.session:
            try:
                self.assert_presence()
            except Exception:
                pass
        if self.flashing or not self.session or time.time() - self.t0 < 0.8:
            return
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if (front is not None and front.processIdentifier() != os.getpid()
                and time.time() - self.t0 > 1.5):
            log("another app took focus; ending session")
            self.end_session()
            return
        try:
            presented = bool(self.picker_item is not None
                             and self.picker_item.isPresented())
        except Exception:
            presented = False
        if presented:
            self.seen_presented = True
        elif self.seen_presented or time.time() - self.t0 > 3.0:
            log("popover closed")
            self.end_session()


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    ctrl = Controller.alloc().init()
    app.setDelegate_(ctrl)

    def on_sigint(signum, frame):
        log("Ctrl+C, quitting")
        app.terminate_(None)

    signal.signal(signal.SIGINT, on_sigint)
    app.run()


if __name__ == "__main__":
    main()
