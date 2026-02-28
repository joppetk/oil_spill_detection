#!/usr/bin/env python3
import os
import sys
import json
import time
import signal
import ipaddress
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import hashlib
import secrets





VENV_PY_CANDIDATES = [
    "/home/pi/drone-venv/bin/python3",
    "/home/pi/drone-venv/bin/python",
]

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
PID_FILE = Path("/tmp/drone_service.pid")
LOG_PATH = APP_DIR / "service.log"

DEFAULTS = {
    "drone_id": "PH_UAV_002",
    "server_ip": "192.168.1.50",
    "sim_port": 14540,
    "service_path": str(APP_DIR / "service.py"),
    "auth": {
        "username": "admin",
        # password is set on first run (default "admin")
        "salt_hex": "",
        "pbkdf2_hex": "",
        "iters": 150_000,
    },
}

def _pbkdf2_hash(password: str, salt: bytes, iters: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)

def _ensure_auth(cfg: dict) -> None:
    auth = cfg.get("auth", {})
    if not auth.get("salt_hex") or not auth.get("pbkdf2_hex"):
        salt = secrets.token_bytes(16)
        iters = int(auth.get("iters", 150_000))
        digest = _pbkdf2_hash("admin", salt, iters)
        cfg["auth"] = {
            "username": auth.get("username", "admin"),
            "salt_hex": salt.hex(),
            "pbkdf2_hex": digest.hex(),
            "iters": iters,
        }

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        cfg = DEFAULTS.copy()
        _ensure_auth(cfg)
        save_config(cfg)
        return cfg

    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = DEFAULTS.copy()

    # merge missing defaults
    merged = DEFAULTS.copy()
    merged.update(cfg)
    if "auth" not in merged:
        merged["auth"] = DEFAULTS["auth"].copy()
    else:
        a = DEFAULTS["auth"].copy()
        a.update(merged["auth"])
        merged["auth"] = a

    _ensure_auth(merged)
    save_config(merged)
    return merged

def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def validate_ip(ip_str: str) -> bool:
    try:
        ipaddress.ip_address(ip_str.strip())
        return True
    except ValueError:
        return False

def validate_port(port_str: str) -> bool:
    try:
        p = int(port_str.strip())
        return 1 <= p <= 65535
    except ValueError:
        return False

def stop_existing_service():
    if not PID_FILE.exists():
        return
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return

    # If we started with start_new_session=True, PID is also a process group leader
    try:
        os.killpg(pid, signal.SIGTERM)
        time.sleep(0.5)
    except ProcessLookupError:
        pass
    except PermissionError:
        # fallback: try killing just pid
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

    PID_FILE.unlink(missing_ok=True)



def _venv_python() -> str:
    for p in VENV_PY_CANDIDATES:
        if Path(p).exists():
            return p
    # fallback (not ideal, but prevents crash if venv path wrong)
    return sys.executable or "/usr/bin/python3"

def start_service(service_path: str, drone_id: str, sim_port: int):
    py = _venv_python()
    cmd = [
        py, service_path,
        "--allow-get",
        "--sys-addr", f"udp://0.0.0.0:{sim_port}",
        "--drone-id", drone_id,
    ]

    logf = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
    p = subprocess.Popen(
        cmd,
        stdout=logf,
        stderr=logf,
        start_new_session=True,
        cwd=str(APP_DIR),
    )
    return p.pid

def open_firefox_kiosk(server_ip: str):
    url = f"https://{server_ip}:8181/pi.html"
    # Add --private-window if you prefer clean kiosk sessions
    subprocess.Popen(
        ["firefox", "--kiosk", "--new-window", url],
        start_new_session=True
    )

class LoginFrame(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=24)
        self.app = app

        card = ttk.Frame(self, padding=24)
        card.place(relx=0.5, rely=0.5, anchor="center")

        title = ttk.Label(card, text="Drone Companion Dashboard", style="Title.TLabel")
        subtitle = ttk.Label(card, text="Please sign in to continue", style="Sub.TLabel")
        title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        subtitle.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 18))

        ttk.Label(card, text="Username").grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.user = ttk.Entry(card, width=28)
        self.user.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        ttk.Label(card, text="Password").grid(row=4, column=0, sticky="w", pady=(0, 6))
        self.pw = ttk.Entry(card, show="•", width=28)
        self.pw.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.show_pw = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(card, text="Show password", variable=self.show_pw, command=self._toggle_pw)
        chk.grid(row=6, column=0, sticky="w", pady=(0, 16))

        btn = ttk.Button(card, text="Login", command=self._login)
        btn.grid(row=7, column=0, sticky="ew")

        quit_btn = ttk.Button(card, text="Quit", command=self.app.quit_app)
        quit_btn.grid(row=7, column=1, sticky="ew", padx=(10, 0))

        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        self.user.focus_set()

    def _toggle_pw(self):
        self.pw.configure(show="" if self.show_pw.get() else "•")

    def _login(self):
        cfg = self.app.cfg
        auth = cfg["auth"]
        username_ok = (self.user.get().strip() == auth["username"])

        try:
            salt = bytes.fromhex(auth["salt_hex"])
            iters = int(auth.get("iters", 150_000))
            digest = _pbkdf2_hash(self.pw.get(), salt, iters).hex()
            password_ok = (digest == auth["pbkdf2_hex"])
        except Exception:
            password_ok = False

        if username_ok and password_ok:
            self.app.show_settings()
        else:
            messagebox.showerror("Login failed", "Invalid username or password.")

