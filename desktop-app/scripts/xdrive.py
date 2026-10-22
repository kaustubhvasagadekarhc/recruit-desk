# Author: kaustubh.s.v
"""Drive the app window under XWayland with XTest (clicks and typing), no third-party modules.

usage: python3 scripts/xdrive.py <window-id hex> click X Y | type "text" | key Return
Coordinates are relative to the window.
"""
import ctypes, ctypes.util, sys, time

x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
xt = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xtst"))
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XDefaultRootWindow.restype = ctypes.c_ulong
x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XStringToKeysym.restype = ctypes.c_ulong
x11.XFlush.argtypes = [ctypes.c_void_p]
x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                      ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
xt.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
xt.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]

d = x11.XOpenDisplay(None)
root = x11.XDefaultRootWindow(d)
wid = int(sys.argv[1], 16)
SHIFTED = '~!@#$%^&*()_+{}|:"<>?ABCDEFGHIJKLMNOPQRSTUVWXYZ'
NAMES = {"@": "at", "=": "equal", "_": "underscore", "?": "question", " ": "space", ".": "period", ",": "comma", "-": "minus", "/": "slash", ":": "colon", "+": "plus",
         "\n": "Return", "(": "parenleft", ")": "parenright", "'": "apostrophe", ";": "semicolon"}


def key(name, shift=False):
    kc = x11.XKeysymToKeycode(d, x11.XStringToKeysym(name.encode()))
    sh = x11.XKeysymToKeycode(d, x11.XStringToKeysym(b"Shift_L"))
    if shift:
        xt.XTestFakeKeyEvent(d, sh, 1, 0)
    xt.XTestFakeKeyEvent(d, kc, 1, 0)
    xt.XTestFakeKeyEvent(d, kc, 0, 0)
    if shift:
        xt.XTestFakeKeyEvent(d, sh, 0, 0)
    x11.XFlush(d)
    time.sleep(0.01)


cmd = sys.argv[2]
if cmd == "click":
    rx, ry, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    x11.XTranslateCoordinates(d, wid, root, int(sys.argv[3]), int(sys.argv[4]), ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(child))
    xt.XTestFakeMotionEvent(d, -1, rx.value, ry.value, 0)
    x11.XFlush(d); time.sleep(0.1)
    xt.XTestFakeButtonEvent(d, 1, 1, 0); xt.XTestFakeButtonEvent(d, 1, 0, 0)
    x11.XFlush(d)
elif cmd == "scroll":  # scroll X Y N  (N < 0 scrolls up)
    rx, ry, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    x11.XTranslateCoordinates(d, wid, root, int(sys.argv[3]), int(sys.argv[4]), ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(child))
    xt.XTestFakeMotionEvent(d, -1, rx.value, ry.value, 0)
    n = int(sys.argv[5])
    for _ in range(abs(n)):
        b = 4 if n < 0 else 5
        xt.XTestFakeButtonEvent(d, b, 1, 0); xt.XTestFakeButtonEvent(d, b, 0, 0)
        x11.XFlush(d); time.sleep(0.02)
elif cmd == "type":
    for ch in sys.argv[3]:
        key(NAMES.get(ch, ch), shift=ch in SHIFTED)
elif cmd == "key":  # key Return | key ctrl+a
    parts = sys.argv[3].split("+")
    if len(parts) == 2 and parts[0] == "ctrl":
        c = x11.XKeysymToKeycode(d, x11.XStringToKeysym(b"Control_L"))
        xt.XTestFakeKeyEvent(d, c, 1, 0)
        key(parts[1])
        xt.XTestFakeKeyEvent(d, c, 0, 0)
        x11.XFlush(d)
    else:
        key(sys.argv[3])
time.sleep(0.2)
