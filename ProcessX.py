
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import psutil
import json
import os
import sys
import ctypes
import ctypes.wintypes
import subprocess
import threading
import time
import base64
import io
import re
import atexit
from pathlib import Path

try:
    import pystray
    from PIL import Image as PILImage
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False


CONFIG_FILE = Path(os.environ.get("APPDATA", ".")) / "ProcessX" / "rules.json"
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)


_ICON_BYTES_CACHE = None
_ICON_CACHE_FILE  = CONFIG_FILE.parent / "icon_cache.png"

def _icon_bytes() -> bytes:
    global _ICON_BYTES_CACHE
    if _ICON_BYTES_CACHE is not None:
        return _ICON_BYTES_CACHE

    try:
        if _ICON_CACHE_FILE.exists():
            data = _ICON_CACHE_FILE.read_bytes()
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                _ICON_BYTES_CACHE = data
                return _ICON_BYTES_CACHE
    except Exception:
        pass

    import struct, zlib, math

    W = H = 256
    SS = 3
    S = W / 680.0
    pixels = bytearray(W * H * 4)

    def rect_corners(rx, ry, rw, rh, cx_r, cy_r, angle_deg):
        a = math.radians(angle_deg)
        ca, sa = math.cos(a), math.sin(a)
        local = [(rx, ry), (rx+rw, ry), (rx+rw, ry+rh), (rx, ry+rh)]
        corners = []
        for lx, ly in local:
            dx, dy = lx - cx_r, ly - cy_r
            corners.append(((ca*dx - sa*dy + cx_r) * S,
                             (sa*dx + ca*dy + cy_r) * S))
        return corners

    def point_in_quad(x, y, corners):
        for i in range(4):
            ax, ay = corners[i]; bx, by = corners[(i+1) % 4]
            if (bx-ax)*(y-ay) - (by-ay)*(x-ax) < 0:
                return False
        return True

    oct_svg = [(213.5,52.5),(466.5,52.5),(627.5,213.5),(627.5,466.5),
               (466.5,627.5),(213.5,627.5),(52.5,466.5),(52.5,213.5)]
    oct_px  = [(x*S, y*S) for x, y in oct_svg]

    def in_oct(x, y):
        inside = False; j = len(oct_px)-1
        for i, (xi, yi) in enumerate(oct_px):
            xj, yj = oct_px[j]
            if ((yi > y) != (yj > y)) and (x < (xj-xi)*(y-yi)/(yj-yi)+xi):
                inside = not inside
            j = i
        return inside

    bar1 = rect_corners(112.3, 244.6, 455.4, 94.3, 340, 291.7,  39.5)
    bar2 = rect_corners(112.3, 341.2, 455.4, 94.3, 340, 388.3, -39.5)
    sq   = rect_corners(312.4, 312.4,  55.2,  55.2, 340, 340,   45.0)

    xs = [p[0] for p in oct_px]; ys = [p[1] for p in oct_px]
    px0, px1 = max(0, int(min(xs))-1), min(W, int(max(xs))+2)
    py0, py1 = max(0, int(min(ys))-1), min(H, int(max(ys))+2)

    offs = [(i + 0.5) / SS for i in range(SS)]
    total_samples = SS * SS

    for py in range(py0, py1):
        for px in range(px0, px1):
            rs = gs = bs = 0.0
            cnt = 0
            for oy in offs:
                y = py + oy
                for ox in offs:
                    x = px + ox
                    if not in_oct(x, y):
                        continue
                    if point_in_quad(x, y, sq):
                        r, g, b = 0, 0, 0
                    elif point_in_quad(x, y, bar1) or point_in_quad(x, y, bar2):
                        r, g, b = 255, 255, 255
                    else:
                        r, g, b = 0, 0, 0
                    rs += r; gs += g; bs += b
                    cnt += 1
            if cnt == 0:
                continue
            i = (py * W + px) * 4
            pixels[i]   = int(rs / cnt + 0.5)
            pixels[i+1] = int(gs / cnt + 0.5)
            pixels[i+2] = int(bs / cnt + 0.5)
            pixels[i+3] = int(255 * cnt / total_samples + 0.5)

    def _chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(pixels[r*W*4:(r+1)*W*4]) for r in range(H))
    _ICON_BYTES_CACHE = (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))

    try:
        _ICON_CACHE_FILE.write_bytes(_ICON_BYTES_CACHE)
    except Exception:
        pass

    return _ICON_BYTES_CACHE

def _make_tk_icon(root, size=None) -> "tk.PhotoImage | None":
    try:
        from PIL import Image as _PIL, ImageTk
        img = _PIL.open(io.BytesIO(_icon_bytes()))
        img = img.convert("RGBA")
        if size:
            img = img.resize((size, size), _PIL.LANCZOS)
        return ImageTk.PhotoImage(img, master=root)
    except Exception:
        return None

def _make_pil_icon() -> "PILImage.Image | None":
    try:
        img = PILImage.open(io.BytesIO(_icon_bytes()))
        img = img.convert("RGBA")
        return img.copy()
    except Exception:
        return None

CPU_COUNT         = psutil.cpu_count(logical=True)
PHYSICAL_COUNT    = psutil.cpu_count(logical=False) or CPU_COUNT


def _detect_cpu_topology() -> dict:
    result = {
        "vendor": "unknown", "brand": "",
        "p_core_count": 0, "e_core_count": 0, "e_thread_start": -1,
        "has_ccd1": False, "ccd1_start": 16,
        "ht_enabled": True,
        "e_cores_disabled_in_bios": False,
        "core_map": None,
    }
    def _parse_vendor_brand(name: str, mfr: str, res: dict):
        res["brand"] = name
        n, m = name.lower(), mfr.lower()
        if "intel" in m or "intel" in n:
            res["vendor"] = "intel"
        elif "amd" in m or "amd" in n:
            res["vendor"] = "amd"

    try:
        r = subprocess.run(
            ["wmic", "cpu", "get", "Name,Manufacturer", "/format:csv"],
            capture_output=True, text=True, creationflags=0x08000000,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Node"):
                continue
            parts = line.split(",")
            if len(parts) >= 3:
                _parse_vendor_brand(parts[2].strip(), parts[1].strip(), result)
                break
    except Exception:
        pass

    if result["vendor"] == "unknown":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            mfr  = winreg.QueryValueEx(key, "VendorIdentifier")[0]
            winreg.CloseKey(key)
            _parse_vendor_brand(name, mfr, result)
        except Exception:
            pass

    if result["vendor"] == "unknown":
        try:
            import platform
            brand = platform.processor()
            _parse_vendor_brand(brand, brand, result)
            if not result["brand"]:
                result["brand"] = brand
        except Exception:
            pass

    if result["vendor"] == "unknown":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-WmiObject Win32_Processor | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, creationflags=0x08000000,
            )
            name = r.stdout.strip()
            if name:
                _parse_vendor_brand(name, name, result)
        except Exception:
            pass


    if result["vendor"] == "intel":
        try:
            _RelationProcessorCore = 0
            kernel32 = ctypes.windll.kernel32

            _needed = ctypes.c_ulong(0)
            kernel32.GetLogicalProcessorInformationEx(
                _RelationProcessorCore, None, ctypes.byref(_needed))

            if _needed.value > 0:
                _glpie_buf = (ctypes.c_byte * _needed.value)()
                _ok = kernel32.GetLogicalProcessorInformationEx(
                    _RelationProcessorCore,
                    _glpie_buf, ctypes.byref(_needed))

                if _ok:
                    _data   = bytes(_glpie_buf)
                    _offset = 0
                    _ht_seen_on  = False
                    _ht_seen_off = False
                    while _offset + 8 <= _needed.value:
                        _rec_size = int.from_bytes(_data[_offset+4:_offset+8], "little")
                        if _rec_size < 8 or _offset + _rec_size > _needed.value:
                            break
                        _flags = _data[_offset + 8] if _offset + 8 < len(_data) else 0
                        if _flags & 1:
                            _ht_seen_on = True
                        else:
                            _ht_seen_off = True
                        _offset += _rec_size

                    if _ht_seen_off and not _ht_seen_on:
                        result["ht_enabled"] = False
        except Exception:
            pass

        try:
            kernel32 = ctypes.windll.kernel32
            BUF_SIZE = 8192
            buf      = (ctypes.c_byte * BUF_SIZE)()
            returned = ctypes.c_ulong(0)
            ok = kernel32.GetSystemCpuSetInformation(
                buf, BUF_SIZE, ctypes.byref(returned), None, 0)
            if ok:
                data = bytes(buf)

                def _parse_efficiency(eff_offset):
                    efficiency = []
                    offset = 0
                    while offset + 12 <= returned.value:
                        size     = int.from_bytes(data[offset:offset+4], "little")
                        cpu_type = int.from_bytes(data[offset+4:offset+8], "little")
                        if size < 12 or offset + size > returned.value:
                            break
                        if cpu_type == 0:
                            pos = offset + eff_offset
                            eff = data[pos] if pos < len(data) else 1
                            efficiency.append(eff)
                        offset += size
                    return efficiency

                best = None
                for eff_offset in (10, 11, 12, 13, 14, 16, 20, 24):
                    efficiency = _parse_efficiency(eff_offset)
                    if not efficiency:
                        continue
                    p = sum(1 for e in efficiency if e > 0)
                    e = sum(1 for e in efficiency if e == 0)
                    if p > 0 and e > 0:
                        total = p + e
                        if total == CPU_COUNT and p <= total * 0.75 and e <= total * 0.875:
                            best = efficiency
                            break

                if best:
                    p_threads = sum(1 for e in best if e > 0)
                    e_threads = sum(1 for e in best if e == 0)
                    e_start   = next((i for i, e in enumerate(best) if e == 0), -1)
                    if 0 < e_start < CPU_COUNT - 1:
                        result["p_core_count"]   = p_threads
                        result["e_core_count"]   = e_threads
                        result["e_thread_start"] = e_start
                        result["core_map"] = [e == 0 for e in best]
        except Exception:
            pass


        _known_hybrid_skus = {
            "270k plus": (8, 16), "270kf plus": (8, 16), "270 plus": (8, 16),
            "250k plus": (6, 12), "250kf plus": (6, 12), "250 plus": (6, 12),
            "285k":  (8, 16), "285kf": (8, 16), "285":   (8, 16),
            "265k":  (8, 12), "265kf": (8, 12), "265":   (8, 12),
            "255k":  (6, 8),  "255kf": (6, 8),  "255":   (6, 8),
            "245k":  (6, 8),  "245kf": (6, 8),  "245":   (6, 8),
        }

        _brand_raw   = result["brand"].lower()
        _brand_clean = _brand_raw.replace("(tm)", "").replace("(r)", "").replace("  ", " ")

        if "ultra" in _brand_clean or "ultra" in _brand_raw:
            result["ht_enabled"] = False

        def _sku_match(brand_raw, brand_clean, sku_table):
            for key, counts in sku_table.items():
                if key in brand_clean or key in brand_raw:
                    return counts
            return None

        _sku_hit = _sku_match(_brand_raw, _brand_clean, _known_hybrid_skus)
        if _sku_hit:
            _p, _e = _sku_hit
            result["p_core_count"]   = _p
            result["e_core_count"]   = _e
            result["e_thread_start"] = _p

        def _build_8p_zone_map(e_total):
            if e_total < 2 or e_total % 2 != 0:
                return None
            e1 = e2 = e_total // 2
            return ([False] * 2 + [True] * e1 + [False] * 4
                     + [True] * e2 + [False] * 2)

        if result["core_map"] is None and "ultra" in (_brand_clean + _brand_raw):
            if result["p_core_count"] == 8 and result["e_core_count"] > 0:
                _zone_map = _build_8p_zone_map(result["e_core_count"])
                if _zone_map and len(_zone_map) == CPU_COUNT:
                    result["core_map"] = _zone_map

        if result["p_core_count"] > 0:
            _expected_p_threads = result["p_core_count"]
            if CPU_COUNT <= _expected_p_threads:
                result["e_cores_disabled_in_bios"] = True

        if result["e_core_count"] == 0 and PHYSICAL_COUNT and CPU_COUNT:
            brand = _brand_raw
            hybrid_keywords = ["12th", "13th", "14th", "core ultra",
                                "raptor lake", "alder lake", "meteor lake",
                                "arrow lake", "lunar lake", "panther lake",
                                "i9-13", "i7-13", "i5-13", "i9-14", "i7-14", "i5-14",
                                "i9-12", "i7-12", "i5-12",
                                "ultra 9 2", "ultra 7 2", "ultra 5 2", "ultra 3 2"]
            brand_clean = _brand_clean
            if any(k in brand_clean for k in hybrid_keywords) or any(k in brand for k in hybrid_keywords):
                if CPU_COUNT > PHYSICAL_COUNT:
                    p = CPU_COUNT - PHYSICAL_COUNT
                    e = PHYSICAL_COUNT - p
                    if p > 0 and e > 0:
                        result["p_core_count"]   = p * 2
                        result["e_core_count"]   = e
                        result["e_thread_start"] = p * 2
                elif PHYSICAL_COUNT >= 8:
                    p_cores = PHYSICAL_COUNT // 2
                    e_cores = PHYSICAL_COUNT - p_cores
                    result["p_core_count"]   = p_cores
                    result["e_core_count"]   = e_cores
                    result["e_thread_start"] = p_cores


    if result["vendor"] == "amd":
        result["has_ccd1"]    = CPU_COUNT >= 17
        result["ccd1_start"]  = 16

    return result

