import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import queue
import ctypes
from ctypes import wintypes

import numpy as np
import soundcard as sc

# Windows SendInput keyboard injection
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
ULONG_PTR = wintypes.WPARAM

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]

VK = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "SHIFT": 0x10,
    "CTRL": 0x11, "ALT": 0x12, "PAUSE": 0x13, "CAPSLOCK": 0x14,
    "ESC": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26,
    "RIGHT": 0x27, "DOWN": 0x28, "INSERT": 0x2D, "DELETE": 0x2E,
}
for i in range(1, 25):
    VK[f"F{i}"] = 0x70 + i - 1
for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK[c] = ord(c)
for c in "0123456789":
    VK[c] = ord(c)

# VkKeyScanExW interprets a typed character using the active Windows layout.
_MODIFIER_VKS = {1: 0x10, 2: 0x11, 4: 0x12}  # Shift, Ctrl, Alt

def _send_inputs(items):
    inputs = (INPUT * len(items))()
    for index, (vk, flags) in enumerate(items):
        inputs[index].type = INPUT_KEYBOARD
        inputs[index].ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    sent = ctypes.windll.user32.SendInput(
        len(inputs), ctypes.byref(inputs), ctypes.sizeof(INPUT)
    )
    if sent != len(inputs):
        raise ctypes.WinError()

def resolve_key(name):
    """Return (virtual_key, modifiers) for a named key or one typed character."""
    raw = name.strip()
    named = raw.upper()
    if named in VK:
        return VK[named], []
    if len(raw) != 1:
        raise ValueError(
            f"Nepodporovaná klávesa: {raw}. Zadejte název klávesy "
            "(např. F8, ENTER) nebo jeden znak podle českého rozložení klávesnice."
        )

    user32 = ctypes.windll.user32
    user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    user32.GetKeyboardLayout.restype = wintypes.HKL
    user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, wintypes.HKL]
    user32.VkKeyScanExW.restype = ctypes.c_short
    layout = user32.GetKeyboardLayout(0)
    mapped = user32.VkKeyScanExW(raw, layout)
    if mapped == -1:
        raise ValueError(
            f"Znak {raw!r} nelze převést podle aktuálního rozložení klávesnice."
        )
    vk = mapped & 0xFF
    shift_state = (mapped >> 8) & 0xFF
    modifiers = [
        modifier_vk for bit, modifier_vk in _MODIFIER_VKS.items()
        if shift_state & bit
    ]
    return vk, modifiers

def press_key(name):
    vk, modifiers = resolve_key(name)
    events = []
    for modifier in modifiers:
        events.append((modifier, 0))
    events.extend([(vk, 0), (vk, KEYEVENTF_KEYUP)])
    for modifier in reversed(modifiers):
        events.append((modifier, KEYEVENTF_KEYUP))
    _send_inputs(events)

