# Author: kaustubh.s.v
"""Screenshot one X11 window (the app under XWayland) to PNG, with no third-party modules.

usage: python3 scripts/xshot.py <window-id hex> <out.png>   (DISPLAY must point at the X server)
"""
import ctypes, ctypes.util, struct, sys, zlib

x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XGetImage.restype = ctypes.c_void_p
x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
x11.XGetPixel.restype = ctypes.c_ulong
x11.XGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]


class XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int)] + [("pad", ctypes.c_char * 256)]


class XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int), ("format", ctypes.c_int),
                ("data", ctypes.POINTER(ctypes.c_ubyte)), ("byte_order", ctypes.c_int), ("bitmap_unit", ctypes.c_int),
                ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
                ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int)]


def main():
    wid, out = int(sys.argv[1], 16), sys.argv[2]
    d = x11.XOpenDisplay(None)
    attrs = XWindowAttributes()
    x11.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    x11.XGetWindowAttributes(d, wid, ctypes.byref(attrs))
    w, h = attrs.width, attrs.height
    img_p = x11.XGetImage(d, wid, 0, 0, w, h, 0xFFFFFFFF, 2)  # ZPixmap
    if not img_p:
        sys.exit("XGetImage failed")
    img = XImage.from_address(img_p)
    bpl = img.bytes_per_line
    raw = ctypes.string_at(img.data, bpl * h)
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        line = raw[y * bpl:y * bpl + w * 4]
        # BGRX little-endian -> RGB
        px = bytearray(w * 3)
        px[0::3] = line[2::4]
        px[1::3] = line[1::4]
        px[2::3] = line[0::4]
        rows += px

    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    open(out, "wb").write(png)
    print(f"{out}: {w}x{h}")


main()