CPU_TOPOLOGY = _detect_cpu_topology()


_watcher_status_cb  = None
_watcher_refresh_cb = None
_watcher_lock      = threading.Lock()

def _rule_watcher_loop(get_rules_fn):
    last_pids: dict = {}

    while True:
        try:
            rules = get_rules_fn()
            if rules:

                want = set(rules.keys())
                found: dict = {}
                for p in psutil.process_iter(["name", "pid"]):
                    try:
                        n = (p.info["name"] or "").lower()
                        if n in want:
                            found.setdefault(n, []).append((p, p.info["pid"]))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                newly_applied = []
                paths_updated = False
                for exe, procs in found.items():
                    seen_pids = last_pids.get(exe, set())
                    current_pids = {pid for _, pid in procs}
                    for proc, pid in procs:
                        if pid not in seen_pids:
                            try:
                                apply_rule(proc, rules[exe])
                                newly_applied.append(exe)
                            except Exception:
                                pass
                            pp_guid = rules[exe].get("power_plan")
                            if pp_guid:
                                try:
                                    _power_plan_manager.on_process_seen(exe, pp_guid)
                                except Exception:
                                    pass
                    last_pids[exe] = current_pids

                    if procs and not rules[exe].get("exe_path"):
                        try:
                            rules[exe]["exe_path"] = procs[0][0].exe()
                            paths_updated = True
                        except Exception:
                            pass

                exited = []
                for exe in list(last_pids):
                    if exe not in found:
                        exited.append(exe)
                        del last_pids[exe]

                for exe in exited:
                    try:
                        _power_plan_manager.on_process_gone(exe)
                    except Exception:
                        pass

                if paths_updated:
                    try:
                        save_rules(rules)
                    except Exception:
                        pass

                status_parts = []
                if newly_applied:
                    status_parts.append("Auto-applied: " + ", ".join(sorted(set(newly_applied))))
                if exited:
                    status_parts.append("Closed: " + ", ".join(sorted(set(exited))))

                if status_parts and _watcher_status_cb:
                    try:
                        _watcher_status_cb(" | ".join(status_parts))
                    except Exception:
                        pass

                if (newly_applied or exited) and _watcher_refresh_cb:
                    try:
                        _watcher_refresh_cb()
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(2.0)

PRIORITY_MAP = {
    "Idle":         psutil.IDLE_PRIORITY_CLASS,
    "Below Normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "Normal":       psutil.NORMAL_PRIORITY_CLASS,
    "Above Normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    "High":         psutil.HIGH_PRIORITY_CLASS,
    "Realtime":     psutil.REALTIME_PRIORITY_CLASS,
}
PRIORITY_REVERSE = {v: k for k, v in PRIORITY_MAP.items()}

IO_PRIORITY_NAMES         = ["Very Low", "Low", "Normal", "High"]
IO_PRIORITY_NAMES_DISPLAY = ["Very Low", "Low", "Normal", "High"]

PROCESS_SET_INFORMATION = 0x0200
ProcessIoPriority = 33


BG       = "#111111"
PANEL    = "#1a1a1a"
ROW_ALT  = "#161616"
TEXT     = "#ffffff"
TEXT_DIM = "#888888"
BTN_BG   = "#2e2e2e"
BTN_FG   = "#ffffff"
SEL_BG   = "#3a3a3a"
BORDER   = "#2e2e2e"
FONT     = "Segoe UI"


_NtSIP = ctypes.windll.ntdll.NtSetInformationProcess
_NtSIP.restype  = ctypes.c_long
_NtSIP.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]


_NtQIP = ctypes.windll.ntdll.NtQueryInformationProcess
_NtQIP.restype  = ctypes.c_long
_NtQIP.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]

def get_io_priority(pid: int) -> int:
    try:
        kernel32 = ctypes.windll.kernel32
        hProcess = kernel32.OpenProcess(0x1000, False, pid)
        if not hProcess:
            hProcess = kernel32.OpenProcess(0x0400, False, pid)
        if not hProcess:
            return -1
        io_val = ctypes.c_ulong(0)
        status = _NtQIP(hProcess, 33, ctypes.byref(io_val), ctypes.sizeof(io_val), None)
        kernel32.CloseHandle(hProcess)
        if status == 0:
            return int(io_val.value)
        return -1
    except Exception:
        return -1


def _grant_increase_base_priority_privilege() -> bool:
    try:
        import tempfile
        cfg = Path(tempfile.gettempdir()) / "processx_secpol.cfg"
        db  = Path(tempfile.gettempdir()) / "processx_secedit.sdb"


        r1 = subprocess.run(
            ["secedit", "/export", "/cfg", str(cfg), "/quiet"],
            capture_output=True,
            creationflags=0x08000000,
        )
        if r1.returncode != 0 or not cfg.exists():
            return False

        text = cfg.read_text(encoding="utf-8", errors="ignore")


        if "SeIncreaseBasePriorityPrivilege" in text:
            lines = text.splitlines()
            new_lines = []
            for line in lines:
                if line.strip().startswith("SeIncreaseBasePriorityPrivilege"):

                    if "*S-1-5-32-544" not in line:
                        line = line.rstrip()
                        line = line + ",*S-1-5-32-544" if line.endswith("=") or "=" in line else line

                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            existing_sids = parts[1].strip()
                            if existing_sids:
                                line = f"{parts[0]}= {existing_sids},*S-1-5-32-544"
                            else:
                                line = f"{parts[0]}= *S-1-5-32-544"
                new_lines.append(line)
            text = "\n".join(new_lines)
        else:

            text = text.replace(
                "[Privilege Rights]",
                "[Privilege Rights]\nSeIncreaseBasePriorityPrivilege = *S-1-5-32-544"
            )

        cfg.write_text(text, encoding="utf-8")


        subprocess.run(
            ["secedit", "/configure", "/db", str(db), "/cfg", str(cfg),
             "/areas", "SECURITYPOLICY", "/quiet"],
            capture_output=True,
            creationflags=0x08000000,
        )


        for f in (cfg, db):
            try: f.unlink()
            except Exception: pass

        return True
    except Exception:
        return False


def _enable_privilege(privilege_name: str) -> bool:
    try:
        TOKEN_ADJUST_PRIVILEGES = 0x0020
        TOKEN_QUERY             = 0x0008
        SE_PRIVILEGE_ENABLED    = 0x00000002

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_ulong)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", ctypes.c_ulong),
                        ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        h_token = ctypes.c_void_p()
        if not ctypes.windll.advapi32.OpenProcessToken(
            ctypes.c_void_p(-1), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(h_token)
        ):
            return False

        luid = LUID()
        if not ctypes.windll.advapi32.LookupPrivilegeValueW(
            None, privilege_name, ctypes.byref(luid)
        ):
            ctypes.windll.kernel32.CloseHandle(h_token)
            return False

        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount           = 1
        tp.Privileges[0].Luid       = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED

        ctypes.windll.advapi32.AdjustTokenPrivileges(
            h_token, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None
        )
        err = ctypes.windll.kernel32.GetLastError()
        ctypes.windll.kernel32.CloseHandle(h_token)
        return err == 0
    except Exception:
        return False


threading.Thread(target=_grant_increase_base_priority_privilege, daemon=True).start()
_enable_privilege("SeIncreaseBasePriorityPrivilege")


def set_io_priority(pid: int, level: int) -> tuple:
    if not 0 <= level <= 3:
        return (False, f"bad level {level}")
    try:
        _enable_privilege("SeIncreaseBasePriorityPrivilege")

        hProcess = ctypes.windll.kernel32.OpenProcess(0x0200, False, pid)
        if not hProcess:
            hProcess = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, pid)
        if not hProcess:
            return (False, f"OpenProcess err={ctypes.windll.kernel32.GetLastError()}")

        io_val = ctypes.c_ulong(level)
        status = _NtSIP(hProcess, 33, ctypes.byref(io_val), ctypes.sizeof(io_val))
        ctypes.windll.kernel32.CloseHandle(hProcess)

        if status == 0:
            return (True, "OK")
        if status == -1073741727:
            return (False, "Restart ProcessX as Administrator to apply High I/O")
        return (False, f"NtStatus=0x{status & 0xFFFFFFFF:08X}")
    except Exception as e:
        return (False, str(e))


_cpu_set_ids: list = []

def _enumerate_cpu_set_ids() -> list:
    try:
        kernel32 = ctypes.windll.kernel32


        BUF_SIZE = 4096
        buf = (ctypes.c_byte * BUF_SIZE)()
        returned = ctypes.c_ulong(0)

        ok = kernel32.GetSystemCpuSetInformation(
            buf, BUF_SIZE, ctypes.byref(returned),
            None, 0
        )
        if not ok:
            return []

        ids = []
        offset = 0
        data = bytes(buf)
        while offset + 12 <= returned.value:
            size = int.from_bytes(data[offset:offset+4], "little")
            if size < 12 or offset + size > returned.value:
                break
            cpu_type = int.from_bytes(data[offset+4:offset+8], "little")
            cpu_id   = int.from_bytes(data[offset+8:offset+12], "little")
            if cpu_type == 0:
                ids.append(cpu_id)
            offset += size
        return ids
    except Exception:
        return []