class App:
    def __init__(self, root):
        self.root = root
        root.title("Zvukový spouštěč")
        root.geometry("410x430")
        root.resizable(False, False)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.running = False

        frm = ttk.Frame(root, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Hranice hlasitosti (%)").grid(row=0, column=0, sticky="w")
        self.threshold = tk.IntVar(value=50)
        self.threshold_label = ttk.Label(frm, text="50 %")
        self.threshold_label.grid(row=0, column=1, sticky="e")
        scale = ttk.Scale(frm, from_=1, to=100, variable=self.threshold,
                          command=lambda v: self.threshold_label.config(text=f"{int(float(v))} %"))
        scale.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 12))

        ttk.Label(frm, text="První klávesa").grid(row=2, column=0, sticky="w")
        self.key1 = tk.StringVar(value="F8")
        ttk.Entry(frm, textvariable=self.key1, width=14).grid(row=2, column=1, sticky="e", pady=4)

        ttk.Label(frm, text="Druhá klávesa").grid(row=3, column=0, sticky="w")
        self.key2 = tk.StringVar(value="F9")
        ttk.Entry(frm, textvariable=self.key2, width=14).grid(row=3, column=1, sticky="e", pady=4)

        ttk.Label(frm, text="Prodleva (ms, max. 2000)").grid(row=4, column=0, sticky="w")
        self.delay = tk.IntVar(value=500)
        ttk.Spinbox(frm, from_=0, to=2000, increment=50, textvariable=self.delay,
                    width=11).grid(row=4, column=1, sticky="e", pady=4)

        self.fallback_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm, text="Použít záložní časovač", variable=self.fallback_enabled
        ).grid(row=5, column=0, sticky="w", pady=(8, 2))
        self.fallback_seconds = tk.IntVar(value=5)
        ttk.Spinbox(
            frm, from_=1, to=20, increment=1,
            textvariable=self.fallback_seconds, width=8
        ).grid(row=5, column=1, sticky="e", pady=(8, 2))
        ttk.Label(frm, text="Čas v sekundách (1–20); při timeoutu se stiskne druhá klávesa").grid(
            row=6, column=0, columnspan=2, sticky="w"
        )

        ttk.Separator(frm).grid(row=7, column=0, columnspan=2, sticky="ew", pady=10)
        self.status = tk.StringVar(value="Zastaveno")
        ttk.Label(frm, textvariable=self.status, wraplength=375).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.level = tk.StringVar(value="Aktuální úroveň: —")
        ttk.Label(frm, textvariable=self.level).grid(row=9, column=0, columnspan=2, sticky="w")

        self.start_btn = ttk.Button(frm, text="Spustit sledování", command=self.start)
        self.start_btn.grid(row=10, column=0, sticky="ew", pady=12)
        self.stop_btn = ttk.Button(frm, text="Zastavit", command=self.stop, state="disabled")
        self.stop_btn.grid(row=10, column=1, sticky="ew", padx=(8, 0), pady=12)
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self.poll_events)

    def start(self):
        try:
            k1, k2 = self.key1.get().strip(), self.key2.get().strip()
            if not k1 or not k2:
                raise ValueError("Vyplňte obě klávesy.")
            # Validate both entries against the current Windows keyboard layout.
            resolve_key(k1)
            resolve_key(k2)
            delay = int(self.delay.get())
            if not 0 <= delay <= 2000:
                raise ValueError("Prodleva musí být v rozsahu 0 až 2000 ms.")
            threshold = int(self.threshold.get())
            fallback_enabled = bool(self.fallback_enabled.get())
            fallback_seconds = int(self.fallback_seconds.get())
            if not 1 <= fallback_seconds <= 20:
                raise ValueError("Záložní časovač musí být v rozsahu 1 až 20 sekund.")
        except Exception as e:
            messagebox.showerror("Neplatné nastavení", str(e))
            return
        self.stop_event.clear()
        self.running = True
        self.worker = threading.Thread(
            target=self.monitor,
            args=(threshold, k1, k2, delay, fallback_enabled, fallback_seconds),
            daemon=True,
        )
        self.worker.start()
        self.status.set("Sleduji zvukový výstup. Spuštění nastane při překročení hranice.")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def monitor(self, threshold, k1, k2, delay, fallback_enabled=False, fallback_seconds=5):
        try:
            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            above = False
            wait_started = time.monotonic()
            fallback_fired = False
            with mic.recorder(samplerate=48000, channels=2, blocksize=4800) as recorder:
                while not self.stop_event.is_set():
                    data = recorder.record(numframes=4800)
                    rms = float(np.sqrt(np.mean(np.square(data))))
                    level = min(100.0, rms * 100.0)
                    self.events.put(("level", level))
                    is_above = level >= threshold

                    if is_above and not above:
                        self.events.put(("status", f"Hranice překročena ({level:.1f} %). Posílám {k1}…"))
                        press_key(k1)
                        if delay:
                            end = time.monotonic() + delay / 1000.0
                            while time.monotonic() < end and not self.stop_event.is_set():
                                time.sleep(0.01)
                        if not self.stop_event.is_set():
                            press_key(k2)
                            self.events.put(("status", f"Provedeno {k1} → {k2}. Čekám na pokles pod hranici a další překročení."))

                    elif not is_above and above:
                        # A new fallback waiting period begins after the signal falls below threshold.
                        wait_started = time.monotonic()
                        fallback_fired = False

                    elif (not is_above and not above and fallback_enabled
                          and not fallback_fired
                          and time.monotonic() - wait_started >= fallback_seconds):
                        # Fallback deliberately presses only key 2, and only once per waiting cycle.
                        press_key(k2)
                        fallback_fired = True
                        self.events.put(("status", f"Záložní časovač ({fallback_seconds} s): stisknuto {k2}. Čekám na zvukový signál."))

                    above = is_above
        except Exception as e:
            self.events.put(("error", str(e)))

    def poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "level":
                    self.level.set(f"Aktuální úroveň: {value:.1f} %")
                elif kind == "status":
                    self.status.set(value)
                elif kind == "error":
                    self.status.set("Chyba při sledování zvuku.")
                    messagebox.showerror("Chyba zvukového vstupu", value)
                    self.stop()
        except queue.Empty:
            pass
        self.root.after(150, self.poll_events)

    def stop(self):
        self.stop_event.set()
        self.running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status.set("Zastaveno.")

    def close(self):
        self.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
