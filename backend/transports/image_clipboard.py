"""Short-lived bitmap clipboard ownership for the Windows transport."""

import io
from contextlib import contextmanager

from PIL import Image


@contextmanager
def pasted_bitmap(path):
    import ctypes

    import win32clipboard as clipboard
    import win32con

    with Image.open(path) as picture:
        buffer = io.BytesIO()
        picture.convert("RGB").save(buffer, "BMP")
    previous = []
    clipboard.OpenClipboard()
    try:
        fmt = clipboard.EnumClipboardFormats(0)
        while fmt:
            value = clipboard.GetClipboardData(fmt)
            # Opaque handles cannot be restored reliably. Abort before replacing
            # the clipboard rather than destroying another application's data.
            if not isinstance(value, (bytes, str)):
                raise TypeError("clipboard format cannot be preserved")
            previous.append((fmt, value))
            fmt = clipboard.EnumClipboardFormats(fmt)
        clipboard.EmptyClipboard()
        clipboard.SetClipboardData(win32con.CF_DIB, buffer.getvalue()[14:])
    finally:
        clipboard.CloseClipboard()
    sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
    try:
        yield
    finally:
        clipboard.OpenClipboard()
        try:
            # Never overwrite something the user copied while generation sent.
            if ctypes.windll.user32.GetClipboardSequenceNumber() == sequence:
                clipboard.EmptyClipboard()
                for fmt, value in previous:
                    clipboard.SetClipboardData(fmt, value)
        finally:
            clipboard.CloseClipboard()