_cpu_set_ids = _enumerate_cpu_set_ids()


_id_to_cpu_idx: dict = {v: i for i, v in enumerate(_cpu_set_ids)}


def set_cpu_sets(pid: int, cpu_indices: list) -> bool:
    if not (_cpu_set_ids and all(i < len(_cpu_set_ids) for i in cpu_indices)):
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        set_ids  = [_cpu_set_ids[i] for i in cpu_indices]
        arr      = (ctypes.c_uint32 * len(set_ids))(*set_ids)

        PROCESS_ALL_ACCESS                = 0x1F0FFF
        PROCESS_SET_LIMITED_INFORMATION   = 0x2000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_SET_INFORMATION           = 0x0200
        PROCESS_QUERY_INFORMATION         = 0x0400

        hProc = None
        for access in (
            PROCESS_ALL_ACCESS,
            PROCESS_SET_LIMITED_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION,
            PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION,
        ):
            hProc = kernel32.OpenProcess(access, False, pid)
            if hProc:
                break
        if not hProc:
            return False

        ok = kernel32.SetProcessDefaultCpuSets(hProc, arr, len(set_ids))
        kernel32.CloseHandle(hProc)
        return bool(ok)
    except Exception:
        return False


def get_effective_cpu_sets(pid: int) -> list:
    if not _cpu_set_ids:
        return []
    try:
        kernel32   = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED = 0x1000
        hProc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not hProc:
            return []

        count = ctypes.c_ulong(0)

        kernel32.GetProcessDefaultCpuSets(hProc, None, 0, ctypes.byref(count))
        if count.value == 0:
            kernel32.CloseHandle(hProc)
            return []

        arr = (ctypes.c_uint32 * count.value)()
        kernel32.GetProcessDefaultCpuSets(hProc, arr, count.value, ctypes.byref(count))
        kernel32.CloseHandle(hProc)

        id_to_idx = _id_to_cpu_idx
        return sorted(id_to_idx[s] for s in arr if s in id_to_idx)
    except Exception:
        return []


_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

def _looks_like_guid(s) -> bool:
    return isinstance(s, str) and bool(_GUID_RE.match(s.strip()))


def _pp_log(msg: str):
    # Lightweight console logging for Power Plan events. Never allowed to
    # raise - a frozen/no-console build can make stdout unusable.
    try:
        print(f"[PowerPlan] {msg}")
    except Exception:
        pass


def list_power_plans() -> list:
    # Ask Windows for every installed Power Plan via `powercfg /list`.
    # Returns [(name, guid, is_active), ...], or [] if powercfg is missing
    # or its output couldn't be parsed. Never hardcode plan names/GUIDs here -
    # different Windows installs (and OEM images) ship different plans.
    try:
        r = subprocess.run(
            ["powercfg", "/list"],
            capture_output=True, text=True, creationflags=0x08000000,
        )
    except Exception as e:
        _pp_log(f"powercfg /list failed: {e}")
        return []

    plans = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.lower().startswith("power scheme"):
            continue
        try:
            after = line.split(":", 1)[1].strip()
            is_active = after.endswith("*")
            after = after.rstrip("*").strip()
            guid, _, rest = after.partition("(")
            guid = guid.strip()
            name = rest.rsplit(")", 1)[0].strip() if "(" in after else guid
            if _looks_like_guid(guid):
                plans.append((name or guid, guid, is_active))
        except Exception:
            continue
    return plans


def get_active_power_plan_guid() -> "str | None":
    # Cheaper than list_power_plans() when we only need the current plan.
    try:
        r = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, text=True, creationflags=0x08000000,
        )
    except Exception as e:
        _pp_log(f"powercfg /getactivescheme failed: {e}")
        return None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith("power scheme guid"):
            guid = line.split(":", 1)[1].strip().split("(")[0].strip()
            return guid if _looks_like_guid(guid) else None
    return None


def set_active_power_plan(guid: str) -> tuple:
    # Activate a Power Plan by GUID via `powercfg /setactive`.
    if not _looks_like_guid(guid):
        return (False, f"invalid GUID: {guid!r}")
    try:
        r = subprocess.run(
            ["powercfg", "/setactive", guid],
            capture_output=True, text=True, creationflags=0x08000000,
        )
        if r.returncode == 0:
            return (True, "OK")
        return (False, (r.stderr or r.stdout or f"exit code {r.returncode}").strip())
    except Exception as e:
        return (False, str(e))


class PowerPlanManager:
    # Windows Power Plans are system-wide, NOT per-process (see README /
    # EditorDialog note). This class is the single place that decides which
    # Power Plan rule "wins" when several managed processes are running at
    # once, so a process rule never fights another process rule over the
    # one active system Power Plan.
    #
    # Ownership model: every process with a power_plan rule that is
    # currently running is a "controller". The most-recently-launched
    # controller's plan is the one that gets activated. When a controller
    # exits, control falls back to whichever other controller is still
    # running (if any); once none remain, the plan that was active before
    # ProcessX started touching anything is restored.
    def __init__(self):
        self._lock          = threading.Lock()
        self._original_guid = None   # captured lazily, on first acquire
        self._controllers   = []     # [(exe_lower, guid), ...] oldest -> newest

    def on_process_seen(self, exe: str, guid: str):
        # `exe` (lowercased) just started and carries a valid power_plan rule.
        if not _looks_like_guid(guid):
            return
        with self._lock:
            if self._original_guid is None:
                self._original_guid = get_active_power_plan_guid()
            self._controllers = [c for c in self._controllers if c[0] != exe]
            self._controllers.append((exe, guid))
            self._activate_locked(guid)

    def on_process_gone(self, exe: str):
        # All instances of `exe` have exited (or its rule no longer wants a
        # Power Plan) - drop it from the controller stack and hand control
        # to whichever managed process is still running, if any.
        with self._lock:
            before = len(self._controllers)
            self._controllers = [c for c in self._controllers if c[0] != exe]
            if len(self._controllers) == before:
                return   # wasn't a controller - nothing to do
            if self._controllers:
                self._activate_locked(self._controllers[-1][1])
            else:
                self._restore_original_locked()

    def shutdown_restore(self):
        # Called on clean ProcessX shutdown: give back whatever plan was
        # active before we started managing anything, if we ever changed it.
        # Safe to call even if we never touched the Power Plan (no-op then).
        with self._lock:
            if self._original_guid is not None:
                self._restore_original_locked()
            self._controllers = []

    def _activate_locked(self, guid):
        # Only ever calls `powercfg /setactive` when the requested plan
        # differs from what's *actually* active right now (queried fresh,
        # so we don't fight anything the user or another tool changed).
        current = get_active_power_plan_guid()
        if current and current.lower() == guid.lower():
            return
        ok, err = set_active_power_plan(guid)
        if ok:
            _pp_log(f"Switched active Power Plan to {guid}")
        else:
            _pp_log(f"Failed to activate Power Plan: GUID={guid} Error={err}")

    def _restore_original_locked(self):
        guid = self._original_guid
        if not guid:
            return
        current = get_active_power_plan_guid()
        if current and current.lower() == guid.lower():
            return
        ok, err = set_active_power_plan(guid)
        if ok:
            _pp_log(f"Restored original Power Plan {guid}")
        else:
            _pp_log(f"Failed to restore original Power Plan: GUID={guid} Error={err}")


_power_plan_manager = PowerPlanManager()
atexit.register(_power_plan_manager.shutdown_restore)


def load_rules() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_rules(rules: dict):
    CONFIG_FILE.write_text(json.dumps(rules, indent=2))


def _validate_rules_data(data) -> tuple:
    if not isinstance(data, dict):
        raise ValueError("File does not contain a rules object (expected a JSON object at the top level).")

    cleaned = {}
    warnings = []
    for exe, rule in data.items():
        if not isinstance(exe, str) or not exe.strip():
            warnings.append(f"Skipped an entry with an invalid EXE name: {exe!r}")
            continue
        if not isinstance(rule, dict):
            warnings.append(f"Skipped '{exe}': rule is not a JSON object.")
            continue

        clean_rule = {}
        if "priority" in rule:
            clean_rule["priority"] = rule["priority"]
        if "io_priority" in rule:
            try:
                clean_rule["io_priority"] = int(rule["io_priority"])
            except (TypeError, ValueError):
                warnings.append(f"'{exe}': ignored invalid io_priority.")
        if "affinity" in rule:
            aff = rule["affinity"]
            if isinstance(aff, list) and all(isinstance(i, int) for i in aff):
                clean_rule["affinity"] = aff
            else:
                warnings.append(f"'{exe}': ignored invalid affinity list.")

        if "power_plan" in rule:
            pp = rule["power_plan"]
            if pp is None:
                pass   # explicit "no Power Plan rule" - fine to just omit it
            elif _looks_like_guid(pp):
                clean_rule["power_plan"] = pp
            else:
                warnings.append(f"'{exe}': ignored invalid power_plan (expected a GUID).")

        if not clean_rule:
            warnings.append(f"Skipped '{exe}': no recognizable rule fields (priority/io_priority/affinity/power_plan).")
            continue

        if "exe_path" in rule and isinstance(rule["exe_path"], str):
            clean_rule["exe_path"] = rule["exe_path"]

        cleaned[exe.lower()] = clean_rule

    if not cleaned:
        raise ValueError("No valid rules were found in this file.")

    return cleaned, warnings


def apply_rule(proc: psutil.Process, rule: dict) -> list:
    if proc is None:
        return ["CPU Sets: Saved", "CPU Priority: Saved", "I/O Priority: Saved"]
    pid = proc.pid
    results = []

    try:
        if rule.get("affinity"):
            results.append("CPU Sets: OK" if set_cpu_sets(pid, rule["affinity"]) else "CPU Sets: FAILED")
    except Exception:
        results.append("CPU Sets: FAILED")

    try:
        if "priority" in rule:
            proc.nice(PRIORITY_MAP.get(rule["priority"], psutil.NORMAL_PRIORITY_CLASS))
            results.append("CPU Priority: OK")
    except Exception:
        results.append("CPU Priority: FAILED")

    try:
        if "io_priority" in rule:
            ok, _reason = set_io_priority(pid, rule["io_priority"])
            results.append("I/O Priority: OK" if ok else "I/O Priority: FAILED")
    except Exception:
        results.append("I/O Priority: FAILED")

    return results


def apply_all_rules(rules: dict):
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            exe = proc.info["name"].lower()
            if exe in rules:
                apply_rule(proc, rules[exe])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _windows_default_rule() -> dict:
    return {
        "priority":    "Normal",
        "io_priority": 2,
        "affinity":    list(range(CPU_COUNT)),
    }


