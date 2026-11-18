import ctypes
import os


def available_ram_gb():
    if os.name != "nt":
        return None
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                    ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
                    ("extended_free", ctypes.c_ulonglong)]
    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        return None
    return memory.available / 1024**3


def check_memory(model_id, device, dtype=None):
    if device != "cpu":
        return
    required = {"dinov2-small": .8, "qwen3vl-2b": 10, "moondream2": 5.5,
                "gemma3-4b": 18}.get(model_id, 0)
    if model_id == "qwen3vl-2b" and dtype == "bfloat16":
        required = 6
    if model_id == "qwen3vl-2b" and dtype == "vision_float32":
        required = 2.5
    free = available_ram_gb()
    if free is not None and free < required:
        raise RuntimeError(f"Zu wenig freier Arbeitsspeicher für dieses Modell im CPU-Betrieb: "
                           f"{free:.1f} GB frei, vorsichtig geschätzt {required:g} GB benötigt. "
                           "Andere Programme schließen oder ein kleineres Modell wählen.")
