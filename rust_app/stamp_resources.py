import ctypes
import struct
import sys
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

BeginUpdateResourceW = kernel32.BeginUpdateResourceW
BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
BeginUpdateResourceW.restype = ctypes.c_void_p

UpdateResourceW = kernel32.UpdateResourceW
UpdateResourceW.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_ushort,
    ctypes.c_void_p,
    ctypes.c_uint,
]
UpdateResourceW.restype = ctypes.c_int

EndUpdateResourceW = kernel32.EndUpdateResourceW
EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_int]
EndUpdateResourceW.restype = ctypes.c_int

LoadLibraryExW = kernel32.LoadLibraryExW
LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint]
LoadLibraryExW.restype = ctypes.c_void_p

FindResourceW = kernel32.FindResourceW
FindResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
FindResourceW.restype = ctypes.c_void_p

FreeLibrary = kernel32.FreeLibrary
FreeLibrary.argtypes = [ctypes.c_void_p]
FreeLibrary.restype = ctypes.c_int


def read_res_id(buf: bytes, off: int):
    w0 = struct.unpack_from("<H", buf, off)[0]
    if w0 == 0xFFFF:
        ordinal = struct.unpack_from("<H", buf, off + 2)[0]
        return ("ord", ordinal), off + 4
    chars = []
    while off + 2 <= len(buf):
        ch = struct.unpack_from("<H", buf, off)[0]
        off += 2
        if ch == 0:
            break
        chars.append(chr(ch))
    return ("str", "".join(chars)), off


def align4(n: int) -> int:
    return (n + 3) & ~3


def parse_res_file(data: bytes):
    off = 0
    entries = []
    while off + 8 <= len(data):
        data_size, hdr_size = struct.unpack_from("<II", data, off)
        if hdr_size < 8 or off + hdr_size + data_size > len(data):
            break
        cur = off + 8
        r_type, cur = read_res_id(data, cur)
        r_name, cur = read_res_id(data, cur)
        cur = align4(cur)
        # DataVersion(4), MemoryFlags(2), LanguageId(2), Version(4), Characteristics(4)
        _, _, lang_id, _, _ = struct.unpack_from("<IHHII", data, cur)
        payload = data[off + hdr_size : off + hdr_size + data_size]
        if not (data_size == 0 and r_type == ("ord", 0) and r_name == ("ord", 0)):
            entries.append((r_type, r_name, lang_id, payload))
        off = align4(off + hdr_size + data_size)
    return entries


def id_to_ptr(id_tuple):
    kind, val = id_tuple
    if kind == "ord":
        return ctypes.c_void_p(val), None
    buf = ctypes.create_unicode_buffer(val)
    return ctypes.cast(buf, ctypes.c_void_p), buf


def stamp(exe_path: Path, res_path: Path):
    entries = parse_res_file(res_path.read_bytes())
    if not entries:
        raise RuntimeError("No resources parsed from .res file")

    h_upd = BeginUpdateResourceW(str(exe_path), 0)
    if not h_upd:
        raise RuntimeError(f"BeginUpdateResourceW failed: {ctypes.get_last_error()}")

    for r_type, r_name, lang_id, payload in entries:
        p_type, _keep1 = id_to_ptr(r_type)
        p_name, _keep2 = id_to_ptr(r_name)
        raw_buf = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        ok = UpdateResourceW(h_upd, p_type, p_name, lang_id, ctypes.cast(raw_buf, ctypes.c_void_p), len(payload))
        if not ok:
            EndUpdateResourceW(h_upd, 1)
            raise RuntimeError(f"UpdateResourceW failed for {r_type}/{r_name}: {ctypes.get_last_error()}")

    if not EndUpdateResourceW(h_upd, 0):
        raise RuntimeError(f"EndUpdateResourceW failed: {ctypes.get_last_error()}")

    # Verify RT_GROUP_ICON(14), RT_VERSION(16), RT_MANIFEST(24) exist in PE!
    h_mod = LoadLibraryExW(str(exe_path), None, 0x00000002)  # LOAD_LIBRARY_AS_DATAFILE
    if not h_mod:
        raise RuntimeError("LoadLibraryExW verification failed")
    try:
        for rt in (14, 16, 24):
            hr = FindResourceW(h_mod, ctypes.c_void_p(1), ctypes.c_void_p(rt))
            if not hr:
                raise RuntimeError(f"Missing embedded resource type {rt}")
    finally:
        FreeLibrary(h_mod)

    print(f"STAMP_OK: {len(entries)} resources embedded & verified in {exe_path}")


if __name__ == "__main__":
    stamp(Path(sys.argv[1]), Path(sys.argv[2]))
