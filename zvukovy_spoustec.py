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
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

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

def press_key(name):
    name = name.strip().upper()
    if name not in VK:
        raise ValueError(f"Nepodporovaná klávesa: {name}")
    code = VK[name]
    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].union.ki = KEYBDINPUT(code, 0, 0, 0, 0)
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].union.ki = KEYBDINPUT(code, 0, KEYEVENTF_KEYUP, 0, 0)
    if ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT)) != 2:
        raise ctypes.WinError()

class App:
    def __init__(self, root):
        self.root = root
        root.title("Zvukový spouštěč")
        root.geometry("390x365")
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

        ttk.Separator(frm).grid(row=5, column=0, columnspan=2, sticky="ew", pady=12)
        self.status = tk.StringVar(value="Zastaveno")
        ttk.Label(frm, textvariable=self.status, wraplength=350).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.level = tk.StringVar(value="Aktuální úroveň: —")
        ttk.Label(frm, textvariable=self.level).grid(row=7, column=0, columnspan=2, sticky="w")

        self.start_btn = ttk.Button(frm, text="Spustit sledování", command=self.start)
        self.start_btn.grid(row=8, column=0, sticky="ew", pady=14)
        self.stop_btn = ttk.Button(frm, text="Zastavit", command=self.stop, state="disabled")
        self.stop_btn.grid(row=8, column=1, sticky="ew", padx=(8, 0), pady=14)
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self.poll_events)

    def start(self):
        try:
            k1, k2 = self.key1.get().strip().upper(), self.key2.get().strip().upper()
            if k1 not in VK or k2 not in VK:
                raise ValueError("Použijte např. A–Z, 0–9, F1–F24, ENTER, SPACE, ESC, CTRL, ALT nebo SHIFT.")
            delay = int(self.delay.get())
            if not 0 <= delay <= 2000:
                raise ValueError("Prodleva musí být v rozsahu 0 až 2000 ms.")
            threshold = int(self.threshold.get())
        except Exception as e:
            messagebox.showerror("Neplatné nastavení", str(e))
            return
        self.stop_event.clear()
        self.running = True
        self.worker = threading.Thread(target=self.monitor, args=(threshold, k1, k2, delay), daemon=True)
        self.worker.start()
        self.status.set("Sleduji zvukový výstup. Spuštění nastane při překročení hranice.")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def monitor(self, threshold, k1, k2, delay):
        try:
            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            above = False
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
