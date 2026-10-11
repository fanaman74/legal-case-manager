"""Encrypting AI provider API keys at rest.

On Windows the key is sealed with DPAPI for the account the web app runs
as, so the database file alone (in a backup, say) is useless: only that
account on this computer can open it. Elsewhere (development, CI) it is
encrypted with a key kept in the data folder's .run directory, readable
only by its owner.

Keys are never logged, never returned to the browser, and only decrypted
just before a call to the provider."""

import os
import sys
from pathlib import Path

DPAPI, FERNET = b"dpapi:", b"fernet:"


class SecretError(Exception):
    """A saved key can't be opened (moved to another computer or account)."""


def protect(plain: str, run_dir: Path) -> bytes:
    data = plain.encode("utf-8")
    if sys.platform == "win32":
        return DPAPI + _dpapi(data, encrypt=True)
    return FERNET + _fernet(run_dir).encrypt(data)


def unprotect(blob: bytes, run_dir: Path) -> str:
    try:
        if blob.startswith(DPAPI):
            if sys.platform != "win32":
                raise SecretError("sealed on Windows")
            return _dpapi(blob[len(DPAPI):], encrypt=False).decode("utf-8")
        if blob.startswith(FERNET):
            return _fernet(run_dir).decrypt(blob[len(FERNET):]).decode("utf-8")
    except SecretError:
        raise
    except Exception as exc:
        raise SecretError(type(exc).__name__) from None
    raise SecretError("unknown format")


def hint(plain: str) -> str:
    """The last four characters, so the Admin can tell which key is saved."""
    plain = plain.strip()
    return plain[-4:] if len(plain) >= 12 else ""


# --- Windows: DPAPI ---------------------------------------------------------------

_ENTROPY = b"Case File Manager AI provider key"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> Blob:
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    src, entropy, out = blob(data), blob(_ENTROPY), Blob()
    UI_FORBIDDEN = 0x1
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(entropy), None, None, UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecretError(f"DPAPI error {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# --- elsewhere: a key file ----------------------------------------------------------


def _fernet(run_dir: Path):
    from cryptography.fernet import Fernet

    path = run_dir / "secret.key"
    if not path.exists():
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # created meanwhile by the other process
            pass
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())