def revert_running_instances(exe_name: str):
    default_rule = _windows_default_rule()
    exe_lower = exe_name.lower()
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if (p.info["name"] or "").lower() != exe_lower:
                continue
            apply_rule(psutil.Process(p.info["pid"]), default_rule)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _write_ico(png_bytes: bytes, path: Path):
    try:
        from PIL import Image as _PIL
        img = _PIL.open(io.BytesIO(png_bytes)).convert("RGBA")
        sizes = [(16, 16), (20, 20), (24, 24), (32, 32),
                 (40, 40), (48, 48), (64, 64), (256, 256)]
        img.save(str(path), format="ICO", sizes=sizes)
        return
    except Exception:
        pass

    import struct
    header = struct.pack("<HHH", 0, 1, 1)
    size   = len(png_bytes)
    entry  = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, size, 22)
    path.write_bytes(header + entry + png_bytes)


def _win_toplevel_hwnd(win):
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd   = win.winfo_id()
        parent = user32.GetParent(hwnd)
        return parent if parent else hwnd
    except Exception:
        return win.winfo_id()

def _win_enable_dark_titlebar(win):
    try:
        hwnd = _win_toplevel_hwnd(win)
        value = ctypes.c_int(1)
        for attr in (20, 19):
            res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
            if res == 0:
                break
        DWMWA_TRANSITIONS_FORCEDISABLED = 3
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_TRANSITIONS_FORCEDISABLED,
            ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


