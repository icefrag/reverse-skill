# ldmem.py — LDPlayer host-side guest memory access (Windows ReadProcessMemory)
import ctypes
from ctypes import wintypes as wt

k32 = ctypes.windll.kernel32

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000

PAGE_NOACCESS = 0x01


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


def open_vm(pid):
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}) failed: {ctypes.GetLastError()}")
    return h


def regions(h, min_size=0):
    out = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while True:
        r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not r:
            break
        base = mbi.BaseAddress or 0
        if mbi.State == MEM_COMMIT and mbi.Protect & 0xFF != PAGE_NOACCESS:
            if mbi.RegionSize >= min_size:
                out.append({
                    "base": base,
                    "size": mbi.RegionSize,
                    "protect": mbi.Protect,
                    "type": mbi.Type,
                    "alloc_base": mbi.AllocationBase or 0,
                })
        addr = base + mbi.RegionSize
        if addr >= 0x7FFFFFFEFFFF:
            break
    return out


def read(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t()
    ok = k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got))
    return (buf.raw[:got.value] if ok else None), got.value


def read_at(h, addr, size):
    data, got = read(h, addr, size)
    if data is None or got != size:
        data2, got2 = None, 0
        # page-wise fallback
        chunks = []
        off = 0
        while off < size:
            step = min(4096, size - off)
            d, g = read(h, addr + off, step)
            if d is None or g == 0:
                break
            chunks.append(d[:g])
            off += g
        data = b"".join(chunks)
        got = len(data)
    return data, got