class SettingsFrame(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=20)
        self.app = app

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Mission Settings", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Logout", command=self.app.show_login).pack(side="right")

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        self.drone_id_var = tk.StringVar(value=self.app.cfg.get("drone_id", "PH_UAV_002"))
        self.server_ip_var = tk.StringVar(value=self.app.cfg.get("server_ip", "192.168.1.50"))
        self.sim_port_var = tk.StringVar(value=str(self.app.cfg.get("sim_port", 14540)))

        row = 0
        ttk.Label(body, text="Drone ID").grid(row=row, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(body, textvariable=self.drone_id_var, width=40).grid(row=row+1, column=0, sticky="ew", pady=(0, 14))

        row += 2
        ttk.Label(body, text="Server IP Address").grid(row=row, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(body, textvariable=self.server_ip_var, width=40).grid(row=row+1, column=0, sticky="ew", pady=(0, 14))

        row += 2
        ttk.Label(body, text="Simulator Port").grid(row=row, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(body, textvariable=self.sim_port_var, width=20).grid(row=row+1, column=0, sticky="w", pady=(0, 16))

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(body, textvariable=self.status, style="Muted.TLabel").grid(row=row+2, column=0, sticky="w", pady=(8, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 0))

        ttk.Button(actions, text="Save Settings", command=self._save).pack(side="left")
        self.start_btn = ttk.Button(actions, text="Start Mission", command=self._start_mission)
        self.start_btn.pack(side="right")

        body.columnconfigure(0, weight=1)

    def _save(self) -> bool:
        drone_id = self.drone_id_var.get().strip()
        server_ip = self.server_ip_var.get().strip()
        sim_port_str = self.sim_port_var.get().strip()

        if not drone_id:
            messagebox.showerror("Validation error", "Drone ID cannot be empty.")
            return False
        if not validate_ip(server_ip):
            messagebox.showerror("Validation error", "Server IP Address is not valid.")
            return False
        if not validate_port(sim_port_str):
            messagebox.showerror("Validation error", "Simulator Port must be an integer from 1 to 65535.")
            return False

        self.app.cfg["drone_id"] = drone_id
        self.app.cfg["server_ip"] = server_ip
        self.app.cfg["sim_port"] = int(sim_port_str)
        save_config(self.app.cfg)

        self.status.set("Settings saved.")
        return True

    def _start_mission(self):
        if not self._save():
            return

        drone_id = self.app.cfg["drone_id"]
        server_ip = self.app.cfg["server_ip"]
        sim_port = self.app.cfg["sim_port"]
        service_path = self.app.cfg.get("service_path", str(APP_DIR / "service.py"))

        if not Path(service_path).exists():
            messagebox.showerror("Missing file", f"service.py not found at:\n{service_path}")
            return

        self.start_btn.configure(state="disabled")
        self.status.set("Starting background service...")

        try:
            stop_existing_service()
            pid = start_service(service_path, drone_id, sim_port)
            self.status.set(f"Service started (PID {pid}). Opening mission UI in 5 seconds...")

            # After 5 seconds, open Firefox kiosk to the remote page
            self.after(5000, lambda: self._open_kiosk(server_ip))

        except Exception as e:
            self.start_btn.configure(state="normal")
            messagebox.showerror("Start failed", str(e))
            self.status.set("Failed to start.")

    def _open_kiosk(self, server_ip: str):
        try:
            open_firefox_kiosk(server_ip)
            self.status.set("Firefox kiosk launched.")
            # Optional: hide the dashboard so only the kiosk is visible
            self.app.root.withdraw()
        except Exception as e:
            self.start_btn.configure(state="normal")
            messagebox.showerror("Firefox launch failed", str(e))
            self.status.set("Failed to launch Firefox.")

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()

        root.title("Drone Companion Dashboard")
        root.attributes("-fullscreen", True)
        root.bind("<Escape>", lambda e: self.quit_app())  # for maintenance
        root.configure(bg="#0b0f14")

        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Title.TLabel", font=("DejaVu Sans", 20, "bold"))
        style.configure("Sub.TLabel", font=("DejaVu Sans", 11))
        style.configure("Muted.TLabel", font=("DejaVu Sans", 10))
        style.configure("TButton", font=("DejaVu Sans", 11))
        style.configure("TEntry", font=("DejaVu Sans", 11))

        self.container = ttk.Frame(root)
        self.container.pack(fill="both", expand=True)

        self.login_frame = LoginFrame(self.container, self)
        self.settings_frame = SettingsFrame(self.container, self)

        self.show_login()

    def show_login(self):
        self.root.deiconify()
        self.settings_frame.pack_forget()
        self.login_frame.pack(fill="both", expand=True)

    def show_settings(self):
        self.login_frame.pack_forget()
        # refresh vars from config if changed externally
        self.settings_frame.drone_id_var.set(self.cfg.get("drone_id", "PH_UAV_002"))
        self.settings_frame.server_ip_var.set(self.cfg.get("server_ip", "192.168.1.50"))
        self.settings_frame.sim_port_var.set(str(self.cfg.get("sim_port", 14540)))
        self.settings_frame.pack(fill="both", expand=True)

    def quit_app(self):
        self.root.destroy()

def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