class ProcessX(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ProcessX")
        self.minsize(640, 400)
        self.configure(bg=BG)


        self._boot_launch = "--startup" in sys.argv
        self.withdraw()
        if not self._boot_launch:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w, h = 820, 465
            x = (sw - w) // 2
            y = (sh - h) // 2
            self.geometry(f"{w}x{h}+{x}+{y}")

        self.update_idletasks()
        self._enable_dark_titlebar()
        self._fix_window_class_background()

        self._tk_icon = _make_tk_icon(self)
        if self._tk_icon:
            self.wm_iconphoto(True, self._tk_icon)

        self._header_logo = _make_tk_icon(self, size=34)

        self.after(200, self._apply_taskbar_icon)

        self.rules     = load_rules()
        self._sort_col = "exe"
        self._sort_rev = False


        global _watcher_status_cb, _watcher_refresh_cb
        _watcher_status_cb  = lambda msg: self.after(0, lambda m=msg: self.status_var.set(m))
        _watcher_refresh_cb = self._on_watcher_rule_applied
        self._watcher_thread = threading.Thread(
            target=_rule_watcher_loop,
            args=(lambda: self.rules,),
            daemon=True)
        self._watcher_thread.start()

        self._build_ui()
        self.bind("<Escape>", self._clear_proc_selection_key)
        self._refresh_rules_list()

        self._tray_icon = None
        self._refresh_job = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_tray()

        if not self._boot_launch:
            self.deiconify()
        self._auto_refresh()

    def _toplevel_hwnd(self):
        return _win_toplevel_hwnd(self)

    def _enable_dark_titlebar(self):
        _win_enable_dark_titlebar(self)

    def _fix_window_class_background(self):
        try:
            gdi32  = ctypes.WinDLL("gdi32",  use_last_error=True)
            user32 = ctypes.WinDLL("user32", use_last_error=True)

            gdi32.CreateSolidBrush.restype  = ctypes.c_void_p
            gdi32.CreateSolidBrush.argtypes = [ctypes.wintypes.DWORD]

            colorref = 0x11 | (0x11 << 8) | (0x11 << 16)
            brush = gdi32.CreateSolidBrush(colorref)
            if not brush:
                return

            GCL_HBRBACKGROUND = -10
            set_fn = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
            set_fn.restype  = ctypes.c_void_p
            set_fn.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

            for hwnd in (self.winfo_id(), self._toplevel_hwnd()):
                try:
                    set_fn(hwnd, GCL_HBRBACKGROUND, brush)
                    user32.InvalidateRect(hwnd, None, True)
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_taskbar_icon(self):
        try:
            WM_SETICON  = 0x0080
            ICON_SMALL  = 0
            ICON_BIG    = 1
            IMAGE_ICON  = 1
            LR_LOADFROMFILE = 0x0010
            SM_CXICON   = 11
            SM_CXSMICON = 49

            tmp_ico = Path(os.environ.get("TEMP", ".")) / "processx_icon.ico"
            _write_ico(_icon_bytes(), tmp_ico)

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            big_sz   = user32.GetSystemMetrics(SM_CXICON)   or 32
            small_sz = user32.GetSystemMetrics(SM_CXSMICON) or 16

            hicon_big = user32.LoadImageW(
                None, str(tmp_ico), IMAGE_ICON, big_sz, big_sz, LR_LOADFROMFILE)
            hicon_small = user32.LoadImageW(
                None, str(tmp_ico), IMAGE_ICON, small_sz, small_sz, LR_LOADFROMFILE)

            hwnd = self._toplevel_hwnd()
            if hicon_big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG,   hicon_big)
            if hicon_small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
        except Exception:
            pass

    def _build_ui(self):

        btn_row = tk.Frame(self, bg=BG, pady=6)
        btn_row.pack(fill="x", padx=12, pady=(2, 3))

        self._btn(btn_row, "Rules",          self._open_rules_window).pack(side="left", padx=(0, 3), pady=2)
        self._btn(btn_row, "Add Rule",       self._open_add_rule).pack(side="left", padx=3, pady=2)
        self._btn(btn_row, "Add to Startup", self._add_startup).pack(side="left", padx=3, pady=2)

        tk.Label(btn_row, text="by jadenpeek",
                 font=(FONT, 8, "bold"), fg=TEXT, bg=BG).pack(side="right", padx=(0, 6), pady=2)
        if self._header_logo:
            tk.Label(btn_row, image=self._header_logo, bg=BG).pack(side="right", padx=(6, 0), pady=2)
        else:
            tk.Label(btn_row, text="ProcessX", font=(FONT, 10, "bold"),
                     fg=TEXT, bg=BG).pack(side="right", padx=(6, 0), pady=2)


        tf = tk.Frame(self, bg=BG, highlightthickness=0, bd=0)
        tf.pack(fill="both", expand=True, padx=12, pady=(0, 3))

        proc_cols = ("name", "cpu", "ram", "priority", "io", "affinity")
        self.proc_tree = ttk.Treeview(tf, columns=proc_cols,
                                      show="tree headings",
                                      selectmode="browse", style="AM.Treeview")
        proc_col_cfg = [
            ("name",     "Name",           150, 70,  "w", "w"),
            ("cpu",      "CPU",            70,  60,  "center", "center"),
            ("ram",      "Memory",         90,  80,  "center", "center"),
            ("priority", "CPU Priority",   120, 100, "center", "center"),
            ("io",       "I/O Priority",   110, 90,  "center", "center"),
            ("affinity", "CPU Sets",       180, 150, "center", "center"),
        ]
        self.proc_tree.column("#0", width=28, minwidth=28, stretch=False)
        for col, label, width, minwidth, head_anchor, cell_anchor in proc_col_cfg:
            self.proc_tree.heading(col, text=label, anchor=head_anchor)
            self.proc_tree.column(col, width=width, minwidth=minwidth,
                                  anchor=cell_anchor, stretch=(col == "name"))

        proc_sb = ttk.Scrollbar(tf, orient="vertical",
                                command=self.proc_tree.yview,
                                style="AM.Vertical.TScrollbar")
        self.proc_tree.configure(yscrollcommand=proc_sb.set)
        proc_sb.pack(side="right", fill="y")
        self.proc_tree.pack(fill="both", expand=True)
        self.proc_tree.bind("<Double-1>", self._proc_tree_double_click)
        self.proc_tree.bind("<ButtonPress-1>", self._on_proc_tree_press, add="+")
        self.proc_tree.bind("<ButtonRelease-1>", self._on_proc_tree_release, add="+")
        self.proc_tree.bind("<Button-1>", self._clear_proc_selection, add="+")
        self.proc_tree.bind("<Escape>", self._clear_proc_selection_key)

        self.proc_tree.tag_configure("group",      foreground=TEXT,      font=(FONT, 9, "bold"))
        self.proc_tree.tag_configure("group_rule", foreground="#7ecf7e", font=(FONT, 9, "bold"))
        self.proc_tree.tag_configure("child",      foreground=TEXT_DIM)
        self.proc_tree.tag_configure("child_rule", foreground="#7ecf7e")

        self._open_groups: set = set()
        self._icon_cache: dict = {}
        self._io_pri_cache: dict = {}
        self._aff_cache: dict = {}

        self.tree = ttk.Treeview(self, columns=("exe","priority","io","affinity","status"),
                                 show="headings", style="AM.Treeview")

        tk.Frame(self, bg=BG, height=4).pack(fill="x")
        self.status_var = tk.StringVar(value="Watcher running — rules auto-applied when EXE starts.")
        tk.Label(self, textvariable=self.status_var,
                 font=(FONT, 8, "bold"), fg=TEXT_DIM, bg=BG, anchor="w").pack(
            fill="x", padx=14, pady=(0, 4))

        self._style_tree()
        self.after(300, self._sync_window_to_columns)

    def _snapshot_col_widths(self):
        widths = {"#0": self.proc_tree.column("#0", "width")}
        for c in self.proc_tree["columns"]:
            widths[c] = self.proc_tree.column(c, "width")
        return widths

    def _on_proc_tree_press(self, event):
        try:
            self._pre_drag_widths = self._snapshot_col_widths()
        except Exception:
            self._pre_drag_widths = None

    def _on_proc_tree_release(self, event):
        try:
            before = getattr(self, "_pre_drag_widths", None)
            if before is None:
                return
            after = self._snapshot_col_widths()
            if after != before:
                self.after(10, self._sync_window_to_columns)
        except Exception:
            pass

    def _sync_window_to_columns(self):
        try:
            total = self.proc_tree.column("#0", "width")
            for c in self.proc_tree["columns"]:
                total += self.proc_tree.column(c, "width")
            total += 24 + 28
            cur_w = self.winfo_width()
            if total > cur_w:
                h = self.winfo_height()
                x = self.winfo_x()
                y = self.winfo_y()
                self.geometry(f"{total}x{h}+{x}+{y}")
        except Exception:
            pass

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd,
                         font=(FONT, 9, "bold"), fg=BTN_FG, bg=BTN_BG,
                         activebackground="#4a4a4a", activeforeground=TEXT,
                         relief="flat", cursor="hand2", padx=9, pady=4,
                         bd=0, highlightthickness=0)

    def _style_tree(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("AM.Treeview",
                        background=PANEL, foreground=TEXT,
                        fieldbackground=PANEL, rowheight=24,
                        font=(FONT, 9, "bold"), borderwidth=0,
                        relief="flat")
        style.configure("AM.Treeview.Heading",
                        background=BG, foreground=TEXT,
                        font=(FONT, 9, "bold"), relief="flat")
        style.map("AM.Treeview.Heading",
                  background=[("active", BG), ("pressed", BG), ("!active", BG)],
                  foreground=[("active", TEXT), ("pressed", TEXT)])
        style.configure("AM.Vertical.TScrollbar",
                        background=BTN_BG, troughcolor=BG,
                        bordercolor=BG, arrowcolor=TEXT,
                        relief="flat", borderwidth=0, arrowsize=12)
        style.map("AM.Vertical.TScrollbar",
                  background=[("active", "#4a4a4a"), ("pressed", "#4a4a4a")],
                  arrowcolor=[("active", TEXT), ("pressed", TEXT)])
        style.configure("AM.TCombobox",
                        fieldbackground=BTN_BG, background=BTN_BG, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=BORDER, lightcolor=BTN_BG,
                        darkcolor=BTN_BG, selectbackground=BTN_BG, selectforeground=TEXT)
        style.map("AM.TCombobox",
                  fieldbackground=[("readonly", BTN_BG), ("!readonly", BTN_BG)],
                  foreground=[("readonly", TEXT)],
                  background=[("readonly", BTN_BG)])
        self.option_add("*TCombobox*Listbox.background", BTN_BG)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", SEL_BG)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        style.layout("AM.Treeview", [
            ("AM.Treeview.treearea", {"sticky": "nswe"})
        ])
        style.map("AM.Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", TEXT)])
        self.proc_tree.configure(takefocus=0)
        style.configure("AM.Treeview", highlightthickness=0, borderwidth=0)
        self.tree.tag_configure("alt",         background=ROW_ALT)
        self.tree.tag_configure("running",     foreground="#7ecf7e")
        self.tree.tag_configure("alt_running", background=ROW_ALT, foreground="#7ecf7e")


    def _get_running_pids(self) -> dict:
        want = set(self.rules.keys())
        result = {}
        for p in psutil.process_iter(["name", "pid"]):
            try:
                n = (p.info["name"] or "").lower()
                if n in want and n not in result:
                    result[n] = p.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return result

    def _aff_str(self, aff):
        if not aff or len(set(aff)) >= CPU_COUNT:
            return "all"
        cpus = sorted(set(aff))
        ranges = []
        start = prev = cpus[0]
        for c in cpus[1:]:
            if c == prev + 1:
                prev = c
                continue
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = c
        ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
        return ",".join(ranges)

    def _refresh_rules_list(self):
        sel = self.tree.focus()
        sel_exe = None
        if sel:
            try:
                sel_exe = str(self.tree.item(sel)["values"][0])
            except Exception:
                pass

        running = self._get_running_pids()

        rows = []
        for exe, rule in self.rules.items():
            aff    = rule.get("affinity", [])
            aff_s  = self._aff_str(aff)
            io_n   = IO_PRIORITY_NAMES[rule["io_priority"]] if "io_priority" in rule else "Not Specified"
            pri    = rule.get("priority", "Normal")
            status = "● Running" if exe in running else "○ Waiting"
            rows.append((exe, pri, io_n, aff_s, status))

        col_idx = {"exe": 0, "priority": 1, "io": 2, "affinity": 3, "status": 4}
        idx = col_idx.get(self._sort_col, 0)
        rows.sort(
            key=lambda x: x[idx].lower() if isinstance(x[idx], str) else x[idx],
            reverse=self._sort_rev
        )

        self.tree.delete(*self.tree.get_children())
        restore_id = None
        for i, row in enumerate(rows):
            exe_name = row[0]
            is_running = exe_name in running
            alt = i % 2 == 1
            if is_running:
                tag = "alt_running" if alt else "running"
            else:
                tag = "alt" if alt else ""
            tags = (tag,) if tag else ()
            iid = self.tree.insert("", "end", values=row, tags=tags)
            if exe_name == sel_exe:
                restore_id = iid

        if restore_id:
            self.tree.focus(restore_id)
            self.tree.selection_set(restore_id)

    def _auto_refresh(self):
        self._refresh_rules_list()
        has_open_dropdown = any(
            self.proc_tree.item(iid, "open")
            for iid in self.proc_tree.get_children()
        )
        if not has_open_dropdown:
            threading.Thread(target=self._auto_refresh_worker, daemon=True).start()

        if self.state() != "withdrawn":
            self._refresh_job = self.after(2000, self._auto_refresh)
        else:
            self._refresh_job = None

    def _auto_refresh_worker(self):
        try:
            snapshot = self._collect_proc_snapshot()
        except Exception:
            return
        self.after(0, self._refresh_proc_list, snapshot)

    def _on_watcher_rule_applied(self):
        try:
            snapshot = self._collect_proc_snapshot()
        except Exception:
            return
        self.after(0, self._apply_watcher_refresh, snapshot)

    def _apply_watcher_refresh(self, snapshot):
        self._refresh_rules_list()
        self._refresh_proc_list(snapshot)

    def _clear_proc_selection(self, event):
        if not self.proc_tree.identify_row(event.y):
            self.proc_tree.selection_remove(*self.proc_tree.selection())
            self.proc_tree.focus("")

    def _clear_proc_selection_key(self, event=None):
        self.proc_tree.selection_remove(*self.proc_tree.selection())
        self.proc_tree.focus("")

    _IO_PRI_TTL  = 6.0
    _AFF_TTL     = 6.0

    def _priority_name(self, nice):
        _map = {
            psutil.IDLE_PRIORITY_CLASS:         "Idle",
            psutil.BELOW_NORMAL_PRIORITY_CLASS: "Below Normal",
            psutil.NORMAL_PRIORITY_CLASS:       "Normal",
            psutil.ABOVE_NORMAL_PRIORITY_CLASS: "Above Normal",
            psutil.HIGH_PRIORITY_CLASS:         "High",
            psutil.REALTIME_PRIORITY_CLASS:     "Realtime",
        }
        return _map.get(nice, "Normal")

    def _io_priority_name_cached(self, pid):
        import time
        now = time.monotonic()
        cached = self._io_pri_cache.get(pid)
        if cached and (now - cached[0]) < self._IO_PRI_TTL:
            return cached[1]
        try:
            from ctypes import windll, byref, c_ulong
            hProc = windll.kernel32.OpenProcess(0x1000, False, pid)
            if not hProc:
                val_str = "Not Specified"
            else:
                val = c_ulong(0)
                _NtQIP = windll.ntdll.NtQueryInformationProcess
                status = _NtQIP(hProc, 33, byref(val), 4, None)
                windll.kernel32.CloseHandle(hProc)
                val_str = {0: "Very Low", 1: "Low", 2: "Normal", 3: "High"}.get(val.value, "Normal") if status == 0 else "Not Specified"
        except Exception:
            val_str = "Not Specified"
        self._io_pri_cache[pid] = (now, val_str)
        return val_str

    def _effective_cpu_sets_cached(self, pid):
        import time
        now = time.monotonic()
        cached = self._aff_cache.get(pid)
        if cached and (now - cached[0]) < self._AFF_TTL:
            return cached[1]
        live = get_effective_cpu_sets(pid)
        self._aff_cache[pid] = (now, live)
        return live

    @staticmethod
    def _fmt_ram(b):
        if b >= 1 << 30: return f"{b/(1<<30):.1f} GB"
        if b >= 1 << 20: return f"{b/(1<<20):.0f} MB"
        if b >= 1 << 10: return f"{b/(1<<10):.0f} KB"
        return f"{b} B"

    @staticmethod
    def _fmt_cpu(pct):
        return f"{pct:.1f}%"

    def _collect_proc_snapshot(self):
        from collections import defaultdict, Counter

        _priority_name    = self._priority_name
        _io_priority_name = self._io_priority_name_cached
        _fmt_ram          = self._fmt_ram
        _fmt_cpu          = self._fmt_cpu

        procs = []
        for p in psutil.process_iter(["name", "pid", "memory_info", "nice", "cpu_percent"]):
            try:
                with p.oneshot():
                    info = p.info
                    name = (info["name"] or "").strip()
                    if not name:
                        continue
                    try:
                        ram = p.memory_full_info().uss
                    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                        ram = info["memory_info"].rss if info["memory_info"] else 0
                    nice = info["nice"]
                    if name.lower() == "system idle process":
                        cpu = 0.0
                    else:
                        cpu  = (info["cpu_percent"] or 0.0) / CPU_COUNT
                    procs.append((name, info["pid"], ram, nice, cpu))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        groups: dict = defaultdict(list)
        for name, pid, ram, nice, cpu in procs:
            groups[name].append((pid, ram, nice, cpu))

        group_order = sorted(
            groups.items(),
            key=lambda kv: sum(r for _, r, _, _ in kv[1]),
            reverse=True
        )

        rules_lower = {k.lower() for k in self.rules}
        snapshot = []

        for name, instances in group_order:
            instances = sorted(instances, key=lambda x: x[1], reverse=True)
            has_children = len(instances) > 1

            if has_children:
                dominant_nice = Counter(n for _, _, n, _ in instances).most_common(1)[0][0]
                rep_pid   = instances[0][0]
                pri_str   = _priority_name(dominant_nice)
                total_ram = sum(r for _, r, _, _ in instances)
                total_cpu = sum(c for _, _, _, c in instances)
            else:
                rep_pid, total_ram, nice, total_cpu = instances[0]
                pri_str = _priority_name(nice)

            name_lower = name.lower()
            if name_lower in rules_lower and "priority" in self.rules[name_lower]:
                pri_str = self.rules[name_lower]["priority"]

            io_str  = _io_priority_name(rep_pid)
            ram_str = _fmt_ram(total_ram)
            cpu_str = _fmt_cpu(total_cpu)

            if name_lower in rules_lower and self.rules[name_lower].get("affinity"):
                aff_str = self._aff_str(self.rules[name_lower]["affinity"])
            else:
                live = self._effective_cpu_sets_cached(rep_pid)
                aff_str = self._aff_str(live) if live else "all"

            children = []
            if has_children:
                rule_priority = self.rules[name_lower].get("priority") if name_lower in rules_lower else None
                rule_affinity = self.rules[name_lower].get("affinity") if name_lower in rules_lower else None

                for pid, ram, nice, cpu in instances:
                    child_pri = rule_priority or _priority_name(nice)
                    child_io  = _io_priority_name(pid)
                    if rule_affinity:
                        child_aff = self._aff_str(rule_affinity)
                    else:
                        child_live = self._effective_cpu_sets_cached(pid)
                        child_aff = self._aff_str(child_live) if child_live else "all"

                    children.append({
                        "pid": pid, "cpu_str": _fmt_cpu(cpu), "ram_str": _fmt_ram(ram),
                        "pri_str": child_pri, "io_str": child_io, "aff_str": child_aff,
                    })

            snapshot.append({
                "name": name, "has_children": has_children,
                "cpu_str": cpu_str, "ram_str": ram_str, "pri_str": pri_str,
                "io_str": io_str, "aff_str": aff_str, "children": children,
            })

        return snapshot

    def _refresh_proc_list(self, snapshot=None):
        if snapshot is None:
            snapshot = self._collect_proc_snapshot()

        currently_open = set()
        for iid in self.proc_tree.get_children():
            if self.proc_tree.item(iid, "open"):
                currently_open.add(self.proc_tree.item(iid, "values")[0].strip())

        sel_val = None
        sel = self.proc_tree.focus()
        if sel:
            try:
                v = self.proc_tree.item(sel, "values")
                sel_val = (v[0].strip(),) + tuple(v[1:]) if v else None
            except Exception:
                pass

        self.proc_tree.delete(*self.proc_tree.get_children())
        restore_iid = None

        for grp in snapshot:
            name = grp["name"]
            icon = self._get_exe_icon(name)

            iid = self.proc_tree.insert(
                "", "end",
                image=icon if icon else "",
                values=(" " + name, grp["cpu_str"], grp["ram_str"], grp["pri_str"], grp["io_str"], grp["aff_str"]),
                tags=("group",)
            )
            if sel_val and sel_val[0] == " " + name:
                restore_iid = iid

            for child in grp["children"]:
                child_iid = self.proc_tree.insert(
                    iid, "end",
                    values=("   " + name, child["cpu_str"], child["ram_str"],
                            child["pri_str"], child["io_str"], child["aff_str"]),
                    tags=("child",)
                )
                if sel_val and sel_val[0] == "   " + name and sel_val[2] == child["ram_str"]:
                    restore_iid = child_iid

            if grp["has_children"] and name in currently_open:
                self.proc_tree.item(iid, open=True)

        if restore_iid:
            self.proc_tree.focus(restore_iid)
            self.proc_tree.selection_set(restore_iid)

        self._open_groups = currently_open

    def _proc_tree_double_click(self, event):
        iid = self.proc_tree.identify_row(event.y)
        if not iid:
            return "break"
        try:
            name = self.proc_tree.item(iid, "values")[0].strip()
            if name:
                self._open_editor_for_exe(name)
        except Exception:
            pass
        return "break"

    _NO_EXE_PROCESSES = {"system", "system idle process", "registry"}

    def _render_hicon(self, hicon, size: int) -> object:
        try:
            SIZE    = size
            user32  = ctypes.windll.user32
            gdi32   = ctypes.windll.gdi32

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize",          ctypes.c_uint32),
                             ("biWidth",         ctypes.c_int32),
                             ("biHeight",        ctypes.c_int32),
                             ("biPlanes",        ctypes.c_uint16),
                             ("biBitCount",      ctypes.c_uint16),
                             ("biCompression",   ctypes.c_uint32),
                             ("biSizeImage",     ctypes.c_uint32),
                             ("biXPelsPerMeter", ctypes.c_int32),
                             ("biYPelsPerMeter", ctypes.c_int32),
                             ("biClrUsed",       ctypes.c_uint32),
                             ("biClrImportant",  ctypes.c_uint32)]
            bih          = BITMAPINFOHEADER()
            bih.biSize   = ctypes.sizeof(BITMAPINFOHEADER)
            bih.biWidth  = SIZE
            bih.biHeight = -SIZE
            bih.biPlanes = 1
            bih.biBitCount  = 32
            bih.biCompression = 0

            ppvBits    = ctypes.c_void_p()
            hdc_screen = user32.GetDC(None)
            hdc_mem    = gdi32.CreateCompatibleDC(hdc_screen)
            hbm_dib    = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bih), 0,
                                                 ctypes.byref(ppvBits), None, 0)
            old_bm = gdi32.SelectObject(hdc_mem, hbm_dib)

            class RECT(ctypes.Structure):
                _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),
                             ("right",ctypes.c_long),("bottom",ctypes.c_long)]
            hbr = gdi32.CreateSolidBrush(0x001a1a1a)
            rc  = RECT(0, 0, SIZE, SIZE)
            user32.FillRect(hdc_mem, ctypes.byref(rc), hbr)
            gdi32.DeleteObject(hbr)

            user32.DrawIconEx(hdc_mem, 0, 0, hicon, SIZE, SIZE, 0, None, 3)
            gdi32.GdiFlush()

            raw_buf = (ctypes.c_ubyte * (SIZE * SIZE * 4))()
            ctypes.memmove(raw_buf, ppvBits, SIZE * SIZE * 4)

            gdi32.SelectObject(hdc_mem, old_bm)
            gdi32.DeleteObject(hbm_dib)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
            user32.DestroyIcon(hicon)

            from PIL import Image as _PILImg
            pil_img = _PILImg.frombytes("RGBA", (SIZE, SIZE), bytes(raw_buf), "raw", "BGRA")
            bg      = _PILImg.new("RGBA", (SIZE, SIZE), (26, 26, 26, 255))
            bg.paste(pil_img, mask=pil_img.split()[3])
            png_buf = io.BytesIO()
            bg.convert("RGB").save(png_buf, format="PNG")
            png_buf.seek(0)
            return tk.PhotoImage(data=base64.b64encode(png_buf.read()))
        except Exception:
            return None

    def _get_default_icon(self, size: int = 16) -> object:
        key = "__default__" if size == 16 else f"__default__@{size}"
        if key in self._icon_cache:
            return self._icon_cache[key]
        img = None
        try:
            from PIL import Image as _PILImg
            SIZE = size
            src = _PILImg.open(io.BytesIO(_icon_bytes())).convert("RGBA")
            src = src.resize((SIZE, SIZE), _PILImg.LANCZOS)
            bg = _PILImg.new("RGBA", (SIZE, SIZE), (26, 26, 26, 255))
            bg.paste(src, mask=src.split()[3])
            png_buf = io.BytesIO()
            bg.convert("RGB").save(png_buf, format="PNG")
            png_buf.seek(0)
            img = tk.PhotoImage(data=base64.b64encode(png_buf.read()))
        except Exception:
            img = None
        self._icon_cache[key] = img
        return img

    def _get_generic_exe_icon(self, size: int = 16) -> object:
        key = "__generic_exe__" if size == 16 else f"__generic_exe__@{size}"
        if key in self._icon_cache:
            return self._icon_cache[key]
        img = None
        try:
            shell32 = ctypes.windll.shell32

            SHGFI_ICON              = 0x100
            SHGFI_LARGEICON          = 0x000000000
            SHGFI_SMALLICON          = 0x000000001
            SHGFI_USEFILEATTRIBUTES  = 0x000000010
            FILE_ATTRIBUTE_NORMAL    = 0x80
            shgfi_flags = (SHGFI_ICON | SHGFI_USEFILEATTRIBUTES
                           | (SHGFI_SMALLICON if size <= 16 else SHGFI_LARGEICON))

            class SHFILEINFOW(ctypes.Structure):
                _fields_ = [("hIcon",        ctypes.wintypes.HANDLE),
                             ("iIcon",        ctypes.c_int),
                             ("dwAttributes", ctypes.wintypes.DWORD),
                             ("szDisplayName",ctypes.c_wchar * 260),
                             ("szTypeName",   ctypes.c_wchar * 80)]
            shfi = SHFILEINFOW()
            ret = shell32.SHGetFileInfoW(
                "processx_generic_placeholder.exe", FILE_ATTRIBUTE_NORMAL,
                ctypes.byref(shfi), ctypes.sizeof(shfi), shgfi_flags)
            if ret and shfi.hIcon:
                img = self._render_hicon(shfi.hIcon, size)
        except Exception:
            img = None
        self._icon_cache[key] = img
        return img

    def _get_exe_icon(self, name: str, size: int = 16, use_logo_fallback: bool = False,
                       exe_path_hint: str = None) -> object:
        key = name.lower() if size == 16 else f"{name.lower()}@{size}"

        if key in self._icon_cache:
            return self._icon_cache[key]

        def _fallback():
            return self._get_default_icon(size) if use_logo_fallback else self._get_generic_exe_icon(size)

        if name.lower() in self._NO_EXE_PROCESSES:
            return _fallback()

        img = None
        try:
            exe_path = None
            for p in psutil.process_iter(["name", "exe"]):
                try:
                    if (p.info["name"] or "").lower() == name.lower() and p.info["exe"]:
                        exe_path = p.info["exe"]
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if not exe_path and exe_path_hint:
                try:
                    if os.path.isfile(exe_path_hint):
                        exe_path = exe_path_hint
                except Exception:
                    pass
            if not exe_path:
                return _fallback()

            SIZE    = size
            shell32 = ctypes.windll.shell32

            SHGFI_ICON       = 0x100
            SHGFI_LARGEICON  = 0x000000000
            SHGFI_SMALLICON  = 0x000000001
            shgfi_flags = SHGFI_ICON | (SHGFI_SMALLICON if size <= 16 else SHGFI_LARGEICON)

            class SHFILEINFOW(ctypes.Structure):
                _fields_ = [("hIcon",        ctypes.wintypes.HANDLE),
                             ("iIcon",        ctypes.c_int),
                             ("dwAttributes", ctypes.wintypes.DWORD),
                             ("szDisplayName",ctypes.c_wchar * 260),
                             ("szTypeName",   ctypes.c_wchar * 80)]
            shfi = SHFILEINFOW()
            ret  = shell32.SHGetFileInfoW(exe_path, 0, ctypes.byref(shfi),
                                          ctypes.sizeof(shfi), shgfi_flags)
            if not ret or not shfi.hIcon:
                self._icon_cache[key] = None
                return None

            img = self._render_hicon(shfi.hIcon, SIZE)
        except Exception:
            img = None
        self._icon_cache[key] = img
        return img

    def _sort_by(self, col):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self._refresh_rules_list()


    def _open_add_rule(self):
        dlg = _ExeNameDialog(self)
        self.wait_window(dlg)
        exe_name = dlg.result
        if not exe_name:
            return

        EditorDialog(self, proc=None, proc_name=exe_name, rules=self.rules)


    def _open_editor_for_exe(self, exe_name: str):
        proc = None
        for p in psutil.process_iter(["name", "pid"]):
            try:
                if (p.info["name"] or "").lower() == exe_name.lower():
                    proc = psutil.Process(p.info["pid"])
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return EditorDialog(self, proc=proc, proc_name=exe_name, rules=self.rules)

    def _open_editor_for_selected(self):
        sel = self.tree.focus()
        if not sel:
            return
        exe_name = str(self.tree.item(sel)["values"][0])
        self._open_editor_for_exe(exe_name)

    def _open_rules_window(self):
        win = tk.Toplevel(self, bg=BG)
        win.title("Rules")
        win.geometry("940x420")
        win.resizable(True, True)
        win.grab_set()
        win.update_idletasks()
        _win_enable_dark_titlebar(win)

        cols = ("exe", "priority", "io", "affinity", "power_plan", "status")
        tree = ttk.Treeview(win, columns=cols, show="tree headings",
                            selectmode="browse", style="AM.Treeview")
        tree.column("#0", width=40, minwidth=40, stretch=False)
        tree.heading("#0", text="")
        col_cfg = [
            ("exe",        "EXE Name",     200, "w"),
            ("priority",   "CPU Priority", 115, "center"),
            ("io",         "I/O Priority", 100, "center"),
            ("affinity",   "CPU Sets",     215, "center"),
            ("power_plan", "Power Plan",   150, "center"),
            ("status",     "Status",       110, "center"),
        ]
        for col, label, width, anchor in col_cfg:
            tree.heading(col, text=label, anchor=anchor)
            tree.column(col, width=width, anchor=anchor, stretch=(col == "exe"))
        tree.tag_configure("alt",        background=ROW_ALT)
        tree.tag_configure("running",    foreground="#7ecf7e")
        tree.tag_configure("alt_running",background=ROW_ALT, foreground="#7ecf7e")

        sb = ttk.Scrollbar(win, orient="vertical", command=tree.yview,
                          style="AM.Vertical.TScrollbar")
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)
        tree.pack(fill="both", expand=True, padx=(8, 0), pady=(8, 4))

        def _populate():
            running = self._get_running_pids()
            plan_name_by_guid = {guid.lower(): name for name, guid, _active in list_power_plans()}
            rows = []
            for exe, rule in self.rules.items():
                aff   = rule.get("affinity", [])
                aff_s = self._aff_str(aff)
                io_n  = IO_PRIORITY_NAMES[rule["io_priority"]] if "io_priority" in rule else "Not Specified"
                pri   = rule.get("priority", "Normal")
                pp_guid = rule.get("power_plan")
                pp_n  = plan_name_by_guid.get(pp_guid.lower(), pp_guid) if pp_guid else "Follow Current"
                status = "● Running" if exe in running else "○ Waiting"
                rows.append((exe, pri, io_n, aff_s, pp_n, status))
            rows.sort(key=lambda x: x[0].lower())
            tree.delete(*tree.get_children())
            for i, row in enumerate(rows):
                exe_name   = row[0]
                is_running = exe_name in running
                alt        = i % 2 == 1
                if is_running:
                    tag = "alt_running" if alt else "running"
                else:
                    tag = "alt" if alt else ""
                icon = self._get_exe_icon(
                    exe_name, 20, use_logo_fallback=True,
                    exe_path_hint=self.rules.get(exe_name, {}).get("exe_path"),
                )
                tree.insert("", "end", image=icon if icon else "",
                            values=row, tags=(tag,) if tag else ())

        _populate()

        def _edit():
            sel = tree.focus()
            if not sel:
                messagebox.showinfo("Edit Rule", "Select a rule to edit.")
                return
            exe_name = str(tree.item(sel)["values"][0])
            dlg = self._open_editor_for_exe(exe_name)
            win.wait_window(dlg)
            _populate()

        def _delete():
            sel = tree.focus()
            if not sel:
                messagebox.showinfo("Delete Rule", "Select a rule to delete.")
                return
            exe_name = str(tree.item(sel)["values"][0])
            key = exe_name.lower()
            if key in self.rules:
                del self.rules[key]
                save_rules(self.rules)
                revert_running_instances(exe_name)
                _power_plan_manager.on_process_gone(key)
                self.status_var.set(f"Rule deleted for {exe_name} — reverted to defaults")
                _populate()

        def _export():
            if not self.rules:
                messagebox.showinfo("Export Rules", "There are no rules to export yet.")
                return
            path = filedialog.asksaveasfilename(
                parent=win,
                title="Export Rules",
                defaultextension=".json",
                initialfile="processx_rules.json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
            if not path:
                return
            try:
                Path(path).write_text(json.dumps(self.rules, indent=2))
            except Exception as e:
                messagebox.showerror("Export Rules", f"Could not save file:\n\n{e}")
                return
            self.status_var.set(f"Exported {len(self.rules)} rule(s) to {path}")

        def _import():
            path = filedialog.askopenfilename(
                parent=win,
                title="Import Rules",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
            if not path:
                return
            try:
                raw = json.loads(Path(path).read_text())
                cleaned, warnings = _validate_rules_data(raw)
            except json.JSONDecodeError as e:
                messagebox.showerror("Import Rules", f"That file isn't valid JSON:\n\n{e}")
                return
            except ValueError as e:
                messagebox.showerror("Import Rules", str(e))
                return
            except Exception as e:
                messagebox.showerror("Import Rules", f"Could not read file:\n\n{e}")
                return

            overlap = sorted(set(cleaned) & set(self.rules))
            if overlap:
                sample = ", ".join(overlap[:5]) + (", ..." if len(overlap) > 5 else "")
                if not messagebox.askyesno(
                    "Import Rules",
                    f"{len(overlap)} rule(s) already exist and will be overwritten:\n\n{sample}\n\nContinue?"
                ):
                    return

            self.rules.update(cleaned)
            save_rules(self.rules)
            _populate()
            self._refresh_rules_list()
            self._refresh_proc_list()

            msg = f"Imported {len(cleaned)} rule(s) from {Path(path).name}"
            if warnings:
                msg += f"  ({len(warnings)} skipped)"
                messagebox.showwarning(
                    "Import Rules",
                    f"Imported {len(cleaned)} rule(s).\n\n" +
                    "\n".join(warnings[:10]) +
                    ("\n..." if len(warnings) > 10 else "")
                )
            self.status_var.set(msg)

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(fill="x", padx=8, pady=(4, 10))
        inner = tk.Frame(btn_row, bg=BG)
        inner.pack(anchor="center")
        self._btn(inner, "Import Rules", _import).pack(side="left", padx=6)
        self._btn(inner, "Export Rules", _export).pack(side="left", padx=6)
        self._btn(inner, "Edit Rule",    _edit).pack(side="left", padx=6)
        self._btn(inner, "Delete Rule",  _delete).pack(side="left", padx=6)
        self._btn(inner, "Close",        win.destroy).pack(side="left", padx=6)

    def _delete_selected_rule(self):
        sel = self.tree.focus()
        if not sel:
            messagebox.showinfo("Delete Rule", "Select a rule to delete.")
            return
        exe_name = str(self.tree.item(sel)["values"][0])
        key = exe_name.lower()
        if key in self.rules:
            del self.rules[key]
            save_rules(self.rules)
            revert_running_instances(exe_name)
            self._refresh_rules_list()
            self.status_var.set(f"Rule deleted for {exe_name} — reverted to defaults")

    def _add_startup(self):
        frozen = getattr(sys, "frozen", False)
        if not frozen:
            messagebox.showwarning(
                "Build required",
                "Add to Startup only works with the compiled ProcessX.exe.\n\n"
                "Run this from ProcessX.exe after building with PyInstaller."
            )
            return

        exe_path = str(Path(sys.executable).resolve())
        cmd = (
            f'schtasks /Create /F /TN "ProcessX" '
            f'/TR "\\"{exe_path}\\" --startup" '
            f'/SC ONLOGON /RL HIGHEST'
        )
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if r.returncode == 0:
                messagebox.showinfo("Done", f"✓ ProcessX.exe added to startup as Administrator.\n\n{exe_path}")
            else:
                messagebox.showerror("Failed", f"schtasks error:\n\n{r.stderr.strip() or r.stdout.strip()}\n\nRun ProcessX.exe as Administrator.")
        except Exception as e:
            messagebox.showerror("Failed", str(e))


    def _start_tray(self):
        if not _TRAY_AVAILABLE:
            self.status_var.set("Tray unavailable — install pystray and Pillow: pip install pystray pillow")
            return
        if self._tray_icon is not None:
            return
        img = _make_pil_icon()
        if img is None:
            self.status_var.set("Tray icon failed — Pillow may not be installed correctly.")
            return

        menu = pystray.Menu(
            pystray.MenuItem("Show ProcessX", self._tray_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray_icon = pystray.Icon("ProcessX", img, "ProcessX", menu)


        t = threading.Thread(target=self._tray_icon.run, daemon=True)
        t.start()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self._show_window)

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        if self._refresh_job is None:
            self._auto_refresh()

    def _tray_quit(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
        _power_plan_manager.shutdown_restore()
        self.after(0, self.destroy)

    def _on_close(self):
        if _TRAY_AVAILABLE and self._tray_icon:
            if self._refresh_job is not None:
                self.after_cancel(self._refresh_job)
                self._refresh_job = None
            self.withdraw()
        else:
            _power_plan_manager.shutdown_restore()
            self.destroy()


class _ExeNameDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("Add Rule")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()
        w, h = 360, 150
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.update_idletasks()
        _win_enable_dark_titlebar(self)
        self._build()

    def _build(self):
        tk.Label(self, text="EXE name (e.g. game.exe)",
                 font=(FONT, 10, "bold"), fg=TEXT, bg=BG).pack(anchor="w", padx=16, pady=(14, 4))
        self._var = tk.StringVar()
        entry = tk.Entry(self, textvariable=self._var,
                         font=(FONT, 11, "bold"), bg="#2a2a2a", fg=TEXT,
                         insertbackground=TEXT, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=SEL_BG)
        entry.pack(fill="x", padx=16, pady=4)
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._ok())

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=10)
        for label, cmd in [("OK", self._ok), ("Cancel", self.destroy)]:
            tk.Button(btn_row, text=label, command=cmd,
                      font=(FONT, 9, "bold"), bg=BTN_BG, fg=BTN_FG,
                      relief="flat", padx=10, pady=3,
                      activebackground="#4a4a4a").pack(side="left", padx=5)

    def _ok(self):
        val = self._var.get().strip()
        if not val:
            messagebox.showwarning("EXE name", "Enter an EXE name.", parent=self)
            return

        if not val.lower().endswith(".exe"):
            val += ".exe"
        self.result = val.lower()
        self.destroy()


class EditorDialog(tk.Toplevel):
    def __init__(self, parent, proc: psutil.Process,
                 proc_name: str, rules: dict):
        super().__init__(parent)
        self.parent    = parent
        self.proc      = proc
        self.proc_name = proc_name
        self.rules     = rules
        self.title(f"Edit: {proc_name}")
        self.resizable(True, True)
        self.configure(bg=BG)
        self.grab_set()

        w, h = 920, 620
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(820, 560)
        self.update_idletasks()
        _win_enable_dark_titlebar(self)
        existing = rules.get(proc_name.lower(), {})
        self._build(existing)

    def _build(self, existing):
        pad = dict(padx=16, pady=5)

        tk.Label(self, text=self.proc_name,
                 font=(FONT, 15, "bold"), fg=TEXT, bg=BG).pack(anchor="w", **pad)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)


        self._lbl("CPU Priority")
        self.pri_var   = tk.StringVar(value=existing.get("priority", "Normal"))
        self.pri_frame = tk.Frame(self, bg=BG)
        self.pri_frame.pack(anchor="w", padx=24, pady=2)
        row1 = tk.Frame(self.pri_frame, bg=BG)
        row1.pack(anchor="w")
        row2 = tk.Frame(self.pri_frame, bg=BG)
        row2.pack(anchor="w")
        self._pri_radios = []
        for i, pri in enumerate(PRIORITY_MAP.keys()):
            r = tk.Radiobutton(
                (row1 if i < 3 else row2), text=pri,
                variable=self.pri_var, value=pri,
                font=(FONT, 10, "bold"), fg=TEXT, bg=BG,
                selectcolor=SEL_BG, activebackground=BG, activeforeground=TEXT
            )
            r.pack(side="left", padx=8)
            self._pri_radios.append(r)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)


        self._lbl("I/O Priority")
        self.io_var   = tk.IntVar(value=existing.get("io_priority", 2))
        self.io_frame = tk.Frame(self, bg=BG)
        self.io_frame.pack(anchor="w", padx=24, pady=2)
        self._io_radios = []
        for i, name in enumerate(IO_PRIORITY_NAMES):
            r = tk.Radiobutton(
                self.io_frame, text=name,
                variable=self.io_var, value=i,
                font=(FONT, 10, "bold"), fg=TEXT, bg=BG,
                selectcolor=SEL_BG, activebackground=BG, activeforeground=TEXT
            )
            r.pack(side="left", padx=8)
            self._io_radios.append(r)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)


        self._lbl(f"CPU Sets  ({CPU_COUNT} logical CPUs)")

        aff_outer = tk.Frame(self, bg=BG)
        aff_outer.pack(anchor="w", padx=24, pady=2)


        import math
        COLS_PER_ROW = min(8, math.ceil(CPU_COUNT / max(1, math.ceil(CPU_COUNT / 8))))

        saved_aff     = existing.get("affinity", list(range(CPU_COUNT)))
        self.aff_vars = []
        self._aff_cbs = []
        for i in range(CPU_COUNT):
            var = tk.BooleanVar(value=(i in saved_aff))
            self.aff_vars.append(var)
            cb = tk.Checkbutton(
                aff_outer, text=f"CPU {i}", variable=var,
                font=(FONT, 10, "bold"), fg=TEXT, bg=BG,
                selectcolor=SEL_BG, activebackground=BG, activeforeground=TEXT,
                width=7, anchor="w"
            )
            cb.grid(row=i // COLS_PER_ROW, column=i % COLS_PER_ROW,
                    padx=4, pady=2, sticky="w")
            self._aff_cbs.append(cb)

        quick = tk.Frame(self, bg=BG)
        quick.pack(anchor="w", padx=24, pady=4)
        self._quick_btns = []

        def _smt_on():
            for v in self.aff_vars:
                v.set(True)

        def _smt_off():
            for i, v in enumerate(self.aff_vars):
                v.set(i % 2 == 0)

        def _ht_on():
            for v in self.aff_vars:
                v.set(True)

        def _ht_off():
            core_map = CPU_TOPOLOGY.get("core_map")
            if core_map:
                p_indices = [i for i, is_e in enumerate(core_map) if not is_e]
                disable = set(p_indices[j] for j in range(1, len(p_indices), 2))
                for i, v in enumerate(self.aff_vars):
                    v.set(i not in disable)
            else:
                e_start = CPU_TOPOLOGY["e_thread_start"]
                for i, v in enumerate(self.aff_vars):
                    if e_start > 0 and i >= e_start:
                        v.set(True)
                    else:
                        v.set(i % 2 == 0)

        def _disable_ccd1():


            start = CPU_TOPOLOGY["ccd1_start"]
            for i, v in enumerate(self.aff_vars):
                v.set(i < start)

        def _disable_ccd0():


            start = CPU_TOPOLOGY["ccd1_start"]
            for i, v in enumerate(self.aff_vars):
                v.set(i >= start)

        def _disable_ecores():

            core_map = CPU_TOPOLOGY.get("core_map")
            if core_map:
                for i, v in enumerate(self.aff_vars):
                    v.set(not core_map[i])
                return

            e_start = CPU_TOPOLOGY["e_thread_start"]
            if e_start <= 0:
                messagebox.showinfo(
                    "E-cores",
                    "No E-cores detected on this CPU (or topology could not be read).\n"
                    f"CPU: {CPU_TOPOLOGY['brand'] or 'Unknown'}"
                )
                return
            for i, v in enumerate(self.aff_vars):
                v.set(i < e_start)

        def _disable_pcores():

            core_map = CPU_TOPOLOGY.get("core_map")
            if core_map:
                for i, v in enumerate(self.aff_vars):
                    v.set(core_map[i])
                return

            e_start = CPU_TOPOLOGY["e_thread_start"]
            if e_start <= 0:
                messagebox.showinfo(
                    "P-cores",
                    "No E-cores detected on this CPU, so P-cores cannot be isolated\n"
                    "(or topology could not be read).\n"
                    f"CPU: {CPU_TOPOLOGY['brand'] or 'Unknown'}"
                )
                return
            for i, v in enumerate(self.aff_vars):
                v.set(i >= e_start)

        topo = CPU_TOPOLOGY
        quick_buttons = [
            ("All",  lambda: [v.set(True)  for v in self.aff_vars]),
            ("None", lambda: [v.set(False) for v in self.aff_vars]),
        ]
        if topo["vendor"] != "intel":
            quick_buttons += [
                ("SMT On",  _smt_on),
                ("SMT Off", _smt_off),
            ]
        elif topo["vendor"] == "intel" and topo.get("ht_enabled", True):
            quick_buttons += [
                ("HT On",  _ht_on),
                ("HT Off", _ht_off),
            ]

        if topo["vendor"] == "amd" and topo["has_ccd1"]:
            quick_buttons.append(("CCD1 Off", _disable_ccd1))
            quick_buttons.append(("CCD0 Off", _disable_ccd0))
        if (topo["vendor"] == "intel"
                and topo["e_thread_start"] > 0
                and not topo["e_cores_disabled_in_bios"]):
            quick_buttons.append(("E-Cores Off", _disable_ecores))
            quick_buttons.append(("P-Cores Off", _disable_pcores))

        for label, fn in quick_buttons:
            b = tk.Button(quick, text=label, command=fn,
                          font=(FONT, 8, "bold"), bg=BTN_BG, fg=BTN_FG,
                          relief="flat", padx=6, pady=2, activebackground="#4a4a4a")
            b.pack(side="left", padx=3)
            self._quick_btns.append(b)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)


        self._lbl("Power Plan")
        pp_row = tk.Frame(self, bg=BG)
        pp_row.pack(anchor="w", padx=24, pady=2)

        FOLLOW_CURRENT = "Follow Current"
        self._pp_guid_by_name = {FOLLOW_CURRENT: None}
        pp_names = [FOLLOW_CURRENT]
        for name, guid, _active in list_power_plans():
            self._pp_guid_by_name[name] = guid
            pp_names.append(name)

        saved_guid = existing.get("power_plan")
        saved_name = FOLLOW_CURRENT
        if saved_guid:
            for name, guid in self._pp_guid_by_name.items():
                if guid and guid.lower() == saved_guid.lower():
                    saved_name = name
                    break
            else:
                # The rule points at a GUID that no longer exists on this
                # system (plan renamed/deleted). Keep it visible instead of
                # silently dropping the user's saved choice.
                saved_name = f"Unknown Plan ({saved_guid})"
                self._pp_guid_by_name[saved_name] = saved_guid
                pp_names.append(saved_name)

        self.pp_var   = tk.StringVar(value=saved_name)
        self.pp_combo = ttk.Combobox(
            pp_row, textvariable=self.pp_var, values=pp_names,
            state="readonly", width=28, style="AM.TCombobox",
            font=(FONT, 10, "bold"))
        self.pp_combo.pack(side="left")

        def _refresh_plans():
            current = self.pp_var.get()
            names = [FOLLOW_CURRENT]
            self._pp_guid_by_name = {FOLLOW_CURRENT: None}
            for name, guid, _active in list_power_plans():
                self._pp_guid_by_name[name] = guid
                names.append(name)
            if current not in self._pp_guid_by_name and current != FOLLOW_CURRENT:
                names.append(current)   # keep the current selection visible
                self._pp_guid_by_name[current] = self._pp_guid_by_name.get(current)
            self.pp_combo["values"] = names

        tk.Button(pp_row, text="\u21bb", command=_refresh_plans,
                  font=(FONT, 9, "bold"), bg=BTN_BG, fg=BTN_FG,
                  relief="flat", padx=8, pady=2,
                  activebackground="#4a4a4a").pack(side="left", padx=(6, 0))

        tk.Label(self,
                 text="Power Plans are system-wide in Windows, not truly per-process.\n"
                      "ProcessX activates it while this EXE is running and restores the\n"
                      "previous plan once no managed process needs it anymore.",
                 font=(FONT, 8), fg=TEXT_DIM, bg=BG, justify="left").pack(
            anchor="w", padx=24, pady=(4, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=4)
        for label, cmd in [
            ("Apply & Save", self._apply_save),
            ("Close",        self.destroy),
        ]:
            tk.Button(btn_row, text=label, command=cmd,
                      font=(FONT, 9, "bold"), bg=BTN_BG, fg=BTN_FG,
                      relief="flat", padx=10, pady=4,
                      activebackground="#4a4a4a").pack(side="left", padx=6)

        self.result_lbl = tk.Label(self, text="", font=(FONT, 8, "bold"), fg=TEXT_DIM, bg=BG)
        self.result_lbl.pack()

    def _lbl(self, text):
        tk.Label(self, text=text, font=(FONT, 10, "bold"),
                 fg=TEXT, bg=BG).pack(anchor="w", padx=16)

    def _apply_save(self):
        rule = {}
        aff = [i for i, v in enumerate(self.aff_vars) if v.get()]
        if not aff:
            messagebox.showwarning("Affinity", "Select at least one CPU.")
            return
        rule["priority"]    = self.pri_var.get()
        rule["io_priority"] = self.io_var.get()
        rule["affinity"]    = aff
        pp_guid = self._pp_guid_by_name.get(self.pp_var.get())
        if pp_guid:
            rule["power_plan"] = pp_guid

        old_rule = self.rules.get(self.proc_name.lower(), {})
        exe_path = old_rule.get("exe_path")
        for p in psutil.process_iter(["name", "exe"]):
            try:
                if (p.info["name"] or "").lower() == self.proc_name.lower() and p.info["exe"]:
                    exe_path = p.info["exe"]
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if exe_path:
            rule["exe_path"] = exe_path

        self.rules[self.proc_name.lower()] = rule
        save_rules(self.rules)

        applied_any  = False
        instance_ct  = 0
        all_msgs     = []
        any_failed   = False
        for p in psutil.process_iter(["name", "pid"]):
            try:
                if (p.info["name"] or "").lower() != self.proc_name.lower():
                    continue
                instance_ct += 1
                msgs = apply_rule(psutil.Process(p.info["pid"]), rule)
                if any("FAILED" in m for m in msgs):
                    any_failed = True
                all_msgs.extend(msgs)
                applied_any = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if applied_any:
            summary = f"Applied to {instance_ct} running instance(s)"
            if any_failed:
                summary += " (some failed)"
            self.result_lbl.config(text=summary + ":  " + "  |  ".join(dict.fromkeys(all_msgs)))
            if pp_guid:
                _power_plan_manager.on_process_seen(self.proc_name.lower(), pp_guid)
            else:
                _power_plan_manager.on_process_gone(self.proc_name.lower())
        else:
            self.result_lbl.config(text="Saved — will apply automatically when the EXE starts.")

        self.parent._refresh_rules_list()
        self.parent._refresh_proc_list()
        self.parent.status_var.set(f"Rule saved for {self.proc_name}")

    def _delete_rule(self):
        key = self.proc_name.lower()
        if key in self.rules:
            del self.rules[key]
            save_rules(self.rules)
            revert_running_instances(self.proc_name)
            _power_plan_manager.on_process_gone(key)
            self.parent.status_var.set(f"Rule deleted for {self.proc_name} — reverted to defaults")
        self.destroy()
        self.parent._refresh_rules_list()
        self.parent._refresh_proc_list()


def _acquire_single_instance_mutex() -> object:
    MUTEX_NAME = "Global\\ProcessX_SingleInstance_jadenpeek"
    ERROR_ALREADY_EXISTS = 183

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    err    = ctypes.get_last_error()

    if not handle or err == ERROR_ALREADY_EXISTS:

        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            hwnd   = user32.FindWindowW(None, "ProcessX")
            if hwnd:
                SW_RESTORE = 9
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

        print("ProcessX is already running.", file=sys.stderr)
        sys.exit(0)

    return handle


if __name__ == "__main__":
    if "--apply-rules" in sys.argv:
        rules = load_rules()
        apply_all_rules(rules)
        sys.exit(0)

    _mutex_handle = _acquire_single_instance_mutex()

    app = ProcessX()
    app.mainloop()
