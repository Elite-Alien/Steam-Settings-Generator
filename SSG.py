#!/usr/bin/env python3
import os, sys, re, json, argparse, difflib, pathlib, requests, shutil, subprocess, threading, queue, time, webbrowser, zipfile, bcrypt, base64, ctypes
from collections import defaultdict
from tkinter import Canvas, Scrollbar, Frame, Label, ttk, Checkbutton, Button, Entry, Radiobutton, filedialog, messagebox
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from typing import Dict, Any, Optional
import tkinter as tk

all_html_files: list[Path] = []
file_status: dict[Path, str] = {}
_prompt_handled = {}
_prompt_handled_lock = threading.Lock()
game_content_lock = defaultdict(threading.Lock)
dlc_lock = threading.Lock()
_download_done: dict[Path, bool] = {}

def show_custom_dialog(parent, dialog_type, title, message, buttons=None, callback=None):
    dialog = CustomModalDialog(parent, title, message, dialog_type, buttons, callback)
    return dialog.show()

def _open_folder(path: Path) -> None:
    try:
        path = path.resolve()
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform.startswith("darwin"):
            subprocess.run(["open", str(path)], check=True)
        else:
            methods = [
                lambda: subprocess.run(["xdg-open", str(path)], check=True),
                lambda: subprocess.run(
                    ["dbus-send", "--print-reply", "--dest=org.freedesktop.FileManager1",
                     "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowFolders",
                     f"array:string:file://{path}", "string:''"], 
                    check=True
                ),
                lambda: subprocess.run(["gio", "open", str(path)], check=True),
                lambda: subprocess.run(["mimeopen", "-d", str(path)], check=True),
                lambda: subprocess.run(["caja", str(path)], check=True),
                lambda: subprocess.run(["nautilus", str(path)], check=True),
                lambda: subprocess.run(["dolphin", str(path)], check=True),
                lambda: subprocess.run(["thunar", str(path)], check=True),
                lambda: subprocess.run(["pcmanfm", str(path)], check=True),
            ]
            
            for method in methods:
                try:
                    method()
                    return
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
                except Exception as e:
                    log_manager.log_message(f"Tried method but got error: {e}")

            log_manager.log_message(f"❌ Could not open folder. Path: {path}")
            log_manager.log_message("   Tried all known methods. Please open manually.")
    except Exception as e:
        log_manager.log_message(f"⚠️ Error opening folder: {e}")

def check_existing_completions() -> dict:
    log_manager.log_message("⏳ Checking for existing completed games...")
    progress_state = log_manager.load_progress_state()
    updated = False
    
    for folder in [HTML_FOLDER, OLD_HTML_FOLDER]:
        for html_path in folder.glob("*.html"):
            if html_path.name in progress_state and progress_state[html_path.name].get("percent") == 100:
                continue
            
            try:
                game_folder = None
                temp_file = TEMP_FOLDER / f"{html_path.name}.txt"
                if temp_file.exists():
                    for line in temp_file.read_text().splitlines():
                        if line.startswith("GAMEDIR="):
                            game_folder = Path(line.split("=", 1)[1].strip())
                            break
            
                if not game_folder:
                    try:
                        with html_path.open("r", encoding="utf-8") as f:
                            soup = BeautifulSoup(f, "html.parser")
                        game_name = clean_title(soup.find("h1", itemprop="name").text)
                        game_folder = GAMES_ROOT / game_name
                    except Exception:
                        continue
                        
                if not game_folder.exists():
                    continue
                    
                steam_settings = game_folder / "steam_settings"
                achievement_images = steam_settings / "achievement_images"
                if not steam_settings.exists() or not achievement_images.exists():
                    continue
                    
                try:
                    with html_path.open("r", encoding="utf-8") as f:
                        soup = BeautifulSoup(f, "html.parser")
                    required_images = set(collect_image_names(soup))
                except Exception:
                    continue
                    
                existing_images = set()
                for p in achievement_images.iterdir():
                    if p.is_file() and p.suffix.lower() == ".jpg":
                        existing_images.add(p.name)
                
                if required_images.issubset(existing_images):
                    progress_state[html_path.name] = {"percent": 100}
                    updated = True
                    log_manager.log_message(f"✅ Found complete installation for {html_path.name}")
                    
            except Exception as e:
                log_manager.log_error(Exception(f"⚠️ Error checking {html_path}: {e}"), "check_existing_completions")
    
    if updated:
        log_manager.save_progress_state(progress_state)
        log_manager.log_message("💾 Updated progress state with existing completions")
    
    return progress_state

def _gui_yes_no(question: str) -> bool:
    if tk is None:
        while True:
            resp = input(f"{question} (Y/N): ").strip().lower()
            if resp in ("y", "yes"):
                return True
            if resp in ("n", "no"):
                return False
            print("Please answer Yes or No.")

    if global_ui is not None:
        return show_custom_dialog(global_ui, "yesno", "Confirm", question)

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        result = messagebox.askyesno("Confirm", question, parent=root)
        root.destroy()
        return result
    except Exception:
        while True:
            resp = input(f"{question} (Y/N): ").strip().lower()
            if resp in ("y", "yes"):
                return True
            if resp in ("n", "no"):
                return False
            print("Please answer Yes or No.")

def hash_api_key(api_key: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(api_key.encode("utf-8"), salt)
    return base64.b64encode(salt + hashed).decode("utf-8")

def verify_api_key(stored_hash: str, input_key: str) -> bool:
    try:
        data = base64.b64decode(stored_hash.encode("utf-8"))
        salt = data[:32]
        stored_hashed = data[32:]
        return bcrypt.checkpw(input_key.encode("utf-8"), stored_hashed)
    except Exception:
        return False

#----------------------------------------------------------------------
class ProgressManager:
    def __init__(self):
        self._ui = None

    def set_ui(self, ui) -> None:
        self._ui = ui

    @staticmethod
    def _terminal_progress(current: int, total: int) -> None:
        percent = int(current / total * 100)
        filled = int(current / total * 30)
        bar = "·" + "·" * (30 - 1)
        bar = bar[:filled] + "●" + bar[filled + 1 :] if filled < 30 else bar
        sys.stdout.write(f"\r[{bar}] {percent:3d}%")
        sys.stdout.flush()

    @staticmethod
    def _noop_progress(_: int, __: int) -> None:
        pass

    def update_progress(self, percent: int, html_path: Path) -> None:
        state = log_manager.load_progress_state(TEMP_FOLDER)
        state[html_path.name] = {"percent": percent}
        log_manager.save_progress_state(state, TEMP_FOLDER)

        if self._ui and hasattr(self._ui, "_row_widgets"):
            def _safe_update():
                if not self._ui.winfo_exists() or html_path not in self._ui._row_widgets:
                    return
                widgets = self._ui._row_widgets[html_path]
                if widgets["progress"].winfo_exists():
                    widgets["progress"]["value"] = percent
                if widgets["percent"].winfo_exists():
                    widgets["percent"].config(text=f"{percent}%")
                if percent == 100:
                    ctrl_btn = widgets.get("ctrl")
                    if ctrl_btn and ctrl_btn.winfo_exists():
                        ctrl_btn.destroy()
                        widgets.pop("ctrl", None)
                self._ui.update_idletasks()
            self._ui.after(0, _safe_update)

    def _get_progress_cb(self, app_id: str, html_path: Path) -> callable:
        if self._ui is not None and hasattr(self._ui, "_row_widgets"):
            def _ui_row_progress(cur: int, tot: int, p=html_path):
                widgets = self._ui._row_widgets.get(p)
                if not widgets:
                    return
                if widgets["progress"].winfo_exists():
                    widgets["progress"]["maximum"] = tot
                    widgets["progress"]["value"] = cur
                if widgets["percent"].winfo_exists():
                    widgets["percent"].config(text=f"{int(cur / tot * 100)}%")
                self._ui.update_idletasks()
            return _ui_row_progress
        return self._terminal_progress

    def _choose_progress_cb(self, app_id: str, html_path: Path) -> callable:
        return self._get_progress_cb(app_id, html_path)

    def _ui_progress(self, cur: int, tot: int, html_path: Path, ui: "WatcherUI", folder: Path) -> None:
        if ui is None:
            return
        widgets = ui._row_widgets.get(html_path)
        if not widgets:
            return
        prog = widgets["progress"]
        perc = widgets["percent"]
        prog["maximum"] = tot
        prog["value"] = cur
        percent = int(cur / tot * 100)
        perc.config(text=f"{percent}%")
        ui.update_idletasks()
        state = log_manager.load_progress_state(folder)
        state[html_path.name] = {"percent": percent}
        log_manager.save_progress_state(state, folder)

progress_manager = ProgressManager()

# -------------------------------------------------------------------------
class JobTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.queued = 0
        self.active = 0

    def add_job(self, n: int = 1):
        with self._lock:
            self.queued += n

    def start_job(self):
        with self._lock:
            if self.queued > 0:
                self.queued -= 1
            self.active += 1

    def finish_job(self):
        with self._lock:
            self.active = max(0, self.active - 1)

    def snapshot(self):
        with self._lock:
            return self.queued, self.active


job_tracker = JobTracker()

def _run_main_with_progress():
    if global_ui is not None:
        global_ui.progress["maximum"] = 1
        global_ui.progress["value"] = 0
        global_ui.progress.pack()

    try:
        if global_ui is not None and html_path is not None:
            progress_cb = progress_manager._get_progress_cb("", html_path)
            globals()["_terminal_progress"] = progress_cb

        main()
    except Exception as e:
        log_manager.log_error(e, "_run_main_with_progress")
        raise
    finally:
        if global_ui is not None:
            global_ui.progress["value"] = 1
            global_ui.update_idletasks()
            time.sleep(0.2)
            global_ui.progress.pack_forget()

# ----------------------------------------------------------------------
HTML_PATTERN = re.compile(
    r'<link\s+rel=["\']canonical["\']\s+href=["\']https?://steamdb\.info/app/(\d+)/.*?["\']',
    re.IGNORECASE,
)
IMG_PATTERN = re.compile(r'([a-f0-9]{40})\.jpg', re.IGNORECASE)

ROOT_DIR = pathlib.Path(__file__).resolve().parent
APP_URL_TEMPLATE = "https://shared.fastly.steamstatic.com/community_assets/images/apps/{app_id}/"
APP_FOLDER = pathlib.Path(__file__).resolve().parent / ".app"
APP_FOLDER.mkdir(parents=True, exist_ok=True)
#-------------------------------------------------------------
THEMES_FOLDER = APP_FOLDER / "themes"
THEMES_FOLDER.mkdir(parents=True, exist_ok=True)
SELECTED_CTHEME_FILE = APP_FOLDER / "selected_ctheme.json"
#-------------------------------------------------------------
VERSION_FILE = APP_FOLDER / "version.txt"
UPDATE_CHECK_FILE = APP_FOLDER / "update_check.json"
GBE_VERSION_FILE = APP_FOLDER / "gbe.txt"
GSE_VERSION_FILE = APP_FOLDER / "gse.txt"
#-------------------------------------------------------------
LATEST_RELEASE_URL = "https://api.github.com/repos/Elite-Alien/Steam-Settings-Generator/releases/latest"
RELEASE_URL = "https://api.github.com/repos/Elite-Alien/Steam-Settings-Generator/releases"
DLM_VERSION_FILE = APP_FOLDER / "dlm_versions.json"
DLM_CACHE_FILE = APP_FOLDER / "dlm_cache.json"
#-------------------------------------------------------------
GBE_FOLDER = APP_FOLDER / "gbe"
GBE_LINUX = GBE_FOLDER / "Linux"
GBE_LINUX.mkdir(parents=True, exist_ok=True)
GBE_WINDOWS = GBE_FOLDER / "Windows"
GBE_WINDOWS.mkdir(parents=True, exist_ok=True)
GBE_WINDOWS_CLIENT = GBE_WINDOWS / "client"
GBE_WINDOWS_CLIENT.mkdir(parents=True, exist_ok=True)
#-------------------------------------------------------------
GSE_FOLDER = APP_FOLDER / "gse"
GSE_LINUX = GSE_FOLDER / "Linux"
GSE_LINUX.mkdir(parents=True, exist_ok=True)
GSE_WINDOWS = GSE_FOLDER / "Windows"
GSE_WINDOWS.mkdir(parents=True, exist_ok=True)
GSE_WINDOWS_CLIENT = GSE_WINDOWS / "client"
GSE_WINDOWS_CLIENT.mkdir(parents=True, exist_ok=True)
#-------------------------------------------------------------
LOGS_FOLDER = APP_FOLDER / "logs"
LOGS_FOLDER.mkdir(parents=True, exist_ok=True)
DOWNLOADS_FOLDER = APP_FOLDER / "downloads"
DOWNLOADS_FOLDER.mkdir(parents=True, exist_ok=True)
TEMP_FOLDER = APP_FOLDER / "temp"
TEMP_FOLDER.mkdir(parents=True, exist_ok=True)
#-------------------------------------------------------------
EXTRA_FOLDER = pathlib.Path(__file__).resolve().parent / "Extra"
EXTRA_FOLDER.mkdir(parents=True, exist_ok=True)
#-------------------------------------------------------------
PROGRESS_STATE_FILE = APP_FOLDER / "progress.json"
REMOVED_FILES_FILE = APP_FOLDER / "removed_files.json"
HTML_FOLDER = pathlib.Path(__file__).resolve().parent / "HTML"
HTML_FOLDER.mkdir(parents=True, exist_ok=True)
GAMES_ROOT = pathlib.Path(__file__).resolve().parent / "Games"
GAMES_ROOT.mkdir(parents=True, exist_ok=True)
OLD_HTML_FOLDER = TEMP_FOLDER / "old_html"
OLD_HTML_FOLDER.mkdir(parents=True, exist_ok=True)
#-------------------------------------------------------------
TOOLS_FOLDER = APP_FOLDER / "tools"
TOOLS_FOLDER.mkdir(parents=True, exist_ok=True)
GBE_TOOLS_FOLDER = TOOLS_FOLDER / "gbe_tools"
GBE_TOOLS_FOLDER.mkdir(parents=True, exist_ok=True)
GSE_TOOLS_FOLDER = TOOLS_FOLDER / "gse_tools"
GSE_TOOLS_FOLDER.mkdir(parents=True, exist_ok=True)
#-------------------------------------------------------------
USER_CONFIG_FILE = APP_FOLDER / "userconfig.json"
GENERAL_SETTINGS_FILE = APP_FOLDER / "general_settings.json"
CRYPT_FOLDER = APP_FOLDER / ".crypt"
CRYPT_FOLDER.mkdir(parents=True, exist_ok=True)
SAPI_FILE = CRYPT_FOLDER / "sapi"
DECKEY_FILE = CRYPT_FOLDER / ".deckey"

# ----------------------------------------------------------------------
class LogManager:
    def __init__(self):
        self.log_dir = LOGS_FOLDER
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"⚠️ Failed to create logs directory: {e}. Falling back to TEMP_FOLDER.")
            self.log_dir = TEMP_FOLDER
            self.log_dir.mkdir(parents=True, exist_ok=True)

        self.master_log = self.log_dir / "master_log.txt"
        self.error_log = self.log_dir / "error_log.txt"
        self._ui = None
        self.current_session_log = None
        self.log_files_cache = {}

        #self._write_to_master(f"=== LogManager initialized at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        #self._write_to_master(f"=== Session log: {self.current_session_log.name} ===")
        self._scan_log_files()

        retention_days = 1
        try:
            retention_days = GENERAL_SETTINGS.get("log_settings", {}).get("retention_days", 1)
        except (NameError, AttributeError):
            retention_days = 1
        self.cleanup_old_logs(days_to_keep=retention_days)

        self._stub_executable_path: Path | None = None
        self._stub_executable_name: str | None = None
        self._stub_output_file: Path | None = None
        self._stub_process: subprocess.Popen | None = None
        self._stub_monitor_thread: threading.Thread | None = None
        self._stub_stop_event = threading.Event()
        self._stub_complete_callback = None
        self._stub_lock = threading.Lock()

    def _write_to_master(self, message: str) -> None:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(self.master_log, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            print(f"⚠️ Failed to write to master log: {e}")

    def _write_to_session(self, message: str) -> None:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        if self.current_session_log is None:
            self.current_session_log = self.log_dir / f"session_{time.strftime('%Y%m%d_%H%M%S')}.log"
        try:
            with open(self.current_session_log, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            print(f"⚠️ Failed to write to session log: {e}")

    def log_message(self, message: str, log_to_session: bool = True) -> None:
        self._write_to_master(message)
        if log_to_session:
            self._write_to_session(message)
        print(message)

    def log_error(self, error: Exception, context: str = "") -> None:
        error_msg = f"ERROR [{context}]: {str(error)}"
        self._write_to_master(error_msg)
        self._write_to_session(error_msg)
        try:
            with open(self.error_log, 'a', encoding='utf-8') as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")
        except Exception:
            pass
        print(error_msg)

    def set_ui(self, ui) -> None:
        self._ui = ui

    def log_to_ui(self, message: str, context: str = "") -> None:
        self.log_message(message, context)
        if hasattr(self, '_ui') and self._ui and hasattr(self._ui, 'output_text') and self._ui.output_text.winfo_exists():
            try:
                self._ui.output_text.config(state="normal")
                self._ui.output_text.insert("end", f"{message}\n")
                self._ui.output_text.see("end")
                self._ui.output_text.config(state="disabled")
            except Exception as e:
                self.log_error(e, "log_to_ui")

    def log_command(self, command: str, context: str = "") -> None:
        self.log_message(f"COMMAND [{context}]: {command}")

    def _scan_log_files(self) -> None:
        self.log_files_cache = {}
        try:
            for log_file in self.log_dir.glob("*.log"):
                self.log_files_cache[log_file.name] = log_file
            for log_file in self.log_dir.glob("*.txt"):
                self.log_files_cache[log_file.name] = log_file
        except Exception as e:
            self.log_error(e, "_scan_log_files")

    @staticmethod
    def read_local_file(filepath: str) -> str:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    def _make_json_serialisable(self, obj) -> dict | list | str:
        if isinstance(obj, dict):
            return {str(k): self._make_json_serialisable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._make_json_serialisable(i) for i in obj]
        if isinstance(obj, pathlib.Path):
            return str(obj)
        return obj

    def load_progress_state(self, folder: Path | None = None) -> dict:
        if folder is None:
            folder = TEMP_FOLDER
        file_path = PROGRESS_STATE_FILE
        if not file_path.is_file():
            return {}
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.log_error(e, "load_progress_state")
            return {}

    def save_progress_state(self, state: dict, folder: Path | None = None) -> None:
        file_path = PROGRESS_STATE_FILE
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        existing = self.load_progress_state(folder)
        merged = {**existing, **state}
        serialisable_state = self._make_json_serialisable(merged)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(serialisable_state, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            self.log_error(e, "save_progress_state")

    def load_removed_files(self) -> set:
        if not REMOVED_FILES_FILE.is_file():
            return set()
        try:
            data = json.loads(REMOVED_FILES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(data)
            elif isinstance(data, dict) and "removed" in data:
                return set(data["removed"])
            return set(data.values()) if isinstance(data, dict) else set()
        except Exception as e:
            self.log_error(e, "load_removed_files")
            return set()

    def save_removed_files(self, removed: set) -> None:
        try:
            REMOVED_FILES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(REMOVED_FILES_FILE, "w", encoding="utf-8") as f:
                json.dump(list(removed), f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_error(e, "save_removed_files")

    def load_dlm_versions(self) -> dict:
        if not DLM_VERSION_FILE.exists():
            return {}
        try:
            return json.loads(DLM_VERSION_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            self.log_error(e, "load_dlm_versions")
            return {}

    def save_dlm_versions(self, versions: dict) -> None:
        try:
            DLM_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DLM_VERSION_FILE, "w", encoding="utf-8") as f:
                json.dump(versions, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_error(e, "save_dlm_versions")

    def load_update_check_time(self) -> float:
        if not UPDATE_CHECK_FILE.exists():
            return 0.0
        try:
            data = json.loads(UPDATE_CHECK_FILE.read_text(encoding="utf-8"))
            return data.get("last_check", 0.0)
        except Exception as e:
            self.log_error(e, "load_update_check_time")
            return 0.0

    def save_update_check_time(self) -> None:
        try:
            UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(UPDATE_CHECK_FILE, "w", encoding="utf-8") as f:
                json.dump({"last_check": time.time()}, f, indent=2)
        except Exception as e:
            self.log_error(e, "save_update_check_time")

    def load_encryption_key(self) -> bytes | None:
        if not DECKEY_FILE.exists():
            key = generate_key()
            self.save_encryption_key(key)
            return key
        try:
            with open(DECKEY_FILE, "rb") as f:
                return f.read()
        except Exception as e:
            self.log_error(e, "load_encryption_key")
            return None

    def save_encryption_key(self, key: bytes) -> None:
        try:
            DECKEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DECKEY_FILE, "wb") as f:
                f.write(key)
        except Exception as e:
            self.log_error(e, "save_encryption_key")

    def save_encrypted_api_key(self, api_key: str) -> None:
        key = self.load_encryption_key()
        if not key:
            self.log_message("No encryption key available.", log_to_session=False)
            return
        try:
            fernet = Fernet(key)
            encrypted = fernet.encrypt(api_key.encode("utf-8"))
            SAPI_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SAPI_FILE, "w", encoding="utf-8") as f:
                f.write(encrypted.decode("utf-8"))
        except Exception as e:
            self.log_error(e, "save_encrypted_api_key")

    def load_decrypted_api_key(self) -> str | None:
        if not SAPI_FILE.exists():
            return None
        key = self.load_encryption_key()
        if not key:
            self.log_message("No encryption key available.", log_to_session=False)
            return None
        try:
            with open(SAPI_FILE, "r", encoding="utf-8") as f:
                encrypted_key = f.read().strip()
            fernet = Fernet(key)
            decrypted = fernet.decrypt(encrypted_key.encode("utf-8"))
            return decrypted.decode("utf-8")
        except Exception as e:
            self.log_error(e, "load_decrypted_api_key")
            return None

    def get_all_log_files(self) -> list[Path]:
        self._scan_log_files()
        return list(self.log_files_cache.values())

    def read_log_file(self, log_path: Path) -> str:
        try:
            return log_path.read_text(encoding="utf-8")
        except Exception as e:
            self.log_error(e, f"read_log_file({log_path})")
            return f"Error reading log file: {e}"

    def get_session_log(self) -> str:
        if self.current_session_log is None or not self.current_session_log.is_file():
            return "No session log available."
        return self.read_log_file(self.current_session_log)

    def cleanup_old_logs(self, days_to_keep: int = 1) -> int:
        from datetime import datetime, timedelta
        import time as time_module

        if days_to_keep < 0:
            days_to_keep = 1

        cutoff = datetime.now() - timedelta(days=days_to_keep)
        cutoff_timestamp = cutoff.timestamp()

        deleted_count = 0
        log_dir = LOGS_FOLDER
        if not log_dir.exists():
            return 0

        if self.current_session_log is None:
            self.current_session_log = self.log_dir / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        log_files = []
        for log_path in log_dir.iterdir():
            if log_path.suffix.lower() in (".log", ".txt"):
                log_files.append(log_path)


        log_files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)

        for log_path in log_files:

            try:
                if log_path == self.current_session_log:
                    continue
            except Exception:
                continue

            try:
                try:
                    mtime = log_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                except PermissionError:
                    self.log_message(f"⚠️  No permission to access: {log_path.name}")
                    continue

                if mtime < cutoff_timestamp:
                    max_retries = 3
                    retry_delay = 0.5
                    for attempt in range(max_retries):
                        try:
                            log_path.unlink()
                            deleted_count += 1
                            self.log_message(f"🗑️  Deleted old log: {log_path.name}")
                            break
                        except PermissionError as e:

                            if attempt < max_retries - 1:
                                self.log_message(f"⚠️  File locked, retrying ({attempt + 1}/{max_retries}): {log_path.name}")
                                time_module.sleep(retry_delay)
                                retry_delay *= 2
                            else:
                                self.log_error(e, f"cleanup_old_logs({log_path}) - File locked after retries")
                        except FileNotFoundError:
                            break
                        except Exception as e:
                            self.log_error(e, f"cleanup_old_logs({log_path})")
                            break
            except Exception as e:
                self.log_error(e, f"cleanup_old_logs({log_path})")

        return deleted_count

log_manager = LogManager()

# ----------------------------------------------------------------------
def should_check_for_updates() -> bool:
    last_check = log_manager.load_update_check_time()
    current_time = time.time()
    return (current_time - last_check) >= (12 * 60 * 60)

def check_for_updates(manual=False, target='app'):
    config = {
        "app": {
            "version_file": VERSION_FILE,
            "release_url": LATEST_RELEASE_URL,
            "auto_setting": "auto_update",
            "asset_patterns": [r"\.zip$"],
            "success_msg": "Application"
        }
    }

    cfg = config[target]

    if not manual and not GENERAL_SETTINGS.get(cfg["auto_setting"], True):
        return

    if not should_check_for_updates():
        return

    log_manager.save_update_check_time()

    try:
        current_version = ""
        if cfg["version_file"].exists():
            current_version = cfg["version_file"].read_text(encoding="utf-8").strip()
            if manual:
                log_manager.log_message(f"Current {cfg['success_msg']} version: {current_version}")

        response = requests.get(cfg["release_url"], timeout=10)
        response.raise_for_status()
        release_data = response.json()
        latest_tag = release_data["tag_name"]

        if manual:
            log_manager.log_message(f"Latest {cfg['success_msg']} version: {latest_tag}")

        if latest_tag != current_version:
            msg = f"New {cfg['success_msg']} version available: {latest_tag}\nDownload and install?"
            if _gui_yes_no(msg):
                assets = []
                patterns = [re.compile(p, re.I) for p in cfg["asset_patterns"]]

                for asset in release_data.get("assets", []):
                    if any(pattern.search(asset["name"]) for pattern in patterns):
                        assets.append(asset)

                if len(assets) < len(cfg["asset_patterns"]):
                    error_msg = f"Missing required assets for {cfg['success_msg']} update"
                    raise Exception(error_msg)

                for asset in assets:
                    dl_path = DOWNLOADS_FOLDER / asset["name"]
                    response = requests.get(asset["browser_download_url"], stream=True)
                    response.raise_for_status()
                    with open(dl_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                    with zipfile.ZipFile(dl_path, 'r') as zip_ref:
                        temp_extract = ROOT_DIR / "update_temp"
                        temp_extract.mkdir(exist_ok=True)
                        zip_ref.extractall(temp_extract)
                        extracted_folder = next(temp_extract.iterdir())
                        for item in extracted_folder.iterdir():
                            dest = ROOT_DIR / item.name
                            if dest.is_dir():
                                shutil.rmtree(dest, ignore_errors=True)
                            elif dest.exists():
                                dest.unlink()
                            shutil.move(str(item), str(dest))

                        shutil.rmtree(temp_extract, ignore_errors=True)
                        dl_path.unlink(missing_ok=True)

                cfg["version_file"].write_text(latest_tag, encoding="utf-8")

                if manual:
                    messagebox.showinfo(
                        f"{cfg['success_msg']} Update Complete",
                        f"{cfg['success_msg']} files updated!"
                    )
            else:
                log_manager.log_message(f"{cfg['success_msg']} update canceled by user")
        else:
            if manual:
                msg = f"You have the latest {cfg['success_msg']} version"
                if global_ui is not None:
                    global_ui.after(0, lambda: messagebox.showinfo(
                        f"{cfg['success_msg']} Update Check", msg
                    ))
                else:
                    messagebox.showinfo(f"{cfg['success_msg']} Update Check", msg)
            else:
                log_manager.log_message(f"You have the latest {cfg['success_msg']} version")

    except Exception as e:
        log_manager.log_error(Exception(f"{cfg['success_msg']} update failed: {e}"), "check_for_updates")
        if manual:
            error_msg = f"Failed to update {cfg['success_msg']}: {str(e)}"
            if global_ui is not None:
                global_ui.after(0, lambda: messagebox.showerror(
                    f"{cfg['success_msg']} Update Error", error_msg
                ))
            else:
                messagebox.showerror(f"{cfg['success_msg']} Update Error", error_msg)

def restart_application():
    python = sys.executable
    os.execl(python, python, *sys.argv)

# Helper function to load custom themes from the themes folder
def load_custom_themes() -> dict:
    themes = {}
    if not THEMES_FOLDER.exists():
        return themes
    for theme_file in THEMES_FOLDER.glob("*.json"):
        try:
            with open(theme_file, "r", encoding="utf-8") as f:
                theme_data = json.load(f)
                theme_name = theme_file.stem
                themes[theme_name] = theme_data
        except Exception as e:
            log_manager.log_error(f"Failed to load theme {theme_file.name}: {e}")
    return themes

# Helper function to save a custom theme
def save_custom_theme(theme_name: str, theme_data: dict) -> bool:
    try:
        THEMES_FOLDER.mkdir(parents=True, exist_ok=True)
        theme_path = THEMES_FOLDER / f"{theme_name}.json"
        with open(theme_path, "w", encoding="utf-8") as f:
            json.dump(theme_data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        log_manager.log_error(f"Failed to save theme {theme_name}: {e}")
        return False

# Helper function to load the selected custom theme
def load_selected_ctheme() -> str | None:
    if not SELECTED_CTHEME_FILE.exists():
        return None
    try:
        with open(SELECTED_CTHEME_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("theme_name")
    except Exception as e:
        log_manager.log_error(f"Failed to load selected custom theme: {e}")
        return None

# Helper function to save the selected custom theme
def save_selected_ctheme(theme_name: str) -> bool:
    try:
        SELECTED_CTHEME_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SELECTED_CTHEME_FILE, "w", encoding="utf-8") as f:
            json.dump({"theme_name": theme_name}, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log_manager.log_error(f"Failed to save selected custom theme: {e}")
        return False

# Helper function to auto-format color codes (add # if missing)
def format_color_code(color: str) -> str:
    color = color.strip()
    if not color:
        return color
    if color.startswith("#"):
        return color
    # Check if it's a valid hex color (3 or 6 digits)
    if re.fullmatch(r"^[0-9a-fA-F]{3,6}$", color):
        return f"#{color}"
    return color

def validate_selected_ctheme() -> str | None:
    selected_theme_name = load_selected_ctheme()
    if not selected_theme_name:
        return None

    custom_themes = load_custom_themes()
    if selected_theme_name not in custom_themes:

        try:
            if SELECTED_CTHEME_FILE.exists():
                SELECTED_CTHEME_FILE.unlink()
            log_manager.log_message(f"⚠️  Selected custom theme '{selected_theme_name}' not found. Resetting to default.")
        except Exception as e:
            log_manager.log_error(e, "validate_selected_ctheme")
        return None

    return selected_theme_name

def generate_key() -> bytes:
    return Fernet.generate_key()

def encrypt_api_key(api_key: str, key: bytes) -> str:
    fernet = Fernet(key)
    encrypted = fernet.encrypt(api_key.encode("utf-8"))
    return encrypted.decode("utf-8")

def decrypt_api_key(encrypted_key: str, key: bytes) -> str | None:
    try:
        fernet = Fernet(key)
        decrypted = fernet.decrypt(encrypted_key.encode("utf-8"))
        return decrypted.decode("utf-8")
    except Exception as e:
        log_manager.log_error(Exception(f"Error decrypting API key: {e}"), "decrypt_api_key")
        return None

# ----------------------------------------------------------------------
def _closest_folder(base_path: Path, html_name: str) -> Path | None:
    candidates = [p for p in base_path.iterdir() if p.is_dir()]
    if not candidates:
        return None

    scores = {
        p: difflib.SequenceMatcher(
            a=html_name.lower(), b=p.name.lower()
        ).ratio()
        for p in candidates
    }
    best_folder = max(scores, key=scores.get)

    return best_folder if scores[best_folder] >= 0.6 else None

def _copy_existing_images(
    json_file: Path,
    src_folder: Path,
    dest_folder: Path,
) -> set[str]:

    try:
        achievements = json.loads(json_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to read achievements JSON ({json_file}): {e}")
        return set()

    needed = set()
    for ach in achievements:
        for key in ("icon", "icongray", "icon_gray"):
            val = ach.get(key)
            if val:
                needed.add(val)

    found = set()
    for img_name in needed:
        src_path = src_folder / img_name
        if src_path.is_file():
            dest_path = dest_folder / img_name
            try:
                shutil.copy2(src_path, dest_path)
                found.add(img_name)
                log_manager.log_message(f"Copied existing image {img_name} from {src_folder}")
            except Exception as e:
                print(f"Could not copy {img_name}: {e}")

    return found


def move_to_old(html_path: Path):
    try:
        if html_path.parent == HTML_FOLDER:
            dest_html = OLD_HTML_FOLDER / html_path.name
            if html_path.exists():
                shutil.move(str(html_path), str(dest_html))
                log_manager.log_message(f"🗂️ Moved HTML file to {dest_html}")
            
            folder_name = html_path.stem + "_files"
            src_folder = html_path.parent / folder_name
            if src_folder.exists():
                dest_folder = OLD_HTML_FOLDER / folder_name
                shutil.move(str(src_folder), str(dest_folder))
                log_manager.log_message(f"🗂️ Moved associated folder to {dest_folder}")

            progress_state = log_manager.load_progress_state()
            progress_state[html_path.name] = {"percent": 100}
            log_manager.save_progress_state(progress_state)

            file_status[html_path] = "done"

            if global_ui:
                global_ui.refresh_file_list(all_html_files, file_status)

    except Exception as e:
        print(f"⚠️ Error moving files to old folder: {e}")

def load_processed_log(folder: Path) -> set:
    return set()

def _hidden_cleanup_needed(html_name: str, processed: set) -> bool:
    return html_name not in processed

def save_processed_log(folder: Path, processed: set) -> None:
    pass

def load_progress_state() -> dict:
    return log_manager.load_progress_state()

def is_version_installed(target: str, version: str) -> bool:
    versions = log_manager.load_dlm_versions()
    return versions.get(target, {}).get(version, False)

def mark_version_installed(target: str, version: str, installed: bool = True) -> None:
    versions = log_manager.load_dlm_versions()
    if target not in versions:
        versions[target] = {}
    versions[target][version] = installed
    log_manager.save_dlm_versions(versions)

def extract_app_id(soup: BeautifulSoup) -> str | None:
    link_tag = soup.find("link", rel="canonical")
    if link_tag and link_tag.get("href"):
        m = re.search(r"/app/(\d+)", link_tag["href"], re.IGNORECASE)
        if m:
            return m.group(1)
    
    m = HTML_PATTERN.search(str(soup))
    if m:
        return m.group(1)
    
    meta_tag = soup.find("meta", property="og:url")
    if meta_tag and meta_tag.get("content"):
        m = re.search(r"/app/(\d+)", meta_tag["content"], re.IGNORECASE)
        if m:
            return m.group(1)
    
    return None

def fix_empty_icon(filename: str) -> str:
    return "hidden.jpg" if filename == ".jpg" else filename

def collect_image_names(soup: BeautifulSoup) -> list[str]:
    names = set()
    for tag in soup.find_all(class_="achievement_image"):
        data_name = tag.get("data-name", "")
        m = IMG_PATTERN.search(data_name)
        if m:
            names.add(f"{m.group(1)}.jpg")
    for tag in soup.find_all(class_="achievement_image_small"):
        data_name = tag.get("data-name", "")
        m = IMG_PATTERN.search(data_name)
        if m:
            names.add(f"{m.group(1)}.jpg")
    return list(names)

def download_images(
    app_id: str,
    filenames: list[str],
    dest_folder: Path,
    progress_cb: callable | None = None,
) -> int:
    base_url = APP_URL_TEMPLATE.format(app_id=app_id)
    dest_folder.mkdir(parents=True, exist_ok=True)

    total = len(filenames)

    downloaded_files = set(dest_folder.iterdir())
    downloaded_count = 0

    for i, fname in enumerate(filenames, start=1):
        file_path = dest_folder / fname

        if file_path not in downloaded_files:
            url = urljoin(base_url, fname)
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                file_path.write_bytes(resp.content)
                downloaded_files.add(file_path)
                downloaded_count += 1
                log_manager.log_message(f"Downloaded {file_path.name} to {dest_folder}")
            except Exception as e:
                log_manager.log_message(f"Failed {url}: {e}")

        if progress_cb is not None:
            try:
                progress_cb(i, total)
            except Exception:
                pass

    return downloaded_count

def get_image_filename(tag) -> str:
    if not tag:
        return "No icon"
    base = tag.get("data-name", "")
    return base if base.lower().endswith(".jpg") else f"{base}.jpg"


def safe_folder_name(name: str) -> str:
    illegal = r'[\/:*?"<>|]'
    name = re.sub(illegal, "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def clean_title(raw_title: str) -> str:
    title = raw_title.strip()
    return safe_folder_name(title)

# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Scrape Steam achievement/DLC data from an HTML file."
    )
    parser.add_argument(
        "html_path",
        type=Path,
        help="Path to the .html file to process",
    )
    args = parser.parse_args()

    if not args.html_path.is_file():
        log_manager.log_error(f"File not found: {args.html_path}")
        sys.exit(1)

    html_path = args.html_path
    progress_manager.update_progress(0, html_path)
    if not html_path.is_file():
        old_path = OLD_HTML_FOLDER / html_path.name
        if old_path.is_file():
            html_path = old_path
        else:
            log_manager.log_error(f"File not found: {args.html_path}")
            sys.exit(1)

    html_content = log_manager.read_local_file(str(html_path))
    soup = BeautifulSoup(html_content, "html.parser")
    
    TEMP_FOLDER = APP_FOLDER / "temp"

    script_dir = pathlib.Path(__file__).resolve().parent
    progress_state = log_manager.load_progress_state()
    base_folder = GAMES_ROOT / clean_title(soup.find("h1", itemprop="name").text)
    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"
    
    TEMP_FOLDER = APP_FOLDER / "temp"

    processed_folder = script_dir
    progress_state = log_manager.load_progress_state(processed_folder)

    steam_settings.mkdir(parents=True, exist_ok=True)
    achievement_images.mkdir(parents=True, exist_ok=True)

    if EXTRA_FOLDER.is_dir():
        for root, dirs, files in os.walk(EXTRA_FOLDER):
            rel_path = pathlib.Path(root).relative_to(EXTRA_FOLDER)
            dest_dir = steam_settings / rel_path
            dest_dir.mkdir(parents=True, exist_ok=True)

            for f in files:
                src_file = pathlib.Path(root) / f
                dst_file = dest_dir / f
                shutil.copy2(src_file, dst_file)

    progress_manager.update_progress(20, html_path)

    app_id = extract_app_id(soup)
    if app_id:
        appid_path = steam_settings / "steam_appid.txt"
        try:
            appid_path.write_text(app_id, encoding="utf-8")
            log_manager.log_message(f"Steam app‑id written to {appid_path}")
        except Exception as e:
            log_manager.log_error(f"Failed to write app‑id file: {e}")

        game_name = clean_title(soup.find("h1", itemprop="name").text)
        game_dir = GAMES_ROOT / game_name
        html_folder_name = html_path.stem + "_files"
        temp_file_path = TEMP_FOLDER / f"{html_path.name}.txt"
        temp_content = (
            f"appid={app_id}\n"
            f"GameName={game_name}\n"
            f"GAMEDIR={game_dir}\n"
            f"HTMLFile={html_path.name}\n"
            f"HTMLFOLDER={html_folder_name}\n"
        )
        hidden_appid = game_dir / f".{app_id}"
        try:
            hidden_appid.touch(exist_ok=True)

        except Exception as e:
            log_manager.log_error(f"Failed to create hidden app‑id file {hidden_appid}: {e}")
        try:
            temp_file_path.write_text(temp_content, encoding="utf-8")
        except Exception:
            pass
    else:
        log_manager.log_error("No Steam app‑id found – steam_appid.txt not created.")

    progress_manager.update_progress(30, html_path)
    progress_cb = None

    achievements = []
    achievement_divs = soup.find_all(
        "div", id=lambda x: x and x.startswith("achievement-")
    )

    if not achievement_divs:
        log_manager.log_error("No achievements found in the provided HTML file.")
        sys.exit(0)

    for achievement in achievement_divs:
        name_div = achievement.find("div", class_="achievement_api")
        if not name_div:
            continue
        name = name_div.text.strip()

        display_name = (
            achievement.find(class_="achievement_name").text.strip()
            if achievement.find(class_="achievement_name")
            else "Unknown"
        )
        description = (
            achievement.find(class_="achievement_desc").text.strip()
            if achievement.find(class_="achievement_desc")
            else "No description"
        )

        icon = get_image_filename(achievement.find(class_="achievement_image"))
        icon_small = get_image_filename(
            achievement.find(class_="achievement_image_small")
        )

        icon = fix_empty_icon(icon)
        icon_small = fix_empty_icon(icon_small)

        progress_manager.update_progress(40, html_path)

        is_multiplayer = (
            achievement.find("div", class_="achievement_group")
            and achievement.find("div", class_="achievement_group").text.strip()
            == "Multiplayer"
        )

        is_hidden = bool(achievement.find("span", class_="achievement_spoiler")) or bool(
            achievement.find("i", string="Hidden achievement:")
        )

        achievements.append(
            {
                "name": name,
                "defaultvalue": 0,
                "displayName": display_name,
                "hidden": 1 if is_hidden else 0,
                "description": description,
                "icon": icon,
                "icongray": icon_small,
                "icon_gray": icon_small,
                "is_multiplayer": is_multiplayer,
            }
        )

    hidden_icon_src = APP_FOLDER / "icons" / "hidden.jpg"
    hidden_icon_dest = achievement_images / "hidden.jpg"

    if not hidden_icon_src.exists():
        log_manager.log_error(f"❌ Critical: Missing required icon at {hidden_icon_src}")
        sys.exit(1)

    if any(any(ach[k] == "hidden.jpg" for k in ["icon", "icongray", "icon_gray"]) for ach in achievements):
        shutil.copy2(hidden_icon_src, hidden_icon_dest)

    processed = load_processed_log(processed_folder)
    processed_html_names = {p for p in processed if not p.isdigit()}

    multiplayer_achievements = [a for a in achievements if a["is_multiplayer"]]
    already_done = (
        progress_state.get(html_path.name, {})
        .get("percent", 0) == 100
    )

    has_hidden_prefix = any(
        a["description"].startswith("Hidden achievement:") for a in achievements
    )

    with _prompt_handled_lock:
        if not _prompt_handled.get(html_path, False):
            current_progress = progress_state.get(html_path.name, {}).get("percent", 0)

            if multiplayer_achievements and not already_done:
                mp_setting = GENERAL_SETTINGS.get("mp_prompt", "Ask")
                if mp_setting == "Yes":
                    achievements = [a for a in achievements if not a["is_multiplayer"]]
                    progress_manager.update_progress(max(current_progress, 50), html_path)
                elif mp_setting == "Ask":
                    if _gui_yes_no("Multiplayer achievements found. Remove them?"):
                        achievements = [a for a in achievements if not a["is_multiplayer"]]
                        progress_manager.update_progress(max(current_progress, 50), html_path)
                _prompt_handled[html_path] = True

            if has_hidden_prefix and not already_done:
                hidden_setting = GENERAL_SETTINGS.get("hidden_prompt", "Ask")
                if hidden_setting == "Yes":
                    for a in achievements:
                        if a["description"].startswith("Hidden achievement:"):
                            a["description"] = a["description"][len("Hidden achievement:"):].lstrip()
                    progress_manager.update_progress(max(current_progress, 50), html_path)
                elif hidden_setting == "Ask":
                    if _hidden_cleanup_needed(html_path.name, processed_html_names):
                        if _gui_yes_no('Clean descriptions that start with "Hidden achievement:"?'):
                            for a in achievements:
                                if a["description"].startswith("Hidden achievement:"):
                                    a["description"] = a["description"][len("Hidden achievement:"):].lstrip()
                        progress_manager.update_progress(max(current_progress, 50), html_path)
                    else:
                        for a in achievements:
                            if a["description"].startswith("Hidden achievement:"):
                                a["description"] = a["description"][len("Hidden achievement:"):].lstrip()
                        progress_manager.update_progress(max(current_progress, 50), html_path)
            _prompt_handled[html_path] = True

    for a in achievements:
        a.pop("is_multiplayer", None)

    json_path = steam_settings / "achievements.json"
    json_path.write_text(
        json.dumps(achievements, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    log_manager.log_message(f"Achievements JSON written to {json_path}")
     
    processed_folder = script_dir
    processed = load_processed_log(processed_folder)

    html_folder = html_path.parent
    closest = _closest_folder(html_folder, html_path.stem)

    if closest:
        already_have = _copy_existing_images(
            json_path,
            closest,
            achievement_images,
        )
    else:
        already_have = set()
        progress_manager.update_progress(70, html_path)
        log_manager.log_error("No similar folder with images found; will download all needed files.")

    all_filenames = collect_image_names(soup)

    existing_local = {
        p.name
        for p in achievement_images.iterdir()
        if p.is_file() and p.suffix.lower() == ".jpg"
    }

    already_present = already_have.union(existing_local)
    missing_filenames = [f for f in all_filenames if f not in already_present]

    if app_id and missing_filenames:
        if _download_done.get(html_path):
            missing_filenames = []
            progress_manager.update_progress(90, html_path)
        else:
            progress_cb = progress_manager._get_progress_cb(app_id, html_path) or progress_manager._terminal_progress
            downloaded_cnt = download_images(
                app_id,
                missing_filenames,
                achievement_images,
                progress_cb=progress_cb,
            )
            log_manager.log_message(f"Downloaded {len(missing_filenames)} missing image(s) to {achievement_images}")
            progress_manager.update_progress(90, html_path)
            _download_done[html_path] = True
            missing_filenames = []
            
            if progress_cb:
                progress_cb(1, 1)
    elif app_id:
        log_manager.log_message("All required images already present - no download needed.")
        progress_manager.update_progress(90, html_path)
        progress_cb = progress_manager._get_progress_cb(app_id, html_path) or progress_manager._terminal_progress
        if progress_cb:
            progress_cb(1, 1)
    else:
        log_manager.log_error("No Steam app-id found - image download skipped.")
        progress_manager.update_progress(90, html_path)
        progress_cb = progress_manager._get_progress_cb(app_id, html_path) or progress_manager._terminal_progress
        if progress_cb:
            progress_cb(1, 1)

    game_dir = GAMES_ROOT / clean_title(soup.find("h1", itemprop="name").text)

    with game_content_lock[str(game_dir.resolve())]:
        steam_settings = game_dir / "steam_settings"
        steam_settings.mkdir(parents=True, exist_ok=True)
        
        json_path = steam_settings / "achievements.json"
        temp_path = json_path.with_suffix(".tmp")
        
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(achievements, f, indent=4, ensure_ascii=False)
            os.replace(str(temp_path), str(json_path))
        finally:
            if temp_path.exists():
                temp_path.unlink()

    with dlc_lock:
        try:
            dlc_txt_path = steam_settings / "DLC.txt"
            ini_path = steam_settings / "configs.app.ini"
        
            if dlc_txt_path.exists() and ini_path.exists():
                log_manager.log_message("DLC files already exist - skipping DLC processing")
            else:
                dlc_info = OrderedDict()
                dlc_rows = soup.find_all('tr', attrs={'data-appid': True})
                for row in dlc_rows:
                    appid = row.get('data-appid')
                    if appid and appid.isdigit():
                        title_cell = row.find_all('td')[1] if len(row.find_all('td')) > 1 else None
                        if title_cell:
                            title = title_cell.get_text(strip=True)
                            dlc_info[int(appid)] = title

                dlc_info = dict(sorted(dlc_info.items()))
        
                if dlc_info:
                    dlc_txt_path = steam_settings / "DLC.txt"
                    with dlc_txt_path.open("w", encoding="utf-8") as f:
                        for dlc_id, title in dlc_info.items():
                            f.write(f"{dlc_id}={title}\n")
            
                    ini_path = steam_settings / "configs.app.ini"
                    with ini_path.open("w", encoding="utf-8") as f:
                        f.write("[app::dlcs]\n")
                        f.write("unlock_all=1\n")
                        for dlc_id, title in dlc_info.items():
                            f.write(f"{dlc_id}={title}\n")
                    
                    log_manager.log_message(f"DLC.txt and configs.app.ini written in {steam_settings}")
                else:
                    log_manager.log_error("No DLC entries found, skipping DLC file creation.")
            
        except Exception as e:
            log_manager.log_error(f"⚠️ Error during DLC processing: {e}")

        try:
            depot_rows = soup.find_all('tr', class_='depot')
            depot_ids = set()
            for row in depot_rows:
                depot_id = row.get('data-depotid')
                if depot_id and depot_id.strip():
                    depot_ids.add(depot_id.strip())

            if depot_ids:
                depots_path = steam_settings / "depots.txt"
                with depots_path.open("w", encoding="utf-8") as f:
                    for depot in sorted(depot_ids, key=int):
                        f.write(f"{depot}\n")

                log_manager.log_message(f"depots.txt written in {steam_settings}")
            else:
                log_manager.log_error("No Depot entries found, skipping Depot file creation.")

        except Exception as e:
            log_manager.log_error(f"⚠️ Error during depot processing: {e}")

    progress_manager.update_progress(95, html_path)

def _wrapped_download(app_id: str, filenames: list[str], dest: Path, cb: callable = progress_manager._terminal_progress):
    html_path = globals().get("html_path")
    if isinstance(html_path, Path) and _download_done.get(html_path):
        return

    for i, fname in enumerate(filenames, start=1):
        url = urljoin(APP_URL_TEMPLATE.format(app_id=app_id), fname)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            (dest / fname).write_bytes(resp.content)
        except Exception as e:
            log_manager.log_error(f"Failed {url}: {e}")

        cb(i, len(filenames))

        state = load_progress_state(TEMP_FOLDER)
        if isinstance(html_path, Path):
            percent = int(i / len(filenames) * 100)
            state[html_path.name] = {"percent": percent}
            log_manager.save_progress_state(state, TEMP_FOLDER)

    progress_manager.update_progress(100, html_path)

    if html_path.parent == HTML_FOLDER:
        try:
            move_to_old(html_path)
            log_manager.log_message(f"🗂️ Moved processed files for {html_path.name} to old_html folder")
        except Exception as e:
            log_manager.log_error(f"⚠️ Error moving files to old folder: {e}")

    state = log_manager.load_progress_state(TEMP_FOLDER)
    state[html_path.name] = {"percent": 100}
    log_manager.save_progress_state(state, TEMP_FOLDER)

# ------------------------------------------------------------
def _mark_complete_if_success(html_path: Path):
    if not html_path.exists():
        archived_path = OLD_HTML_FOLDER / html_path.name
        if archived_path.exists():
            html_path = archived_path

    try:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
        base_folder = pathlib.Path(__file__).resolve().parent / clean_title(
            soup.find("h1", itemprop="name").text
        )
    except Exception:
        return False

    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"

    if not steam_settings.is_dir():
        return False

    json_path = steam_settings / "achievements.json"
    if not json_path.is_file():
        return False
    try:
        achievements = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(achievements, list) or not achievements:
            return False
    except Exception:
        return False

    expected_imgs = set(collect_image_names(soup))
    present_imgs = {
        p.name
        for p in achievement_images.iterdir()
        if p.is_file() and p.suffix.lower() == ".jpg"
    }
    if not expected_imgs.issubset(present_imgs):
        return False

    extra_src = pathlib.Path(__file__).resolve().parent / "Extra"
    if extra_src.is_dir():
        for root, _, files in os.walk(extra_src):
            rel = pathlib.Path(root).relative_to(extra_src)
            dest_dir = steam_settings / rel
            for f in files:
                if not (dest_dir / f).is_file():
                    return False

    dlc_txt = steam_settings / "DLC.txt"
    ini_txt = steam_settings / "configs.app.ini"
    has_dlc = bool(re.search(r'>\s*DLC\s+\d+\s*<', html_path.read_text(encoding="utf-8"), re.I))
    if has_dlc and not (dlc_txt.is_file() or ini_txt.is_file()):
        return False

    return True

def _run_main_in_thread(html_path: Path):
    global all_html_files, file_status
    old_argv = sys.argv[:]
    sys.argv = [sys.argv[0], str(html_path)]
    try:
        job_tracker.start_job()
        main()

        if html_path.parent == HTML_FOLDER:
            try:
                move_to_old(html_path)
            except Exception as e:
                log_manager.log_error(f"Error moving files: {e}")

        file_status[html_path] = "done"
        progress_manager.update_progress(100, html_path)

    finally:
        job_tracker.finish_job()
        sys.argv = old_argv

# ------------------------------------------------------------
class SettingsManager:
    def __init__(self, config_file: Path, default_settings: dict):
        self.config_file = config_file
        self.default_settings = default_settings
        self.settings = default_settings.copy()
        self._raw_api_key = None
        self.load()
    
    def load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    stored_data = json.load(f)
                    self.settings = {**self.default_settings, **stored_data}
                    self._raw_api_key = log_manager.load_decrypted_api_key()
        except Exception as e:
            log_manager.log_error(f"Error loading {self.config_file.name}: {e}")
            self.settings = self.default_settings.copy()
    
    def save(self):
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            settings_to_save = self.settings.copy()
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(settings_to_save, f, indent=2)
        except Exception as e:
            log_manager.log_error(f"Error saving {self.config_file.name}: {e}")
    
    def get(self, key, default=None):
        if key == "steam_api_key":
            return self._raw_api_key or self.settings.get(key, default)
        return self.settings.get(key, self.default_settings.get(key, default))
    
    def set(self, key, value, autosave=True):
        if key == "steam_api_key":
            self._raw_api_key = value
            log_manager.save_encrypted_api_key(value)
        else:
            self.settings[key] = value
        if autosave:
            self.save()

USER_SETTINGS = SettingsManager(
    USER_CONFIG_FILE,
    {
        "enabled": False,
        "account_name": "",
        "steamid": "76561197960287930",
        "language": "English",
        "country": "US"
    }
)

GENERAL_SETTINGS = SettingsManager(
    GENERAL_SETTINGS_FILE,
    {
        "auto_update": True,
        "mp_prompt": "Ask",
        "hidden_prompt": "Ask",
        "search_language": "english",
        "log_settings": {"retention_days": 1}        
    }
)

# ------------------------------------------------------------
class ThemeConfigTab:
    def __init__(self, parent_frame, theme, ui_instance):
        self.parent = parent_frame
        self.ui = ui_instance
        self.theme = theme
        if hasattr(self.ui, 'dark_mode'):
            self.current_theme = self.ui.DARK_THEME if self.ui.dark_mode else self.ui.LIGHT_THEME
        else:
            self.current_theme = self.ui.DARK_THEME
        self.custom_themes_enabled = tk.BooleanVar(
            value=GENERAL_SETTINGS.get("custom_themes_enabled", False)
        )
        self.selected_theme = tk.StringVar()
        self.theme_fields = {}
        self.custom_theme_name = tk.StringVar()
        self.theme_listbox = None
        self.custom_theme_data = {}

        # Default theme fields (from DARK_THEME)
        self.default_theme_fields = {
            "bg": "#2d2d2d",
            "fg": "#cdcdcd",
            "widget_bg": "#404040",
            "widget_fg": "#ffffff",
            "hover_bg": "#505050",
            "active_bg": "#606060",
            "border": "#606060",
            "button_bg": "#404040",
            "progress": "darkred"
        }

        self._setup_ui()

    def _setup_ui(self):
        default_theme_frame = Frame(self.parent, bg=self.theme["widget_bg"])
        default_theme_frame.pack(fill="x", pady=5)

        default_theme_label = Label(
            default_theme_frame,
            text="Default Theme:",
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
        )
        default_theme_label.pack(side="left", padx=5)

        # Default theme variable
        self.default_theme_var = tk.StringVar(value=GENERAL_SETTINGS.get("default_theme", "dark"))

        dark_rb = Radiobutton(
            default_theme_frame,
            text="Dark",
            variable=self.default_theme_var,
            value="dark",
            command=self._save_default_theme,
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
            selectcolor=self.theme["widget_bg"],
        )
        dark_rb.pack(side="left", padx=5)

        light_rb = Radiobutton(
            default_theme_frame,
            text="Light",
            variable=self.default_theme_var,
            value="light",
            command=self._save_default_theme,
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
            selectcolor=self.theme["widget_bg"],
        )
        light_rb.pack(side="left", padx=5)

        self.custom_rb = Radiobutton(
            default_theme_frame,
            text="Custom",
            variable=self.default_theme_var,
            value="custom",
            command=self._save_default_theme,
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
            selectcolor=self.theme["widget_bg"],
            state="disabled",
        )
        self.custom_rb.pack(side="left", padx=5)

        # Enable/disable Custom radio based on custom themes enabled
        self.custom_themes_enabled.trace_add("write", self._update_custom_rb_state)
        self._update_custom_rb_state()

        enable_cb = Checkbutton(
            default_theme_frame,
            text="Enable Custom Themes",
            variable=self.custom_themes_enabled,
            command=self._toggle_custom_themes,
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
            selectcolor=self.theme["widget_bg"],
            activebackground=self.theme["widget_bg"],
            activeforeground=self.theme["widget_fg"],
        )
        enable_cb.pack(side="right", padx=5)

        # Theme Selection Frame (Scrollable)
        selection_frame = Frame(self.parent, bg=self.theme["widget_bg"])
        selection_frame.pack(fill="both", expand=True, pady=5)

        selection_label = Label(
            selection_frame,
            text="Select a Custom Theme:",
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
        )
        selection_label.pack(anchor="w", padx=5)

        # Canvas and Scrollbar for Theme List
        canvas = Canvas(selection_frame, bg=self.theme["widget_bg"], highlightthickness=0, height=60)
        scrollbar = Scrollbar(selection_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = Frame(canvas, bg=self.theme["widget_bg"])

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.theme_listbox_frame = scrollable_frame

        # Theme Creation Frame
        creation_frame = Frame(self.parent, bg=self.theme["widget_bg"])
        creation_frame.pack(fill="x", pady=5)

        creation_label = Label(
            creation_frame,
            text="Create a Custom Theme:",
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
        )
        creation_label.pack(anchor="w", padx=5)

        # Name Field
        name_frame = Frame(creation_frame, bg=self.theme["widget_bg"])
        name_frame.pack(fill="x", pady=2)

        name_label = Label(
            name_frame,
            text="Name:",
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
        )
        name_label.pack(side="left", padx=5)

        name_entry = Entry(
            name_frame,
            textvariable=self.custom_theme_name,
            bg=self.theme["widget_bg"],
            fg=self.theme["widget_fg"],
            insertbackground=self.theme["fg"],
        )
        name_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Color Fields
        for field, default_value in self.default_theme_fields.items():
            field_frame = Frame(creation_frame, bg=self.theme["widget_bg"])
            field_frame.pack(fill="x", pady=2)

            field_label = Label(
                field_frame,
                text=f"{field}:",
                bg=self.theme["widget_bg"],
                fg=self.theme["widget_fg"],
                width=12,
                anchor="w",
            )
            field_label.pack(side="left", padx=5)

            field_entry = Entry(
                field_frame,
                bg=self.theme["widget_bg"],
                fg=self.theme["widget_fg"],
                insertbackground=self.theme["fg"],
            )
            field_entry.insert(0, default_value)
            field_entry.bind("<KeyRelease>", lambda e, f=field: self._update_theme_preview(f))
            field_entry.pack(side="left", fill="x", expand=True, padx=5)

            self.theme_fields[field] = field_entry

        # Save Button
        save_button = Button(
            creation_frame,
            text="Save Theme",
            command=self._save_custom_theme,
            bg=self.theme["button_bg"],
            fg=self.theme["widget_fg"],
        )
        save_button.pack(pady=10)

        # Load themes and refresh the list
        self._refresh_theme_list()

        # Load selected custom theme
        selected_theme = load_selected_ctheme()
        if selected_theme:
            self.selected_theme.set(selected_theme)
            self._highlight_selected_theme()

    def _update_custom_rb_state(self, *args):
        if self.custom_themes_enabled.get():
            self.custom_rb.config(state="normal")
        else:
            self.custom_rb.config(state="disabled")
            if self.default_theme_var.get() == "custom":
                self.default_theme_var.set("dark")
                self._save_default_theme()

    def _save_default_theme(self):
        GENERAL_SETTINGS.set("default_theme", self.default_theme_var.get())

    def _toggle_custom_themes(self):
        GENERAL_SETTINGS.set("custom_themes_enabled", self.custom_themes_enabled.get())

    def _refresh_theme_list(self):
        for widget in self.theme_listbox_frame.winfo_children():
            widget.destroy()

        custom_themes = load_custom_themes()
        if not custom_themes:
            no_themes_label = Label(
                self.theme_listbox_frame,
                text="No custom themes found.",
                bg=self.theme["widget_bg"],
                fg=self.theme["widget_fg"],
            )
            no_themes_label.pack(anchor="w", padx=5)
            return

        for theme_name in custom_themes:
            theme_frame = Frame(self.theme_listbox_frame, bg=self.theme["widget_bg"])
            theme_frame.pack(fill="x", pady=2)

            theme_radio = Radiobutton(
                theme_frame,
                variable=self.selected_theme,
                value=theme_name,
                command=lambda tn=theme_name: self._select_theme(tn),
                bg=self.theme["widget_bg"],
                fg=self.theme["widget_fg"],
                selectcolor=self.theme["widget_bg"],
                activebackground=self.theme["widget_bg"],
                activeforeground=self.theme["widget_fg"],
            )
            theme_radio.pack(side="left", padx=5)

            theme_label = Label(
                theme_frame,
                text=theme_name,
                bg=self.theme["widget_bg"],
                fg=self.theme["widget_fg"],
            )
            theme_label.pack(side="left", padx=5)

        # Highlight the selected theme
        self._highlight_selected_theme()

    def _highlight_selected_theme(self):
        selected = self.selected_theme.get()
        if not selected:
            return

        for widget in self.theme_listbox_frame.winfo_children():
            if isinstance(widget, Frame):
                for child in widget.winfo_children():
                    if isinstance(child, Radiobutton) and child.cget("value") == selected:
                        child.select()

    def _select_theme(self, theme_name: str):
        self.selected_theme.set(theme_name)
        save_selected_ctheme(theme_name)
        self._apply_selected_theme()

    def _apply_selected_theme(self):
        # Validate the theme exists before applying
        selected_theme_name = validate_selected_ctheme()
        if not selected_theme_name:
            return

        custom_themes = load_custom_themes()
        if selected_theme_name not in custom_themes:
            return

        self.ui.custom_theme = custom_themes[selected_theme_name]
        self.ui._apply_theme()

    def _update_theme_preview(self, field: str):
        if not self.custom_themes_enabled.get():
            return

        # Real-time Preview
        new_color = self.theme_fields[field].get()
        new_color = format_color_code(new_color)

        # Apply the color to the UI for live preview
        preview_theme = self._get_current_theme_data()
        self.ui.custom_theme = preview_theme
        self.ui._apply_theme()  # Use theme logic

    def _get_current_theme_data(self) -> dict:
        theme_data = {}
        for field, entry in self.theme_fields.items():
            color = entry.get()
            theme_data[field] = format_color_code(color)
        return theme_data

    def _save_custom_theme(self):
        theme_name = self.custom_theme_name.get().strip()
        if not theme_name:
            show_custom_dialog(self.parent, "error", "Error", "Please enter a theme name.")
            return

        # Replace spaces with underscores
        theme_name = theme_name.replace(" ", "_")

        # Check for empty fields
        for field, entry in self.theme_fields.items():
            if not entry.get().strip():
                show_custom_dialog(self.parent, "error", "Error", f"Please fill in the {field} field.")
                return

        # Get theme data
        theme_data = self._get_current_theme_data()

        # Save the theme
        if save_custom_theme(theme_name, theme_data):
            self._refresh_theme_list()
            self.custom_theme_name.set("")
            show_custom_dialog(self.parent, "info", "Success", f"Theme '{theme_name}' saved successfully!")
        else:
            show_custom_dialog(self.parent, "error", "Error", "Failed to save the theme.")

# ------------------------------------------------------------
import os
import subprocess
from urllib.parse import unquote, urlparse

# ------------------------------------------------------------
class DropZoneHelper:
    def __init__(
        self,
        parent_widget,
        on_files_callback,
        theme,
        allowed_extensions=None,
        initial_text="Drop files here or click to browse",
        height=8,
        font_size=12
    ):
        self.parent = parent_widget
        self.on_files_callback = on_files_callback
        self.theme = theme
        self.use_custom_theme = False
        self.allowed_extensions = [ext.lower() for ext in (allowed_extensions or [])]
        self.initial_text = initial_text
        self.drop_label = None
        self._dnd_window = None
        self._dnd_data = None

        try:
            parent_widget.tk.eval('package require tkdnd')
            self.tkdnd_available = True
        except:
            try:
                parent_widget.tk.eval('namespace eval ::tkdnd {}')
                parent_widget.tk.eval('set ::tkdnd::initialized 1')
                self.tkdnd_available = True
            except:
                self.tkdnd_available = False

        self._create_drop_zone(height, font_size)

    def _create_drop_zone(self, height, font_size):
        drop_frame = Frame(self.parent, bg=self.theme['bg'])
        drop_frame.pack(fill="both", expand=True, pady=10)

        self.drop_label = Label(
            drop_frame,
            text=self.initial_text,
            bg=self.theme['widget_bg'],
            fg=self.theme['fg'],
            height=height,
            relief="groove",
            font=("Helvetica", font_size),
            cursor="hand2",
            takefocus=True
        )
        self.drop_label.pack(fill="both", expand=True, padx=20, pady=10)

        # Bind events
        self.drop_label.bind("<Button-1>", self._on_click)
        self.drop_label.bind("<Control-v>", self._on_paste)
        self._bind_drag_and_drop()

    def _bind_drag_and_drop(self):
        if self.tkdnd_available and self._is_wayland():
            self.drop_label.bind("<ButtonPress>", self._wayland_dnd_start)
            self.drop_label.bind("<ButtonRelease>", self._wayland_dnd_stop)
            self.drop_label.bind("<Motion>", self._wayland_dnd_motion)
        else:
            self.drop_label.bind("<Enter>", self._xdnd_enter)
            self.drop_label.bind("<Leave>", lambda e: self.drop_label.config(bg=self.theme['widget_bg']))
            try:
                self.drop_label.bind("<XdndPosition>", self._xdnd_position)
                self.drop_label.bind("<XdndDrop>", self._xdnd_drop)
                try:
                    self._register_xdnd()
                except Exception as e:
                    print(f"[DropZoneHelper] XDND registration failed: {e}")
            except Exception as e:
                print(f"[DropZoneHelper] Could not bind X11 DND: {e}")
                self.drop_label.bind("<Enter>", lambda e: self.drop_label.config(bg=self.theme['hover_bg']))
                self.drop_label.bind("<Leave>", lambda e: self.drop_label.config(bg=self.theme['widget_bg']))

    def _is_wayland(self):
        return "wayland" in os.environ.get("XDG_SESSION_TYPE", "").lower()

    def _register_xdnd(self):
        try:
            self.parent.tk.call('package', 'require', 'xdnd')
            self.drop_label.tk.call('xdnd', 'bindtarget', self.drop_label._w, 'xdnd')
            self.drop_label.tk.call('bind', 'xdnd', '<XdndEnter>', self._xdnd_enter)
            self.drop_label.tk.call('bind', 'xdnd', '<XdndPosition>', self._xdnd_position)
            self.drop_label.tk.call('bind', 'xdnd', '<XdndDrop>', self._xdnd_drop)
        except Exception as e:
            print(f"[DropZoneHelper] XDND registration error: {e}")

    def _on_click(self, event=None):
        if self.allowed_extensions:
            file_patterns = [f"*{ext}" for ext in self.allowed_extensions]
            file_types = [(f"{', '.join(self.allowed_extensions)} files", " ".join(file_patterns)), ("All files", "*.*")]
        else:
            file_types = [("All files", "*.*")]

        try:
            paths = filedialog.askopenfilenames(title="Select files", filetypes=file_types)
            if paths:
                self._process_paths(paths)
        except Exception as e:
            print(f"[DropZoneHelper] File dialog error: {e}")

    def _on_paste(self, event):
        try:
            content = self.parent.clipboard_get()
            if not content:
                return
            paths = self._parse_clipboard(content)
            if paths:
                self._process_paths(paths)
        except tk.TclError:
            pass
        except Exception as e:
            print(f"[DropZoneHelper] Clipboard error: {e}")

    def _parse_clipboard(self, content):
        paths = []

        if content.startswith("x-special/gnome-copied-files"):
            parts = content.split("\n")
            uris = parts[1:] if len(parts) > 1 and parts[0] == "copy" else parts
            for uri in uris:
                path = self._uri_to_path(uri)
                if path and os.path.exists(path):
                    paths.append(path)
        elif content.startswith("file://"):
            for uri in content.split():
                path = self._uri_to_path(uri)
                if path and os.path.exists(path):
                    paths.append(path)
        elif os.path.exists(content):
            paths.append(content)

        return paths

    def _uri_to_path(self, uri):
        try:
            parsed = urlparse(uri)
            path = unquote(parsed.path).replace("%20", " ")
            if sys.platform == "win32" and path.startswith("/"):
                path = path[1:]
            return path
        except Exception:
            return None

    def _process_paths(self, paths):
        for path in paths:
            self._process_file_path(path)

    def _process_file_path(self, path):
        file_path = Path(path)

        if self.allowed_extensions:
            if not any(file_path.name.lower().endswith(ext) for ext in self.allowed_extensions):
                show_custom_dialog(self, "warning", "Invalid File", f"Only {', '.join(self.allowed_extensions)} files are supported")
                return

        if self.on_files_callback:
            self.on_files_callback(file_path)

    # ------------------------------------------------------------
    # X11 Handlers
    # ------------------------------------------------------------
    def _xdnd_enter(self, event):
        self.drop_label.config(bg=self.theme['hover_bg'])
        return "copy"

    def _xdnd_position(self, event):
        return "copy"

    def _xdnd_drop(self, event):
        try:
            data = self.parent.tk.call('selection', 'get', 'XdndSelection')
            paths = []
            for uri in data.split():
                if uri.startswith('file://'):
                    path = self._uri_to_path(uri)
                    if path and os.path.exists(path):
                        paths.append(path)
            if paths:
                self._process_paths(paths)
        except Exception as e:
            print(f"[DropZoneHelper] XDND drop error: {e}")
        return "copy"

    # ------------------------------------------------------------
    # Wayland Handlers
    # ------------------------------------------------------------
    def _wayland_dnd_start(self, event):
        self._dnd_data = None
        try:
            self._dnd_window = self.parent.tk.call('winfo', 'toplevel', self.drop_label._w)
            self.parent.tk.call(
                'tkdnd', 'dnd', 'bindtarget',
                self._dnd_window, 'text/uri-list', '<Drop>', self._wayland_drop
            )
        except tk.TclError:
            print("[DropZoneHelper] TkDnD not available - using file dialog fallback")
            self._on_click()

    def _wayland_dnd_motion(self, event):
        if self._dnd_window and self._dnd_data:
            try:
                self.parent.tk.call('tkdnd', 'dnd', 'motion', self._dnd_window, event.x_root, event.y_root)
            except tk.TclError:
                pass

    def _wayland_dnd_stop(self, event):
        try:
            if self._dnd_window:
                self.parent.tk.call('tkdnd', 'dnd', 'cleartarget', self._dnd_window)
        except tk.TclError:
            pass
        finally:
            self._dnd_window = None

    def _wayland_drop(self, event):
        if not self.tkdnd_available:
            return

        try:
            mime_types = ['text/uri-list', 'x-special/gnome-copied-files', 'UTF8_STRING']
            for mime in mime_types:
                try:
                    data = self.parent.tk.call('tkdnd', 'dnd', 'getdata', mime)
                    if not data:
                        continue

                    if mime == 'x-special/gnome-copied-files':
                        lines = data.split('\n')
                        uris = lines[1:] if lines and lines[0] == 'copy' else lines
                    else:
                        uris = data.split()

                    paths = []
                    for uri in uris:
                        if uri.startswith('file://'):
                            path = self._uri_to_path(uri)
                            if path and os.path.exists(path):
                                paths.append(path)

                    if paths:
                        self._process_paths(paths)
                        return

                except tk.TclError:
                    continue

            print("[DropZoneHelper] Could not process any known MIME types")
        except Exception as e:
            print(f"[DropZoneHelper] Wayland drop error: {e}")
        finally:
            self._wayland_dnd_stop(None)

    def update_text(self, new_text):
        if self.drop_label:
            self.drop_label.config(text=new_text)

# ------------------------------------------------------------
class VersionDropdownHelper:
    def __init__(self, parent_frame, target, on_select_callback=None, theme=None, disabled_if_single=True):
        self.parent_frame = parent_frame
        self.target = target
        self.on_select_callback = on_select_callback
        self.theme = theme or (parent_frame.master.DARK_THEME if hasattr(parent_frame.master, 'dark_mode') and parent_frame.master.dark_mode else parent_frame.master.LIGHT_THEME)
        self.disabled_if_single = disabled_if_single
        self.dropdown = None
        self.var = None
        self._create_dropdown()

    def _create_dropdown(self):
        installed_versions = self._get_installed_versions()
        if not installed_versions:
            Label(
                self.parent_frame,
                text=f"No {self.target.upper()} versions installed",
                bg=self.theme['bg'],
                fg=self.theme['fg']
            ).pack(side="left", padx=(0, 10))
            return

        self.var = tk.StringVar()
        if len(installed_versions) == 1 and self.disabled_if_single:
            self.var.set(installed_versions[0])
            Label(
                self.parent_frame,
                text=f"{self.target.upper()} {installed_versions[0]}",
                bg=self.theme['bg'],
                fg=self.theme['fg']
            ).pack(side="left", padx=(0, 10))
        else:
            self.dropdown = ttk.Combobox(
                self.parent_frame,
                textvariable=self.var,
                values=sorted(installed_versions, reverse=True),
                state="readonly",
                width=15,
                cursor="hand2"
            )
            self.dropdown.pack(side="left", padx=(0, 10))
            self.var.set(installed_versions[0])
            if self.on_select_callback:
                self.dropdown.bind("<<ComboboxSelected>>", lambda e: self.on_select_callback(self.var.get()))

    def _get_installed_versions(self):
        versions = log_manager.load_dlm_versions()
        target_versions = versions.get(self.target, {})
        return [v for v, installed in target_versions.items() if installed]

    def get_selected_version(self):
        return self.var.get() if self.var else None

# ------------------------------------------------------------
class CustomModalDialog:
    def __init__(self, parent, title, message, dialog_type="info", buttons=None, callback=None):
        self.parent = parent
        self.title = title
        self.message = message
        self.dialog_type = dialog_type
        self.callback = callback

        if buttons is None:
            if dialog_type == "yesno":
                buttons = [{"text": "Yes", "value": True}, {"text": "No", "value": False}]
            elif dialog_type == "ok":
                buttons = [{"text": "OK", "value": True}]
            elif dialog_type == "okcancel":
                buttons = [{"text": "OK", "value": True}, {"text": "Cancel", "value": False}]
            else:
                buttons = [{"text": "OK", "value": True}]

        self.buttons = buttons
        self.result = None
        self.dialog_frame = None
        self.overlay = None

    def show(self):
        theme = self.parent.DARK_THEME if hasattr(self.parent, 'dark_mode') and self.parent.dark_mode else self.parent.LIGHT_THEME

        overlay_color = "#202020"
        self.overlay = Frame(self.parent, bg=overlay_color)
        self.overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self.overlay.lift()

        try:
            self.overlay.attributes("-alpha", 0.7)
        except:
            pass

        self.overlay.bind("<Button-1>", lambda e: None)

        self.dialog_frame = Frame(self.parent, bg=theme['widget_bg'], padx=20, pady=20)
        self.dialog_frame.place(in_=self.parent, anchor="center", relx=0.5, rely=0.5)
        self.dialog_frame.lift()

        title_label = Label(
            self.dialog_frame,
            text=self.title,
            font=("Helvetica", 14, "bold"),
            bg=theme['widget_bg'],
            fg=theme['fg']
        )
        title_label.pack(fill="x", pady=(0, 10))

        message_label = Label(
            self.dialog_frame,
            text=self.message,
            font=("Helvetica", 11),
            bg=theme['widget_bg'],
            fg=theme['fg'],
            wraplength=400,
            justify="left"
        )
        message_label.pack(fill="x", pady=(0, 20))

        button_frame = Frame(self.dialog_frame, bg=theme['widget_bg'])
        button_frame.pack(fill="x")

        button_container = Frame(button_frame, bg=theme['widget_bg'])
        button_container.pack(expand=True)

        for i, btn in enumerate(self.buttons):
            button = Button(
                button_container,
                text=btn["text"],
                command=lambda v=btn["value"]: self._on_button_click(v),
                bg=theme['button_bg'],
                fg=theme['fg'],
                padx=15,
                pady=8,
                bd=0,
                relief='flat'
            )
            button.pack(side="left", padx=(0, 10) if i < len(self.buttons) - 1 else 0)

        self.dialog_frame.grab_set()

        self.parent.update_idletasks()
        self.dialog_frame.update_idletasks()

        self.parent.wait_window(self.dialog_frame)

        if self.overlay:
            self.overlay.destroy()
        self.overlay = None
        self.dialog_frame = None

        return self.result

    def _on_button_click(self, value):
        self.result = value
        if self.callback:
            self.callback(value)

        if self.dialog_frame:
            self.dialog_frame.grab_release()
            self.dialog_frame.destroy()

# ------------------------------------------------------------
class SearchManager:
    def __init__(self, ui=None):
        self._ui = ui
        self.all_html_files = all_html_files
        self.file_status = file_status

    def set_ui(self, ui) -> None:
        self._ui = ui

    def _get_search_language(self) -> str:
        return GENERAL_SETTINGS.get("search_language", "english")

    def download_appid_html(self, appid: str) -> Path | None:
        appid = appid.strip()
        if not appid.isdigit():
            log_manager.log_error(f"⚠️ Invalid AppID: {appid}")
            if self._ui:
                show_custom_dialog(self._ui, "Invalid AppID", f"{appid} is not a valid numeric AppID")
            return None

        websearch_language = self._get_search_language()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': f'{websearch_language},en-US;q=0.9,en;q=0.8'
        }

        try:
            log_manager.log_message(f"Downloading Steam Store data for AppID {appid}...")

            # Get game info from Steam Store API
            store_url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l={websearch_language}"
            store_response = requests.get(store_url, timeout=30)
            store_response.raise_for_status()
            store_data = store_response.json()

            if str(appid) not in store_data or not store_data[str(appid)].get('success', False):
                log_manager.log_error(f"❌ Game {appid} not found on Steam Store")
                if self._ui:
                    show_custom_dialog(self._ui, "Game Not Found", f"AppID {appid} not found")
                return None

            game_data = store_data[str(appid)]['data']
            game_name = game_data.get('name', f"Unknown Game ({appid})")
            clean_name = clean_title(game_name)

            # Get DLC info
            dlc_list = game_data.get('dlc', [])
            dlc_info = {}
            for dlc_id in dlc_list:
                try:
                    dlc_response = requests.get(
                        f"https://store.steampowered.com/api/appdetails?appids={dlc_id}&l={websearch_language}",
                        timeout=15
                    )
                    dlc_response.raise_for_status()
                    dlc_data = dlc_response.json()
                    if str(dlc_id) in dlc_data and dlc_data[str(dlc_id)].get('success', False):
                        dlc_name = dlc_data[str(dlc_id)]['data'].get('name', f'DLC {dlc_id}')
                        dlc_info[dlc_id] = dlc_name
                except Exception as e:
                    log_manager.log_message(f"⚠️ Could not fetch DLC {dlc_id}: {e}")
                    dlc_info[dlc_id] = f"Unknown DLC {dlc_id}"

            store_page_url = f"https://store.steampowered.com/app/{appid}/?l={websearch_language}"
            store_achievements = {}

            try:
                page_response = requests.get(store_page_url, headers=headers, timeout=30)
                page_response.raise_for_status()
                page_soup = BeautifulSoup(page_response.text, 'html.parser')

                achievement_rows = page_soup.select('div.achievement_row, div.achievementBlock')
                if not achievement_rows:
                    achievement_rows = page_soup.select('div[class*="achievement"]')

                for row in achievement_rows:
                    try:
                        name = row.select_one('h3, h4, .achievementName')
                        name = name.get_text(strip=True) if name else "Unknown"

                        description = row.select_one('.achievementDesc, .description')
                        description = description.get_text(strip=True) if description else "No description"

                        hidden = 1 if row.select_one('.achievementHidden, .hidden') else 0

                        icon_img = row.select_one('img')
                        icon_hash = ""
                        if icon_img and icon_img.get('src'):
                            src = icon_img['src']
                            match = re.search(r'/([a-f0-9]{40})\.jpg', src)
                            if match:
                                icon_hash = match.group(1)

                        if name:
                            store_achievements[name] = {
                                'icon': icon_hash,
                                'description': description,
                                'hidden': hidden
                            }

                    except Exception as e:
                        log_manager.log_message(f"⚠️ Error parsing store achievement: {e}")
                        continue

                log_manager.log_message(f"✅ Found {len(store_achievements)} achievements on Store page")

            except Exception as e:
                log_manager.log_message(f"⚠️ Could not scrape store page: {e}")

            community_url = f"https://steamcommunity.com/stats/{appid}/achievements/"
            community_achievements = {}

            try:
                log_manager.log_message(f"🔍 Scraping Community stats page for gray icons...")
                community_response = requests.get(community_url, headers=headers, timeout=30)

                if community_response.status_code == 403:
                    log_manager.log_message(f"⚠️ Community page access denied (403) for {appid}")
                else:
                    community_response.raise_for_status()
                    community_soup = BeautifulSoup(community_response.text, 'html.parser')

                    for row in community_soup.select('div.achieveRow'):
                        try:
                            img_tag = row.select_one('div.achieveImgHolder img')
                            icon_hash = ""
                            if img_tag and img_tag.get('src'):
                                src = img_tag['src']
                                match = re.search(r'/([a-f0-9]{40})\.jpg', src)
                                if match:
                                    icon_hash = match.group(1)

                            name_tag = row.select_one('div.achieveTxt h3')
                            name = name_tag.get_text(strip=True) if name_tag else "Unknown"

                            desc_tag = row.select_one('div.achieveTxt h5')
                            description = desc_tag.get_text(strip=True) if desc_tag else ""

                            if name:
                                community_achievements[name] = {
                                    'icon': icon_hash,
                                    'description': description
                                }

                        except Exception as e:
                            log_manager.log_message(f"⚠️ Error parsing community achievement: {e}")
                            continue

                    log_manager.log_message(f"✅ Found {len(community_achievements)} achievements on Community page")

            except Exception as e:
                log_manager.log_message(f"⚠️ Could not scrape community page: {e}")

            achievements = []

            all_names = set(store_achievements.keys()) | set(community_achievements.keys())

            for name in all_names:
                store_data = store_achievements.get(name, {})
                community_data = community_achievements.get(name, {})

                description = store_data.get('description', community_data.get('description', 'No description'))
                hidden = store_data.get('hidden', 0)

                icon_hash = store_data.get('icon', '')
                icongray_hash = community_data.get('icon', '')

                if not icon_hash and icongray_hash:
                    icon_hash = icongray_hash
                if not icongray_hash and icon_hash:
                    icongray_hash = icon_hash

                achievements.append({
                    'name': name,
                    'displayName': name,
                    'description': description,
                    'hidden': hidden,
                    'icon': icon_hash,
                    'icongray': icongray_hash
                })

            achievements.sort(key=lambda x: x['name'].lower())

            html_folder = pathlib.Path(__file__).resolve().parent / "HTML"
            html_folder.mkdir(parents=True, exist_ok=True)
            html_path = html_folder / f"{clean_name}.html"

            html_content = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{game_name} - SteamDB</title>
        <meta property="og:url" content="https://steamdb.info/app/{appid}/stats/">
        <link rel="canonical" href="https://steamdb.info/app/{appid}/stats/">
    </head>
    <body>
        <h1 itemprop="name">{game_name}</h1>
        <div id="achievements">"""

            for ach in achievements:
                display_name = ach.get('displayName', 'Unknown')
                name = ach.get('name', '')
                description = ach.get('description', 'No description')
                hidden = int(ach.get('hidden', 0))
                icon_hash = ach.get('icon', '')
                icongray_hash = ach.get('icongray', '')

                icon_filename = f"{icon_hash}.jpg" if icon_hash else "No icon"
                icongray_filename = f"{icongray_hash}.jpg" if icongray_hash else "No icon"

                html_content += f"""
            <div id="achievement-{name}">
                <div class="achievement_api">{name}</div>
                <div class="achievement_name">{display_name}</div>
                <div class="achievement_desc">{description}</div>
                <div class="achievement_image" data-name="{icon_filename}"></div>
                <div class="achievement_image_small" data-name="{icongray_filename}"></div>
                {"<span class=\"achievement_spoiler\"></span>" if hidden else ""}
            </div>"""

            if dlc_info:
                html_content += f"<!-- DLC Information:\n"
                for dlc_id, dlc_name in dlc_info.items():
                    html_content += f"        DLC {dlc_id}: {dlc_name}\n"
                html_content += "    -->"

            html_content += """
    </div>
    </body>
    </html>"""

            html_path.write_text(html_content, encoding="utf-8")
            log_manager.log_message(f"✅ Generated HTML for AppID {appid} ({game_name}) at {html_path}")

            if html_path not in self.all_html_files:
                self.all_html_files.append(html_path)
                self.file_status[html_path] = "waiting"
                job_tracker.add_job()
                threading.Thread(target=lambda: _run_main_in_thread(html_path), daemon=True).start()

            if self._ui and hasattr(self._ui, 'search_entry'):
                self._ui.after(0, lambda: self._ui.search_entry.delete(0, tk.END))

            return html_path

        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            log_manager.log_message(f"❌ Request failed for AppID {appid}: {error_msg}")
            if self._ui:
                def show_options():
                    result = messagebox.askyesnocancel(
                        "Download Failed",
                        f"Could not download AppID {appid}:\n{error_msg}\n\n"
                        "Options:\n"
                        "1. Open in browser to save manually (Yes)\n"
                        "2. Check your internet connection (No)\n"
                        "3. Cancel (Cancel)"
                    )
                    if result:
                        webbrowser.open(f"https://store.steampowered.com/app/{appid}/")
                        if self._ui:
                            self._ui.search_entry.delete(0, tk.END)
                show_options()
            return None

        except Exception as e:
            error_msg = str(e)
            log_manager.log_error(Exception(f"Error processing AppID {appid}: {error_msg}"), "download_appid_html")
            if self._ui:
                show_custom_dialog(self._ui, "Error", f"Failed to process AppID {appid}:\n{error_msg}")
            return None

    def download_appid_via_steam_api(self, appid: str, api_key: str) -> Path | None:
        try:
            store_url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
            store_response = requests.get(store_url, timeout=30)
            store_response.raise_for_status()
            store_data = store_response.json()

            if str(appid) not in store_data or not store_data[str(appid)].get('success', False):
                log_manager.log_error(f"❌ Game {appid} not found on Steam Store")
                if self._ui:
                    show_custom_dialog(self._ui, "Game Not Found", f"AppID {appid} not found")
                return None

            game_name = store_data[str(appid)]['data']['name']
            clean_name = clean_title(game_name)

            api_url = f"https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/"
            params = {
                "appid": appid,
                "format": "json",
                "key": api_key
            }
            response = requests.get(api_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if not data.get('game', {}).get('availableGameStats', {}).get('achievements'):
                log_manager.log_error(f"⚠️ No achievements found for AppID {appid}")
                if self._ui:
                    show_custom_dialog(self._ui, "No Achievements", f"AppID {appid} has no achievements")
                return None

            html_folder = pathlib.Path(__file__).resolve().parent / "HTML"
            html_folder.mkdir(parents=True, exist_ok=True)

            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{game_name} - SteamDB</title>
    <meta property="og:url" content="https://steamdb.info/app/{appid}/stats/">
    <link rel="canonical" href="https://steamdb.info/app/{appid}/stats/">
</head>
<body>
    <h1 itemprop="name">{game_name}</h1>
    <div id="achievements">
"""

            for ach in data['game']['availableGameStats']['achievements']:
                display_name = ach.get('displayName', 'Unknown')
                name = ach.get('name', '')
                description = ach.get('description', 'No description')
                hidden = int(ach.get('hidden', 0))
                icon_hash = ach.get('icon', '')
                icongray_hash = ach.get('icongray', '')

                icon_filename = f"{icon_hash}.jpg" if icon_hash else "No icon"
                icongray_filename = f"{icongray_hash}.jpg" if icongray_hash else "No icon"

                html_content += f"""
        <div id="achievement-{name}">
            <div class="achievement_api">{name}</div>
            <div class="achievement_name">{display_name}</div>
            <div class="achievement_desc">{description}</div>
            <div class="achievement_image" data-name="{icon_filename}"></div>
            <div class="achievement_image_small" data-name="{icongray_filename}"></div>
            {"<span class=\"achievement_spoiler\"></span>" if hidden else ""}
        </div>
"""
            html_content += """
    </div>
</body>
</html>
"""

            html_path = html_folder / f"{clean_name}.html"
            html_path.write_text(html_content, encoding="utf-8")
            log_manager.log_message(f"✅ Generated HTML for AppID {appid} ({game_name}) at {html_path}")

            if html_path not in self.all_html_files:
                self.all_html_files.append(html_path)
                self.file_status[html_path] = "waiting"
                job_tracker.add_job()
                threading.Thread(target=lambda: _run_main_in_thread(html_path), daemon=True).start()

            if self._ui and hasattr(self._ui, 'search_entry'):
                self._ui.after(0, lambda: self._ui.search_entry.delete(0, tk.END))

            return html_path

        except Exception as e:
            error_msg = str(e)
            log_manager.log_error(Exception(f"Steam API error for AppID {appid}: {error_msg}"), "download_appid_via_steam_api")
            if self._ui:
                show_custom_dialog(self._ui, "Steam API Error", f"Failed to fetch data:\n{error_msg}\n\nCheck your API key in Settings.")
            return None

    def filter_local_search(self, search_text: str, html_files: list[Path]) -> list[Path]:
        if not search_text:
            return []
        return [p for p in html_files if search_text.lower() in p.name.lower()]

# ------------------------------------------------------------
class DownloadManager:
    TARGETS = {
        "steamless": {
            "name": "Steamless",
            "release_url": "https://api.github.com/repos/atom0s/Steamless/releases",
            "asset_patterns": [r"\.zip$"],
            "install_dir": APP_FOLDER / "steamless",
        },
        "gbe": {
            "name": "GBE",
            "release_url": "https://api.github.com/repos/Detanup01/gbe_fork/releases",
            "asset_patterns": [
                r"emu-linux-release\.tar\.bz2$",
                r"emu-win-release\.7z$"
            ],
            "install_dir": GBE_FOLDER,
            "tools_dir": GBE_TOOLS_FOLDER,
        },
        "gse": {
            "name": "GSE",
            "release_url": "https://api.github.com/repos/alex47exe/gse_fork/releases",
            "asset_patterns": [
                r"emu-linux-release\.tar\.bz2$",
                r"emu-win-release\.7z$"
            ],
            "install_dir": GSE_FOLDER,
            "tools_dir": GSE_TOOLS_FOLDER,
        }
    }

    def __init__(self, target: str, ui_instance=None):
        self.target = target
        self.ui = ui_instance
        self.config = self.TARGETS.get(target)
        if not self.config:
            raise ValueError(f"Unknown download target: {target}")

        self.download_queue = []
        self.current_download = None
        self.download_status = {}
        self._cached_releases = None
        self._load_cached_releases()

    def _load_cached_releases(self):
        if not DLM_CACHE_FILE.exists():
            self._cached_releases = None
            return
        try:
            cache_mtime = DLM_CACHE_FILE.stat().st_mtime
            if time.time() - cache_mtime < 8 * 60 * 60:  # 8 hours
                with open(DLM_CACHE_FILE, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                self._cached_releases = cached_data.get(self.target)
            else:
                self._cached_releases = None
        except Exception as e:
            print(f"⚠️ Error loading cached releases for {self.target}: {e}")
            self._cached_releases = None

    def _save_cached_releases(self, releases: list):
        try:
            cached_data = {}
            if DLM_CACHE_FILE.exists():
                with open(DLM_CACHE_FILE, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
            cached_data[self.target] = releases
            DLM_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DLM_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cached_data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving cached releases for {self.target}: {e}")

    def fetch_releases(self, callback=None):
        if self._cached_releases is not None:
            if callback:
                self.ui.after(0, lambda: callback(self._cached_releases))
            return

        def _fetch():
            try:
                response = requests.get(self.config["release_url"], timeout=10)
                response.raise_for_status()
                releases = response.json()
                releases.sort(
                    key=lambda x: x.get('published_at', ''),
                    reverse=True
                )

                if self.target in ["gbe", "gse"]:
                    filtered = []
                    for release in releases:
                        version = release.get('tag_name', '')
                        if not version:
                            continue
                        has_assets = False
                        for asset in release.get('assets', []):
                            if any(re.search(p, asset['name'], re.I) for p in self.config["asset_patterns"]):
                                has_assets = True
                                break
                        if has_assets:
                            filtered.append(release)
                    releases = filtered

                self._cached_releases = releases
                self._save_cached_releases(releases)

                if callback:
                    self.ui.after(0, lambda: callback(releases))
            except Exception as e:
                print(f"⚠️ Error fetching {self.config['name']} releases: {e}")
                if callback:
                    self.ui.after(0, lambda: callback([]))

        threading.Thread(target=_fetch, daemon=True).start()

    def get_cached_releases(self) -> list | None:
        return self._cached_releases

    def _get_version_dir(self, version: str) -> Path:
        return self.config["install_dir"] / version

    def is_installed(self, version: str) -> bool:
        return is_version_installed(self.target, version)

    def _clear_temp_folder(self, temp_folder: Path):
        try:
            if temp_folder.exists():
                shutil.rmtree(temp_folder, ignore_errors=True)
        except Exception as e:
            print(f"Error cleaning temp folder {temp_folder}: {e}")

    def download_and_install(self, version: str, release_data: dict):
        if version not in self.download_status:
            self.download_status[version] = {
                'status': 'pending',
                'version': version,
                'frame': None,
                'version_label': None,
                'status_frame': None,
                'download_btn': None,
                'cancel_btn': None,
                'check_label': None,
                'delete_btn': None,
                'retry_btn': None,
            }
        else:
            for field in ['frame', 'version_label', 'status_frame', 'download_btn',
                         'cancel_btn', 'check_label', 'delete_btn', 'retry_btn']:
                if field not in self.download_status[version]:
                    self.download_status[version][field] = None

        if self.download_status[version]['status'] in ['completed', 'queued', 'downloading', 'extracting']:
            return

        self.download_status[version]['status'] = 'queued'
        self.download_queue.append(version)
        self._update_status(version, 'queued')

        if not self.current_download:
            self._process_queue()

    def _process_queue(self):
        if not self.download_queue or self.current_download:
            return

        version = self.download_queue.pop(0)
        self.current_download = version
        self._update_status(version, 'downloading')

        threading.Thread(
            target=self._download_and_extract,
            args=(version,),
            daemon=True
        ).start()

    def _download_and_extract(self, version: str):
        try:
            response = requests.get(self.config["release_url"], timeout=10)
            response.raise_for_status()
            releases = response.json()
            release = next((r for r in releases if r['tag_name'] == version), None)
            if not release:
                self._update_status(version, 'error')
                print(f"Release {version} not found for {self.config['name']}")
                return

            assets = []
            for a in release.get('assets', []):
                if any(re.search(p, a['name'], re.I) for p in self.config["asset_patterns"]):
                    assets.append(a)

            if not assets:
                self._update_status(version, 'error')
                print(f"No matching assets found for {self.config['name']} {version}")
                return

            print(f"Found {len(assets)} assets for {self.config['name']} {version}")

            linux_dl = DOWNLOADS_FOLDER / "linux" / version
            windows_dl = DOWNLOADS_FOLDER / "windows" / version
            temp_extract = DOWNLOADS_FOLDER / f"{version}_temp"

            for d in [linux_dl, windows_dl, temp_extract]:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)

            for asset in assets:
                asset_name = asset['name']
                is_linux = 'linux' in asset_name.lower() or asset_name.endswith('.tar.bz2')
                is_windows = 'win' in asset_name.lower() or asset_name.endswith('.7z')

                clean_name = asset_name
                for ext in ['.tar.bz2', '.tar.gz', '.tar', '.7z', '.zip']:
                    if clean_name.lower().endswith(ext):
                        clean_name = clean_name[:-len(ext)]

                if is_linux:
                    dl_path = linux_dl / asset_name
                    extract_target = linux_dl / clean_name
                elif is_windows:
                    dl_path = windows_dl / asset_name
                    extract_target = windows_dl / clean_name
                else:
                    dl_path = temp_extract / asset_name
                    extract_target = temp_extract / clean_name

                log_manager.log_message(f"Downloading {asset_name}...")

                try:
                    resp = requests.get(asset['browser_download_url'], stream=True, timeout=30)
                    resp.raise_for_status()
                    with open(dl_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                if self.download_status.get(version, {}).get('status') == 'cancelled':
                                    f.close()
                                    dl_path.unlink(missing_ok=True)
                                    self._update_status(version, 'pending')
                                    return
                except Exception as e:
                    print(f"Download failed for {asset_name}: {e}")
                    self._clear_temp_folder(temp_extract)
                    self._update_status(version, 'error')
                    return

                if extract_target.exists():
                    shutil.rmtree(extract_target, ignore_errors=True)
                extract_target.mkdir(parents=True, exist_ok=True)

                try:
                    if asset_name.endswith('.zip'):
                        with zipfile.ZipFile(dl_path, 'r') as zip_ref:
                            zip_ref.extractall(extract_target)
                    elif asset_name.endswith('.tar.bz2'):
                        subprocess.run(["tar", "xjf", str(dl_path), "-C", str(extract_target)], check=True)
                    elif asset_name.endswith('.7z'):
                        if sys.platform.startswith("win"):
                            subprocess.run(["7z", "x", str(dl_path), f"-o{extract_target}", "-y"], check=True)
                        else:
                            subprocess.run(["7zr", "x", str(dl_path), f"-o{extract_target}", "-y"], check=True)
                except Exception as e:
                    print(f"Extraction failed for {asset_name}: {e}")
                    self._clear_temp_folder(temp_extract)
                    self._update_status(version, 'error')
                    return

                extracted_items = list(extract_target.iterdir())
                if len(extracted_items) == 1 and extracted_items[0].is_dir():
                    subdir = extracted_items[0]
                    for item in subdir.iterdir():
                        dest = extract_target / item.name
                        if dest.exists():
                            if dest.is_dir():
                                shutil.rmtree(dest, ignore_errors=True)
                            else:
                                dest.unlink()
                        shutil.move(str(item), str(dest))
                    subdir.rmdir()

                dl_path.unlink(missing_ok=True)
                log_manager.log_message(f"Extracted {asset_name} to {extract_target}")

            self._update_status(version, 'extracting')
            log_manager.log_message(f"Installing files for {self.config['name']} {version}...")

            version_dir = self._get_version_dir(version)
            if version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
            version_dir.mkdir(parents=True, exist_ok=True)

            if self.target == "steamless":
                self._install_steamless(temp_extract, self._get_version_dir(version))
            else:
                self._install_emu(DOWNLOADS_FOLDER, version)

            installed_files = list(version_dir.rglob("*"))
            if not installed_files:
                print(f"CRITICAL: No files installed to {version_dir}!")
                self._update_status(version, 'error')
                return

            log_manager.log_message(f"Successfully installed {len(installed_files)} files to {version_dir}")
            self._clear_temp_folder(temp_extract)

            linux_parent = DOWNLOADS_FOLDER / "linux"
            windows_parent = DOWNLOADS_FOLDER / "windows"

            for d in [linux_parent, windows_parent]:
                if d.exists():
                    try:
                        shutil.rmtree(d, ignore_errors=True)
                        log_manager.log_message(f"🗑️ Deleted download folder: {d}")
                    except Exception as e:
                        print(f"⚠️ Failed to delete {d}: {e}")

            mark_version_installed(self.target, version, True)
            self._update_status(version, 'completed')

        except Exception as e:
            print(f"Error in download/extract for {version}: {e}")
            self._update_status(version, 'error')
        finally:
            self.current_download = None
            self._process_queue()

#----------------------------------------------------------------------------------------------------
    def _install_files(self, extract_folder: Path, install_dir: Path):
        if self.target == "steamless":
            self._install_steamless(extract_folder, install_dir)
        elif self.target in ["gbe", "gse"]:
            self._install_emu(extract_folder, install_dir.name)
#----------------------------------------------------------------------------------------------------
    def _install_steamless(self, extract_folder: Path, install_dir: Path):
        plugins_folder = None
        for root, dirs, files in os.walk(extract_folder):
            if "Plugins" in dirs:
                plugins_folder = Path(root) / "Plugins"
                break

        dest_plugins = install_dir / "Plugins"
        if dest_plugins.exists():
            shutil.rmtree(dest_plugins, ignore_errors=True)
        dest_plugins.mkdir(parents=True, exist_ok=True)

        if plugins_folder and plugins_folder.exists():
            for dll_path in plugins_folder.glob("*.dll"):
                shutil.copy2(dll_path, dest_plugins / dll_path.name)
                shutil.copy2(dll_path, install_dir / dll_path.name)
                log_manager.log_message(f"📄 Copied {dll_path.name} to Plugins/ and root of {install_dir}")

            for item in plugins_folder.iterdir():
                if not item.name.endswith('.dll'):
                    dest = dest_plugins / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)

            log_manager.log_message(f"📁 Plugins folder created at {dest_plugins}")

        for file in extract_folder.rglob("*"):
            if file.is_file():
                if file.name == "Steamless.CLI.exe":
                    dest = install_dir / "Steamless.CLI.exe"
                    if dest.exists(): dest.unlink()
                    shutil.move(str(file), str(dest))
                elif file.name == "Steamless.CLI.exe.config":
                    dest = install_dir / "Steamless.CLI.exe.config"
                    if dest.exists(): dest.unlink()
                    shutil.move(str(file), str(dest))

        tracking_file = APP_FOLDER / ".steamless_versions.json"
        try:
            tracking_data = {}
            if tracking_file.exists():
                with open(tracking_file, 'r', encoding='utf-8') as f:
                    tracking_data = json.load(f)
            version_name = install_dir.name
            installed_files = [str(p.relative_to(install_dir)) for p in install_dir.rglob("*") if p.is_file()]
            tracking_data[version_name] = installed_files
            tracking_file.parent.mkdir(parents=True, exist_ok=True)
            with open(tracking_file, 'w', encoding='utf-8') as f:
                json.dump(tracking_data, f, indent=2)
            log_manager.log_message(f"📝 Saved Steamless tracking file: {tracking_file}")
        except Exception as e:
            print(f"⚠️ Failed to save Steamless tracking file: {e}")

#----------------------------------------------------------------------------------------------------
    def _install_emu(self, extract_folder: Path, version: str):
        is_gbe = self.target == "gbe"
        emu = "gbe" if is_gbe else "gse"

        linux_base = APP_FOLDER / emu / version / "Linux"
        windows_base = APP_FOLDER / emu / version / "Windows"
        client_base = windows_base / "client"
        old_base = windows_base / "old"
        tools_base = TOOLS_FOLDER / f"{emu}_tools" / version
        tracking_file = APP_FOLDER / emu / f".{emu}_{version}.json"
        installed_paths = []

        for d in [linux_base, windows_base, client_base, old_base, tools_base]:
            d.mkdir(parents=True, exist_ok=True)

        def find_file_in_dir(dir_path: Path, filename: str) -> Path | None:
            if not dir_path.exists():
                return None
            target_lower = filename.lower()

            for f in dir_path.iterdir():
                if f.is_file() and f.name.lower() == target_lower:
                    return f

            for root, _, files in os.walk(dir_path):
                for f in files:
                    if f.lower() == target_lower:
                        return Path(root) / f
            return None

        def find_dir(root: Path, name: str) -> Path | None:
            if not root.exists():
                return None
            target_lower = name.lower()

            for d in root.iterdir():
                if d.is_dir() and d.name.lower() == target_lower:
                    return d

            for r, dirs, _ in os.walk(root):
                for d in dirs:
                    if d.lower() == target_lower:
                        return Path(r) / d
            return None

        linux_root = extract_folder / "linux"
        if linux_root.exists():
            exp_folder = find_dir(linux_root, "experimental")
            tools_folder = find_dir(linux_root, "tools")

            if exp_folder:
                for arch_32 in ["x86", "x32"]:
                    x86_src = exp_folder / arch_32
                    if x86_src.exists():
                        dest = linux_base / "x32"
                        dest.mkdir(parents=True, exist_ok=True)
                        for fname in ["libsteam_api.so", "steamclient.so"]:
                            src = find_file_in_dir(x86_src, fname)
                            if src:
                                dest_file = dest / src.name
                                if dest_file.exists():
                                    dest_file.unlink()
                                shutil.copy2(src, dest_file)
                                src.unlink()
                                installed_paths.append(str(dest_file))
                                log_manager.log_message(f"✅ Linux x32: {src.name}")
                        break

                for arch_64 in ["x64", "x86_64"]:
                    x64_src = exp_folder / arch_64
                    if x64_src.exists():
                        dest = linux_base / "x64"
                        dest.mkdir(parents=True, exist_ok=True)
                        for fname in ["libsteam_api.so", "steamclient.so"]:
                            src = find_file_in_dir(x64_src, fname)
                            if src:
                                dest_file = dest / src.name
                                if dest_file.exists():
                                    dest_file.unlink()
                                shutil.copy2(src, dest_file)
                                src.unlink()
                                installed_paths.append(str(dest_file))
                                log_manager.log_message(f"✅ Linux x64: {src.name}")
                        break

            if tools_folder:
                tools_src = tools_folder / "generate_interfaces"
                if tools_src.exists():
                    for fname in ["generate_interfaces_x32", "generate_interfaces_x86", "generate_interfaces_x64"]:
                        src = find_file_in_dir(tools_src, fname)
                        if src:
                            dest_file = tools_base / src.name
                            if dest_file.exists():
                                dest_file.unlink()
                            shutil.copy2(src, dest_file)
                            src.unlink()
                            installed_paths.append(str(dest_file))
                            try:
                                os.chmod(dest_file, 0o755)
                            except:
                                pass
                            log_manager.log_message(f"✅ Linux tool: {src.name}")

        windows_root = extract_folder / "windows"
        if windows_root.exists():
            sc_src = find_dir(windows_root, "steamclient_experimental")
            old_src = find_dir(windows_root, "steam_old_lib")
            exp_folder = find_dir(windows_root, "experimental")
            tools_folder = find_dir(windows_root, "tools")

            if sc_src and sc_src.exists():
                for fname in ["GameOverlayRenderer.dll", "GameOverlayRenderer64.dll", "steamclient.dll", "steamclient64.dll", "steamclient_loader_x32.exe", "steamclient_loader_x86.exe", "steamclient_loader_x64.exe", "ColdClientLoader.ini"]:
                    src = find_file_in_dir(sc_src, fname)
                    if src:
                        dest_file = client_base / src.name
                        if dest_file.exists():
                            dest_file.unlink()
                        shutil.copy2(src, dest_file)
                        src.unlink()
                        installed_paths.append(str(dest_file))
                        log_manager.log_message(f"✅ Windows client: {src.name}")

                extra_src = sc_src / "extra_dlls"
                if extra_src.exists():
                    dest_extra = client_base / "extra_dlls"
                    dest_extra.mkdir(parents=True, exist_ok=True)
                    for fname in ["steamclient_extra_x64.dll", "steamclient_extra_x32.dll", "steamclient_extra_x86.dll"]:
                        src = find_file_in_dir(extra_src, fname)
                        if src:
                            dest_file = dest_extra / src.name
                            if dest_file.exists():
                                dest_file.unlink()
                            shutil.copy2(src, dest_file)
                            src.unlink()
                            installed_paths.append(str(dest_file))
                            log_manager.log_message(f"✅ Windows extra_dll: {src.name}")

            if old_src and old_src.exists():
                src = find_file_in_dir(old_src, "Steam.dll")
                if src:
                    dest_file = old_base / src.name
                    if dest_file.exists():
                        dest_file.unlink()
                    shutil.copy2(src, dest_file)
                    src.unlink()
                    installed_paths.append(str(dest_file))
                    log_manager.log_message(f"✅ Windows old lib: {src.name}")

            if exp_folder and exp_folder.exists():
                for arch_32 in ["x86", "x32"]:
                    x86_src = exp_folder / arch_32
                    if x86_src.exists():
                        dest = windows_base / "x32"
                        dest.mkdir(parents=True, exist_ok=True)
                        for fname in ["steam_api.dll", "steamclient.dll"]:
                            src = find_file_in_dir(x86_src, fname)
                            if src:
                                dest_file = dest / src.name
                                if dest_file.exists():
                                    dest_file.unlink()
                                shutil.copy2(src, dest_file)
                                src.unlink()
                                installed_paths.append(str(dest_file))
                                log_manager.log_message(f"✅ Windows x32: {src.name}")
                        break

                for arch_64 in ["x64", "x86_64"]:
                    x64_src = exp_folder / arch_64
                    if x64_src.exists():
                        dest = windows_base / "x64"
                        dest.mkdir(parents=True, exist_ok=True)
                        for fname in ["steam_api64.dll", "steamclient64.dll"]:
                            src = find_file_in_dir(x64_src, fname)
                            if src:
                                dest_file = dest / src.name
                                if dest_file.exists():
                                    dest_file.unlink()
                                shutil.copy2(src, dest_file)
                                src.unlink()
                                installed_paths.append(str(dest_file))
                                log_manager.log_message(f"✅ Windows x64: {src.name}")
                        break

            if tools_folder:
                tools_src = tools_folder / "generate_interfaces"
                if tools_src.exists():
                    for fname in ["generate_interfaces_x64.exe", "generate_interfaces_x32.exe", "generate_interfaces_x86.exe"]:
                        src = find_file_in_dir(tools_src, fname)
                        if src:
                            dest_file = tools_base / src.name
                            if dest_file.exists():
                                dest_file.unlink()
                            shutil.copy2(src, dest_file)
                            src.unlink()
                            installed_paths.append(str(dest_file))
                            log_manager.log_message(f"✅ Windows tool: {src.name}")

            try:
                tracking_file.parent.mkdir(parents=True, exist_ok=True)
                with open(tracking_file, "w", encoding="utf-8") as f:
                    json.dump(installed_paths, f, indent=2)
                log_manager.log_message(f"📝 Saved file: {tracking_file}")
            except Exception as e:
                print(f"⚠️ Failed to save path file: {e}")

    def _update_status(self, version: str, status: str):
        if version not in self.download_status:
            self.download_status[version] = {'status': status}
            return

        self.download_status[version]['status'] = status

        if (self.ui and
            version in self.download_status and
            self.download_status[version].get('status_frame') is not None):
            self.ui.after(0, lambda v=version, s=status: self._update_ui_status(v, s))

            self.download_status[version]['status'] = status
            if self.ui:
                self.ui.after(0, lambda v=version, s=status: self._update_ui_status(v, s))

    def _update_ui_status(self, version: str, status: str):
        if version not in self.download_status:
            return

        entry = self.download_status[version]

        if entry.get('status_frame') is None or not entry['status_frame'].winfo_exists():
            return

        theme = self.ui.DARK_THEME if self.ui.dark_mode else self.ui.LIGHT_THEME

        for widget in entry['status_frame'].winfo_children():
            widget.destroy()

        if status == 'pending':
            download_btn = Button(
                entry['status_frame'],
                text="⬇️",
                command=lambda v=version: self.download_and_install(v, {}),
                bg=theme['button_bg'],
                fg=theme['fg'],
                bd=0,
                relief='flat',
                padx=5,
                pady=2
            )
            download_btn.pack(side="left")
            entry['download_btn'] = download_btn

        elif status in ['queued', 'downloading', 'extracting']:
            Label(
                entry['status_frame'],
                text="⏳",
                bg=theme['bg'],
                fg=theme['fg']
            ).pack(side="left")

            cancel_btn = Button(
                entry['status_frame'],
                text="❌",
                command=lambda v=version: self.cancel_download(v),
                bg=theme['button_bg'],
                fg=theme['fg'],
                bd=0,
                relief='flat',
                padx=5,
                pady=2
            )
            cancel_btn.pack(side="left")
            entry['cancel_btn'] = cancel_btn

        elif status == 'completed':
            check_label = Label(
                entry['status_frame'],
                text="✅",
                bg=theme['bg'],
                fg=theme['fg']
            )
            check_label.pack(side="left")
            entry['check_label'] = check_label

            delete_btn = Button(
                entry['status_frame'],
                text="❌",
                command=lambda v=version: self.delete_version(v),
                bg=theme['button_bg'],
                fg=theme['fg'],
                bd=0,
                relief='flat',
                padx=5,
                pady=2
            )
            delete_btn.pack(side="left")
            entry['delete_btn'] = delete_btn

        elif status == 'error':
            Label(
                entry['status_frame'],
                text="❌",
                bg=theme['bg'],
                fg=theme['fg']
            ).pack(side="left")

            retry_btn = Button(
                entry['status_frame'],
                text="↻",
                command=lambda v=version: self.download_and_install(v, {}),
                bg=theme['button_bg'],
                fg=theme['fg'],
                bd=0,
                relief='flat',
                padx=5,
                pady=2
            )
            retry_btn.pack(side="left")
            entry['retry_btn'] = retry_btn

    def cancel_download(self, version: str):
        if version not in self.download_status:
            return

        if self.download_status[version]['status'] in ['downloading', 'extracting']:
            self.download_status[version]['status'] = 'cancelled'
            self._update_status(version, 'pending')

        elif self.download_status[version]['status'] == 'queued':
            self.download_queue = [v for v in self.download_queue if v != version]
            self._update_status(version, 'pending')

    def delete_version(self, version: str):
        if not _gui_yes_no(f"Are you sure you want to delete {self.config['name']} {version}?"):
            return

        try:
            version_dir = self._get_version_dir(version)
            if version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
                log_manager.log_message(f"🗑️ Deleted version directory: {version_dir}")

            linux_dl = DOWNLOADS_FOLDER / "linux" / version
            windows_dl = DOWNLOADS_FOLDER / "windows" / version
            for d in [linux_dl, windows_dl]:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
                    log_manager.log_message(f"🗑️ Deleted download folder: {d}")

            if "tools_dir" in self.config:
                tools_dir = self.config["tools_dir"] / version
                if tools_dir.exists():
                    shutil.rmtree(tools_dir, ignore_errors=True)
                    log_manager.log_message(f"🗑️ Deleted tools folder: {tools_dir}")

            if self.target == "steamless":
                tracking_file = APP_FOLDER / ".steamless_versions.json"
                if tracking_file.exists():
                    try:
                        with open(tracking_file, 'r', encoding='utf-8') as f:
                            tracking_data = json.load(f)
                        if version in tracking_data:
                            del tracking_data[version]
                            with open(tracking_file, 'w', encoding='utf-8') as f:
                                json.dump(tracking_data, f, indent=2)
                            log_manager.log_message(f"🗑️ Removed {version} from Steamless tracking")
                    except Exception as e:
                        print(f"⚠️ Error updating Steamless tracking: {e}")

                mark_version_installed(self.target, version, False)

            elif self.target in ["gbe", "gse"]:
                emu = self.target
                tracking_file = APP_FOLDER / emu / f".{emu}_{version}.json"
                if tracking_file.exists():
                    tracking_file.unlink()
                    log_manager.log_message(f"🗑️ Deleted tracking file: {tracking_file}")
                mark_version_installed(self.target, version, False)

            if version in self.download_status:
                entry = self.download_status[version]
                entry['status'] = 'pending'

                if entry.get('status_frame') and entry['status_frame'].winfo_exists():
                    for widget in entry['status_frame'].winfo_children():
                        widget.destroy()

                    theme = self.ui.DARK_THEME if self.ui.dark_mode else self.ui.LIGHT_THEME
                    download_btn = Button(
                        entry['status_frame'],
                        text="⬇️",
                        command=lambda v=version: self.download_and_install(v, {}),
                        bg=theme['button_bg'],
                        fg=theme['fg'],
                        bd=0,
                        relief='flat',
                        padx=5,
                        pady=2
                    )
                    download_btn.pack(side="left")
                    entry['download_btn'] = download_btn
                    entry['delete_btn'] = None
                    entry['check_label'] = None

        except Exception as e:
            print(f"❌ Error deleting {version}: {e}")

    def add_version_to_ui(self, version: str, release_data: dict, parent_frame: Frame, theme: dict):
        version_frame = Frame(parent_frame, bg=theme['bg'])
        version_frame.pack(fill="x", pady=2, padx=5)

        version_label = Label(
            version_frame,
            text=f"{release_data.get('name', version)} ({version})",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11),
            anchor="w"
        )
        version_label.pack(side="left", fill="x", expand=True)

        status_frame = Frame(version_frame, bg=theme['bg'])
        status_frame.pack(side="right")

        is_installed = self.is_installed(version)
        status = 'completed' if is_installed else 'pending'

        download_btn = None
        if not is_installed:
            download_btn = Button(
                status_frame,
                text="⬇️",
                command=lambda v=version: self.download_and_install(v, release_data),
                bg=theme['button_bg'],
                fg=theme['fg'],
                bd=0,
                relief='flat',
                padx=5,
                pady=2
            )
            download_btn.pack(side="left")

        elif is_installed:
            check_label = Label(
                status_frame,
                text="✅",
                bg=theme['bg'],
                fg=theme['fg']
            )
            check_label.pack(side="left")

            delete_btn = Button(
                status_frame,
                text="❌",
                command=lambda v=version: self.delete_version(v),
                bg=theme['button_bg'],
                fg=theme['fg'],
                bd=0,
                relief='flat',
                padx=5,
                pady=2
            )
            delete_btn.pack(side="left")

        entry = {
            'status': status,
            'version': version,
            'frame': version_frame,
            'version_label': version_label,
            'status_frame': status_frame,
            'download_btn': download_btn,
            'check_label': check_label if is_installed else None,
            'delete_btn': delete_btn if is_installed else None,
        }

        self.download_status[version] = entry
        return entry

# ------------------------------------------------------------
class MenuManager:
    def __init__(self, ui):
        self.ui = ui
        self.current_menu = None
        self.menu_frames = {}
        self.menu_buttons = {}
        self.menu_icons = {
            'settings': {'open': '⚙️', 'closed': '❌'},
            'logs': {'open': '📜', 'closed': '❌'}
        }

        self.open_menus = set()

    def register_menu(self, menu_name: str, frame, button=None):
        self.menu_frames[menu_name] = frame
        if button:
            self.menu_buttons[menu_name] = button

    def is_menu_open(self, menu_name: str) -> bool:
        return menu_name in self.open_menus

    def get_open_menu(self) -> str | None:
        return self.current_menu

    def close_all_menus(self):
        for menu_name in list(self.open_menus):
            self._close_menu(menu_name)
        self.open_menus.clear()
        self.current_menu = None
        self._update_button_icons()

    def _close_menu(self, menu_name: str):
        frame = self.menu_frames.get(menu_name)
        if frame and frame.winfo_exists():
            frame.pack_forget()

        if menu_name in self.open_menus:
            self.open_menus.discard(menu_name)
        if self.current_menu == menu_name:
            self.current_menu = None

    def close_current_menu(self):
        if self.current_menu:
            self._close_menu(self.current_menu)
            self.current_menu = None
            self._update_button_icons()

    def toggle_menu(self, menu_name: str):
        if self.current_menu == menu_name:
            self.close_current_menu()
            return

        self.close_all_menus()

        frame = self.menu_frames.get(menu_name)

        if menu_name == 'logs':
            if hasattr(self.ui, 'show_log_viewer'):
                self.ui.show_log_viewer()
            self.current_menu = menu_name
            self.open_menus.add(menu_name)
            self._update_button_icons()

        elif frame and not frame.winfo_ismapped():
            if menu_name == 'settings':
                frame.pack(fill="both", expand=True)
                if hasattr(self.ui, 'populate_settings'):
                    self.ui.populate_settings()
            elif menu_name == 'attention':
                frame.pack(fill="x", side="bottom", ipady=10)
                if hasattr(self.ui, 'attention_visible'):
                    self.ui.attention_visible = True

            self.current_menu = menu_name
            self.open_menus.add(menu_name)
            self._update_button_icons()

    def _update_button_icons(self):
        for menu_name, button in self.menu_buttons.items():
            if button and button.winfo_exists():
                if menu_name in self.open_menus:
                    button.config(text=self.menu_icons[menu_name]['closed'])
                else:
                    button.config(text=self.menu_icons[menu_name]['open'])

    def show_menu(self, menu_name: str):
        self.close_all_menus()
        frame = self.menu_frames.get(menu_name)

        if menu_name == 'logs':
            if hasattr(self.ui, 'show_log_viewer'):
                self.ui.show_log_viewer()
            self.current_menu = menu_name
            self.open_menus.add(menu_name)

        elif frame:
            if menu_name == 'settings':
                frame.pack(fill="both", expand=True)
                if hasattr(self.ui, 'populate_settings'):
                    self.ui.populate_settings()
            elif menu_name == 'attention':
                frame.pack(fill="x", side="bottom", ipady=10)
                if hasattr(self.ui, 'attention_visible'):
                    self.ui.attention_visible = True

            self.current_menu = menu_name
            self.open_menus.add(menu_name)

        self._update_button_icons()

# ------------------------------------------------------------
class WatcherUI(tk.Tk):
    DARK_THEME = {
        'bg': '#2d2d2d',
        'fg': '#cdcdcd',
        'widget_bg': '#404040',
        'widget_fg': '#ffffff',
        'hover_bg': '#505050',
        'active_bg': '#606060',
        'border': '#606060',
        'button_bg': '#404040',
        'progress': 'darkred'
    }

    LIGHT_THEME = {
        'bg': '#ffffff',
        'fg': '#000000',
        'widget_bg': '#f0f0f0',
        'widget_fg': '#000000',
        'hover_bg': '#e0e0e0',
        'active_bg': '#d0d0d0',
        'border': '#c0c0c0',
        'button_bg': '#f0f0f0',
        'progress': 'lightgreen'
    }

    def apply_custom_theme(self, theme_data: dict):
        self.custom_theme = theme_data
        self._apply_theme()

    def _apply_theme(self):
        # Determine which theme to use
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        # Update theme button text
        if hasattr(self, "custom_theme") and self.custom_theme:
            self.theme_btn.config(text="🎨")
        else:
            self.theme_btn.config(text='🌞' if self.dark_mode else '🌚')

        # Apply theme to all UI elements
        self.top_bar.config(bg=theme['bg'])
        self.control_bar.config(bg=theme['bg'])
        self.right_button_frame.config(bg=theme['bg'])
        self.search_entry.config(bg=theme['widget_bg'], fg=theme['fg'], insertbackground=theme['fg'])
        self.search_btn.config(bg=theme['button_bg'], fg=theme['fg'])
        self.mass_close_btn.config(bg=theme['button_bg'], fg=theme['fg'])
        self.settings_btn.config(bg=theme['button_bg'], fg=theme['fg'])
        self.log_btn.config(bg=theme['button_bg'], fg=theme['fg'])

        self.style.configure('TNotebook', background=theme['bg'])
        self.style.configure('TNotebook.Tab', background=theme['widget_bg'], foreground=theme['fg'], lightcolor=theme['border'], borderwidth=0)
        self.style.map('TNotebook.Tab', background=[('selected', theme['widget_bg'])], foreground=[('selected', theme['fg'])])
        self.style.configure('TCombobox', fieldbackground=theme['widget_bg'], background=theme['widget_bg'], foreground=theme['fg'])
        self.style.map('TCombobox', fieldbackground=[('readonly', theme['widget_bg'])], selectbackground=[('readonly', theme['widget_bg'])], selectforeground=[('readonly', theme['fg'])], arrowcolor=[('readonly', theme['fg'])])
        self.search_mode_dropdown.config(background=theme['widget_bg'], foreground=theme['fg'])

        for target, manager in self.download_managers.items():
            for version, entry in manager.download_status.items():
                if 'status_frame' in entry and entry['status_frame'].winfo_exists():
                    for widget in entry['status_frame'].winfo_children():
                        if isinstance(widget, Button):
                            widget.config(bg=theme['button_bg'], fg=theme['fg'])
                        elif isinstance(widget, Label):
                            widget.config(bg=theme['bg'], fg=theme['fg'])

        def update_widget_colors(widget):
            try:
                if isinstance(widget, (Frame, Canvas)):
                    widget.config(bg=theme['bg'])
                elif isinstance(widget, Label):
                    widget.config(bg=theme['bg'], fg=theme['fg'])
                elif isinstance(widget, Entry):
                    widget.config(bg=theme['widget_bg'], fg=theme['fg'], insertbackground=theme['fg'])
                elif isinstance(widget, Checkbutton):
                    widget.config(bg=theme['bg'], fg=theme['fg'], activebackground=theme['bg'], activeforeground=theme['fg'], selectcolor=theme['widget_bg'])
                elif isinstance(widget, ttk.Combobox):
                    widget.config(style='TCombobox')
            except Exception:
                pass

            for child in widget.winfo_children():
                update_widget_colors(child)

        if self.settings_frame.winfo_ismapped():
            update_widget_colors(self.settings_frame)
            self.settings_frame.config(bg=theme['bg'])
            for tab in [self.general_tab, self.user_tab]:
                tab.config(bg=theme['bg'])
                update_widget_colors(tab)

        self.configure(bg=theme['bg'])
        self.counter_label.config(bg=theme['bg'], fg=theme['fg'])
        self.list_frame.config(bg=theme['bg'])
        self.canvas.config(bg=theme['bg'])
        self.inner_frame.config(bg=theme['bg'])
        self.scrollbar.config(bg=theme['widget_bg'], troughcolor=theme['bg'])

        for btn in [self.mass_close_btn, self.settings_btn, self.theme_btn]:
            btn.config(bg=theme['button_bg'], fg=theme['fg'], activebackground=theme['active_bg'])

        self.style.configure(f'{theme["progress"]}.Horizontal.TProgressbar', background=theme['progress'], troughcolor=theme['widget_bg'])

        for widgets in self._row_widgets.values():
            if widgets['frame'].winfo_exists():
                widgets['frame'].config(bg=theme['widget_bg'], highlightbackground=theme['border'])
                widgets['top_frame'].config(bg=theme['widget_bg'])
                widgets['bottom_frame'].config(bg=theme['widget_bg'])
                widgets['percent'].config(bg=theme['widget_bg'], fg=theme['fg'])
                widgets['name_label'].config(bg=theme['widget_bg'], fg=theme['fg'])
                widgets['path_label'].config(bg=theme['button_bg'], fg=theme['fg'])
                widgets['attention_btn'].config(bg=theme['button_bg'], fg=theme['fg'])
                widgets['close_btn'].config(bg=theme['button_bg'], fg=theme['fg'])
                widgets['progress'].configure(style=f'{theme["progress"]}.Horizontal.TProgressbar')

    def toggle_theme(self):
        # Check if custom themes are enabled and we have a valid theme
        custom_themes_enabled = GENERAL_SETTINGS.get("custom_themes_enabled", False)
        selected_theme_name = validate_selected_ctheme()
        custom_theme_exists = selected_theme_name is not None

        can_use_custom = custom_themes_enabled and custom_theme_exists

        # Determine current theme state
        if hasattr(self, "custom_theme") and self.custom_theme:
            current_theme = "custom"
        else:
            current_theme = "dark" if self.dark_mode else "light"

        # Determine next theme in cycle
        if can_use_custom:
            # Cycle: dark -> light -> custom -> dark
            if current_theme == "dark":
                next_theme = "light"
            elif current_theme == "light":
                next_theme = "custom"
            else:  # current_theme == "custom"
                next_theme = "dark"
        else:
            # Only dark/light available
            next_theme = "light" if current_theme == "dark" else "dark"

        # Apply the next theme
        if next_theme == "custom":
            custom_themes = load_custom_themes()
            self.custom_theme = custom_themes[selected_theme_name]
        elif next_theme == "dark":
            self.custom_theme = None
            self.dark_mode = True
        else:  # light
            self.custom_theme = None
            self.dark_mode = False

        self._apply_theme()

    def _perform_search(self):
        query = self.search_entry.get().lower()
        log_manager.log_message(f"Searching for: {query}")

    def _toggle_auto_update(self):
        self.general_settings.set("auto_update", self.auto_update_var.get())
        self._update_manual_btn_visibility()

    def _update_manual_btn_visibility(self):
        auto_update = self.general_settings.get("auto_update", True)
        self.manual_update_btn.config(state=tk.DISABLED if auto_update else tk.NORMAL)
        self.downgrade_btn.config(state=tk.NORMAL if not auto_update else tk.DISABLED)

    def _delete_api_key(self):
        self.steam_api_key_var.set("")

        GENERAL_SETTINGS._raw_api_key = None
        GENERAL_SETTINGS.settings["steam_api_key"] = ""
        GENERAL_SETTINGS.save()

        try:
            if SAPI_FILE.exists():
                SAPI_FILE.unlink()
                log_manager.log_message("🗑️ Deleted encrypted API key file")
        except Exception as e:
            print(f"⚠️ Error deleting API key file: {e}")

        try:
            if DECKEY_FILE.exists():
                DECKEY_FILE.unlink()
                log_manager.log_message("🗑️ Deleted encryption key file")
        except Exception as e:
            print(f"⚠️ Error deleting encryption key file: {e}")

    def _save_retention_days(self, *args):
        try:
            value = int(self.retention_days_var.get() or 1)
            log_settings = self.general_settings.get("log_settings", {})
            if not isinstance(log_settings, dict):
                log_settings = {}

            log_settings["retention_days"] = value
            self.general_settings.set("log_settings", log_settings)
        except ValueError:
            self.retention_days_var.set("1")
            log_settings = self.general_settings.get("log_settings", {})
            if not isinstance(log_settings, dict):
                log_settings = {}
            log_settings["retention_days"] = 1
            self.general_settings.set("log_settings", log_settings)

    def _populate_download_tab(self, target: str, parent_frame: Frame, theme: dict):
        for widget in parent_frame.winfo_children():
            widget.destroy()

        canvas_frame = Frame(parent_frame, bg=theme['bg'])
        canvas_frame.pack(fill="both", expand=True)
        container_canvas = Canvas(canvas_frame, bg=theme['bg'], borderwidth=0, highlightthickness=0)
        scrollbar = Scrollbar(canvas_frame, orient="vertical", command=container_canvas.yview)
        container_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        container_canvas.pack(side="left", fill="both", expand=True)
        container = Frame(container_canvas, bg=theme['bg'])
        window_id = container_canvas.create_window((0, 0), window=container, anchor="nw")

        def _configure_container(event):
            container_canvas.configure(scrollregion=container_canvas.bbox("all"))

        def _configure_canvas(event):
            if window_id:
                container_canvas.itemconfig(window_id, width=event.width)

        container.bind("<Configure>", _configure_container)
        container_canvas.bind("<Configure>", _configure_canvas)

        title = Label(
            container,
            text=f"{self.download_managers[target].config['name']} Versions",
            font=("Helvetica", 14, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        info_label = Label(
            container,
            text=f"Download and manage {self.download_managers[target].config['name']} versions",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 10)
        )
        info_label.pack(pady=(0, 10))

        releases_frame = Frame(container, bg=theme['bg'])
        releases_frame.pack(fill="both", expand=True, padx=10, pady=5)

        loading_label = Label(
            releases_frame,
            text="Loading releases...",
            bg=theme['bg'],
            fg=theme['fg']
        )
        loading_label.pack(pady=20)

        self.download_managers[target].fetch_releases(
            callback=lambda releases: self._populate_download_versions(target, releases, releases_frame, theme)
        )

    def _populate_download_versions(self, target: str, releases: list, parent_frame: Frame, theme: dict):
        for widget in parent_frame.winfo_children():
            widget.destroy()

        manager = self.download_managers[target]
        config = manager.config

        for release in releases:
            version = release.get('tag_name', '')
            if not version:
                continue

            if target in ["gbe", "gse"]:
                has_assets = False
                for asset in release.get('assets', []):
                    if any(re.search(p, asset['name'], re.I) for p in config["asset_patterns"]):
                        has_assets = True
                        break
                if not has_assets:
                    continue

            manager.add_version_to_ui(version, release, parent_frame, theme)

        self.dlm_releases_loaded[target] = True

    def _on_download_tab_selected(self, event=None):
        if not hasattr(self, 'settings_frame') or not self.settings_frame.winfo_ismapped():
            return

        tablist = None
        for widget in self.settings_frame.winfo_children():
            if isinstance(widget, ttk.Notebook):
                tablist = widget
                break
        if not tablist:
            return

        try:
            current_tab_index = tablist.index("current")
            current_tab_text = tablist.tab(current_tab_index, "text")
        except:
            return

        tab_info = {
            "GBE Config": ("gbe", self.gbe_tab),
            "GSE Config": ("gse", self.gse_tab),
            "Steamless Config": ("steamless", self.steamless_tab)
        }

        info = tab_info.get(current_tab_text)
        if not info:
            return

        target, tab_frame = info
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        for w in tab_frame.winfo_children():
            w.destroy()

        self._populate_download_tab(target, tab_frame, theme)

# ------------------------------------------------------------
    def populate_settings(self):
        for widget in self.settings_frame.winfo_children():
            widget.destroy()
        
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        self.settings_frame.config(bg=theme['bg'])
        
        title = Label(
            self.settings_frame, 
            text="Settings Menu",
            font=("Helvetica", 16, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        tablist = ttk.Notebook(self.settings_frame)
        tablist.pack(fill="both", expand=True, padx=10, pady=10)

        self.general_tab = Frame(tablist, bg=theme['bg'])
        tablist.add(self.general_tab, text="General Config")

        container_canvas = Canvas(self.general_tab, bg=theme['bg'], borderwidth=0, highlightthickness=0)
        scrollbar = Scrollbar(self.general_tab, orient="vertical", command=container_canvas.yview)
        container_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 2))
        container_canvas.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        general_container = Frame(container_canvas, bg=theme['bg'])
        canvas_window = container_canvas.create_window((0, 0), window=general_container, anchor="nw")

        def _configure_container(event):
            container_canvas.configure(scrollregion=container_canvas.bbox("all"))

        general_container.bind("<Configure>", _configure_container)

        def _configure_canvas(event):
            canvas_width = event.width
            container_canvas.itemconfig(canvas_window, width=canvas_width)

        container_canvas.bind("<Configure>", _configure_canvas)

#---------------------------------------------------------------------------------------------------------------------------
        prompt_label = Label(
            general_container,
            text="Update Settings",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 12, "bold")
        )
        prompt_label.pack(pady=(10, 5))

        separator = Frame(general_container, height=2, bg=theme['border'])
        separator.pack(fill="x", pady=(0, 10))

        update_frame = Frame(general_container, bg=theme['bg'])
        update_frame.pack(fill="x", pady=5)
        
        self.auto_update_var = tk.BooleanVar(value=self.general_settings.get("auto_update", True))
        Checkbutton(
            update_frame,
            text="Automatic Update Check",
            variable=self.auto_update_var,
            command=lambda: self._toggle_auto_update(target='app'),
            bg=theme['bg'],
            fg=theme['fg'],
            activebackground=theme['bg'],
            activeforeground=theme['fg'],
            selectcolor=theme['widget_bg']
        ).pack(side="left", padx=5)
        
        self.manual_update_btn = Button(
            update_frame,
            text="Manual Update",
            command=lambda: threading.Thread(target=check_for_updates, args=(True, 'app'), daemon=True).start(),
            bg=theme['button_bg'],
            fg=theme['fg'],
            state=tk.NORMAL if not self.auto_update_var.get() else tk.DISABLED
        )
        self.manual_update_btn.pack(side="right", padx=5)
        
        self.downgrade_btn = Button(
            update_frame,
            text="Downgrade",
            command=lambda: self.downgrader("app"),
            bg=theme['button_bg'],
            fg=theme['fg'],
            state=tk.NORMAL if not self.auto_update_var.get() else tk.DISABLED
        )
        self.downgrade_btn.pack(side="right", padx=5)

#---------------------------------------------------------------------------------------------------------------------------
        prompt_label = Label(
            general_container,
            text="Prompt Settings",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 12, "bold")
        )
        prompt_label.pack(pady=(10, 5))

        separator = Frame(general_container, height=2, bg=theme['border'])
        separator.pack(fill="x", pady=(0, 10))

        mp_frame = Frame(general_container, bg=theme['bg'])
        mp_frame.pack(fill="x", pady=5)

        Label(
            mp_frame,
            text="Multiplayer Achievements",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        ).pack(side="left", padx=5)

        mp_radio_frame = Frame(mp_frame, bg=theme['bg'])
        mp_radio_frame.pack(side="right", padx=5)

        self.mp_prompt_var = tk.StringVar(value=self.general_settings.get("mp_prompt", "Ask"))
        tk.Radiobutton(
            mp_radio_frame,
            text="Ask",
            variable=self.mp_prompt_var,
            value="Ask",
            bg=theme['bg'],
            fg=theme['fg'],
            selectcolor=theme['widget_bg'],
            command=lambda: self.general_settings.set("mp_prompt", self.mp_prompt_var.get())
        ).pack(side="left", padx=(0, 10))
        tk.Radiobutton(
            mp_radio_frame,
            text="Yes",
            variable=self.mp_prompt_var,
            value="Yes",
            bg=theme['bg'],
            fg=theme['fg'],
            selectcolor=theme['widget_bg'],
            command=lambda: self.general_settings.set("mp_prompt", self.mp_prompt_var.get())
        ).pack(side="left", padx=(0, 10))
        tk.Radiobutton(
            mp_radio_frame,
            text="No",
            variable=self.mp_prompt_var,
            value="No",
            bg=theme['bg'],
            fg=theme['fg'],
            selectcolor=theme['widget_bg'],
            command=lambda: self.general_settings.set("mp_prompt", self.mp_prompt_var.get())
        ).pack(side="left")

        hidden_frame = Frame(general_container, bg=theme['bg'])
        hidden_frame.pack(fill="x", pady=5)

        Label(
            hidden_frame,
            text="Hidden Achievements",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        ).pack(side="left", padx=5)

        hidden_radio_frame = Frame(hidden_frame, bg=theme['bg'])
        hidden_radio_frame.pack(side="right", padx=5)

        self.hidden_prompt_var = tk.StringVar(value=self.general_settings.get("hidden_prompt", "Ask"))
        tk.Radiobutton(
            hidden_radio_frame,
            text="Ask",
            variable=self.hidden_prompt_var,
            value="Ask",
            bg=theme['bg'],
            fg=theme['fg'],
            selectcolor=theme['widget_bg'],
            command=lambda: self.general_settings.set("hidden_prompt", self.hidden_prompt_var.get())
        ).pack(side="left", padx=(0, 10))
        tk.Radiobutton(
            hidden_radio_frame,
            text="Yes",
            variable=self.hidden_prompt_var,
            value="Yes",
            bg=theme['bg'],
            fg=theme['fg'],
            selectcolor=theme['widget_bg'],
            command=lambda: self.general_settings.set("hidden_prompt", self.hidden_prompt_var.get())
        ).pack(side="left", padx=(0, 10))
        tk.Radiobutton(
            hidden_radio_frame,
            text="No",
            variable=self.hidden_prompt_var,
            value="No",
            bg=theme['bg'],
            fg=theme['fg'],
            selectcolor=theme['widget_bg'],
            command=lambda: self.general_settings.set("hidden_prompt", self.hidden_prompt_var.get())
        ).pack(side="left")

#---------------------------------------------------------------------------------------------------------------------------
        search_label = Label(
            general_container,
            text="Search Settings",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 12, "bold")
        )
        search_label.pack(pady=(10, 5))

        separator = Frame(general_container, height=2, bg=theme['border'])
        separator.pack(fill="x", pady=(0, 10))

        language_frame = Frame(general_container, bg=theme['bg'])
        language_frame.pack(fill="x", pady=5)

        Label(
            language_frame,
            text="Language:",
            bg=theme['bg'],
            fg=theme['fg']
        ).pack(side="left", padx=5)

        self.search_language_var = tk.StringVar(value=GENERAL_SETTINGS.get("search_language", "english"))
        language_dropdown = ttk.Combobox(
            language_frame,
            textvariable=self.search_language_var,
            values=["arabic", "bulgarian", "schinese", "tchinese", "czech", "danish", "dutch", "english", "finnish", "french", "german", "greek", "hungarian", "italian", "japanese", "koreana", "norwegian", "polish", "portuguese", "brazilian", "romanian", "russian", "spanish", "latam", "swedish", "thai", "turkish", "ukrainian", "vietnamese"],
            state="readonly",
            width=12
        )
        language_dropdown.pack(side="left", padx=5)
        language_dropdown.bind("<<ComboboxSelected>>", lambda e: (
            GENERAL_SETTINGS.set("search_language", self.search_language_var.get()),
            log_manager.log_message(f"Search language set to: {self.search_language_var.get()}")
        ))

        api_key_frame = Frame(general_container, bg=theme['bg'])
        api_key_frame.pack(fill="x", pady=5)

        Label(
            api_key_frame,
            text="Steam API Key:",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        ).pack(side="left", padx=5)
        
        raw_api_key = GENERAL_SETTINGS.get("steam_api_key", "")
        self.steam_api_key_var = tk.StringVar(value=raw_api_key if raw_api_key else " ")
        self.steam_api_key_entry = Entry(
            api_key_frame,
            textvariable=self.steam_api_key_var,
            width=40,
            bg=theme['widget_bg'],
            fg=theme['fg'],
            show="*"
        )
        self.steam_api_key_entry.pack(side="left", fill="x", expand=True)
        self.mask_timer = None

        def reset_mask_timer():
            if self.mask_timer:
                self.after_cancel(self.mask_timer)
            self.mask_timer = self.after(8000, lambda: self.steam_api_key_entry.config(show="*"))

        def on_key_release(event):
            GENERAL_SETTINGS._raw_api_key = self.steam_api_key_var.get()
            log_manager.save_encrypted_api_key(self.steam_api_key_var.get())
            reset_mask_timer()

        def on_focus_in(event):
            self.steam_api_key_entry.config(show="")
            reset_mask_timer()

        def on_focus_out(event):
            self.steam_api_key_entry.config(show="*")
            GENERAL_SETTINGS._raw_api_key = self.steam_api_key_var.get()
            log_manager.save_encrypted_api_key(self.steam_api_key_var.get())

        self.steam_api_key_entry.bind("<KeyRelease>", on_key_release)
        self.steam_api_key_entry.bind("<FocusIn>", on_focus_in)
        self.steam_api_key_entry.bind("<FocusOut>", on_focus_out)
        self.steam_api_key_entry.bind("<Leave>", on_focus_out)

        info_label = Label(
            general_container,
            text="Get API key from: https://steamcommunity.com/dev/apikey",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 10, "italic")
        )
        info_label.pack(fill="x", pady=(0, 10))

        self.steam_apik_del = Button(
            api_key_frame,
            text="Remove API Key",
            command=self._delete_api_key,
            bg=theme['button_bg'],
            fg=theme['fg']
        )
        self.steam_apik_del.pack(side="right", padx=5)

#---------------------------------------------------------------------------------------------------------------------------
        self.retention_days_var = tk.StringVar(value=str(GENERAL_SETTINGS.get("log_settings", {}).get("retention_days", 1)))
        self.retention_days_var.trace_add("write", self._save_retention_days)
        log_settings_label = Label(
            general_container,
            text="Log Settings",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 12, "bold")
        )
        log_settings_label.pack(pady=(10, 5))

        separator = Frame(general_container, height=2, bg=theme['border'])
        separator.pack(fill="x", pady=(0, 10))

        retention_frame = Frame(general_container, bg=theme['bg'])
        retention_frame.pack(fill="x", pady=5)

        retention_label = Label(
            retention_frame,
            text="Retention Days:",
            bg=theme['bg'],
            fg=theme['fg']
        )
        retention_label.pack(side="left", padx=5)

        retention_entry = tk.Entry(
            retention_frame,
            width=5,
            textvariable=self.retention_days_var,
            bg=theme['widget_bg'],
            fg=theme['fg']
        )
        retention_entry.pack(side="right", padx=5)

#---------------------------------------------------------------------------------------------------------------------------
        current_theme = self.custom_theme if (hasattr(self, "custom_theme") and self.custom_theme) else (self.DARK_THEME if self.dark_mode else self.LIGHT_THEME)
        self.theme_config_frame = Frame(tablist, bg=current_theme['bg'])
        self.theme_config_tab = ThemeConfigTab(self.theme_config_frame, current_theme, self)
        tablist.add(self.theme_config_frame, text="Theme Config")

#---------------------------------------------------------------------------------------------------------------------------
        self.gbe_tab = Frame(tablist, bg=theme['bg'])
        tablist.add(self.gbe_tab, text="GBE Config")
        self.gse_tab = Frame(tablist, bg=theme['bg'])
        tablist.add(self.gse_tab, text="GSE Config")
        self.steamless_tab = Frame(tablist, bg=theme['bg'])
        tablist.add(self.steamless_tab, text="Steamless Config")
        tablist.bind("<<NotebookTabChanged>>", self._on_download_tab_selected)

#---------------------------------------------------------------------------------------------------------------------------
        self.user_tab = Frame(tablist, bg=theme['bg'])
        tablist.add(self.user_tab, text="User Config")

        tab_separator = Frame(self.user_tab, height=2, bg=theme['border'])
        tab_separator.pack(fill="x", pady=(0, 10))

        settings_container = Frame(self.user_tab, bg=theme['bg'])
        settings_container.pack(pady=10, padx=20, fill="x")

        enable_frame = Frame(settings_container, bg=theme['bg'])
        enable_frame.pack(fill="x", pady=5)
        self.enable_var = tk.BooleanVar(value=self.user_config.get("enabled", False))
        Checkbutton(
            enable_frame,
            text="Enable User Config",
            variable=self.enable_var,
            command=self._toggle_config_fields,
            bg=theme['bg'],
            fg=theme['fg'],
            activebackground=theme['bg'],
            activeforeground=theme['fg'],
            selectcolor=theme['widget_bg']
        ).pack(anchor="w")

        self.fields_frame = Frame(settings_container, bg=theme['bg'])
        self.fields_frame.pack(fill="x", pady=10)

        account_frame = Frame(self.fields_frame, bg=theme['bg'])
        account_frame.pack(fill="x", pady=5)
        Label(account_frame, text="Account Name:", bg=theme['bg'], fg=theme['fg'], width=12, anchor="e").pack(side="left", padx=(0, 10))
        self.account_var = tk.StringVar(value=self.user_config.get("account_name"))
        self.account_entry = Entry(
            account_frame, 
            textvariable=self.account_var,
            width=30,
            bg=theme['widget_bg'],
            fg=theme['fg']
        )
        self.account_entry.pack(side="left", fill="x", expand=True)
        self.account_entry.bind("<KeyRelease>", lambda e: self._save_config("account_name", self.account_var.get()))

        steamid_frame = Frame(self.fields_frame, bg=theme['bg'])
        steamid_frame.pack(fill="x", pady=5)
        Label(steamid_frame, text="SteamID:", bg=theme['bg'], fg=theme['fg'], width=12, anchor="e").pack(side="left", padx=(0, 10))
        current_steamid = self.user_config.get("steamid")

        if not current_steamid:
            current_steamid = "76561197960287930"
            self.user_config.set("steamid", current_steamid)
            
        self.steamid_var = tk.StringVar(value=current_steamid)
        self.steamid_entry = Entry(
            steamid_frame, 
            textvariable=self.steamid_var,
            width=30,
            bg=theme['widget_bg'],
            fg=theme['fg']
        )
        self.steamid_entry.pack(side="left", fill="x", expand=True)
        self.steamid_entry.bind("<KeyRelease>", lambda e: self._save_config("steamid", self.steamid_var.get()))

        lang_frame = Frame(self.fields_frame, bg=theme['bg'])
        lang_frame.pack(fill="x", pady=5)
        Label(lang_frame, text="Language:", bg=theme['bg'], fg=theme['fg'], width=12, anchor="e").pack(side="left", padx=(0, 10))
        self.lang_var = tk.StringVar(value=self.user_config.get("language"))
        lang_dropdown = ttk.Combobox(
            lang_frame,
            textvariable=self.lang_var,
            values=["English", "French", "German", "Spanish", "Russian", "Japanese", "Chinese", "Korean", "Portuguese"],
            state="readonly",
            width=27
        )
        lang_dropdown.pack(side="left")
        lang_dropdown.bind("<<ComboboxSelected>>", lambda e: self._save_config("language", self.lang_var.get()))

        country_frame = Frame(self.fields_frame, bg=theme['bg'])
        country_frame.pack(fill="x", pady=5)
        Label(country_frame, text="Country:", bg=theme['bg'], fg=theme['fg'], width=12, anchor="e").pack(side="left", padx=(0, 10))
        self.country_var = tk.StringVar(value=self.user_config.get("country"))
        country_dropdown = ttk.Combobox(
            country_frame,
            textvariable=self.country_var,
            values=["US", "UK", "DE", "FR", "RU", "JP", "KR", "CN", "TW", "IT", "ES", "PT"],
            state="readonly",
            width=5
        )
        country_dropdown.pack(side="left")
        country_dropdown.bind("<<ComboboxSelected>>", lambda e: self._save_config("country", self.country_var.get()))

        self._toggle_config_fields()
        self.settings_btn.lift()

    def downgrader(self, target: str = "app"):
        if target == "app":
            log_manager.save_update_check_time()

            try:
                current_version = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "v0.0"
            
                response = requests.get(RELEASE_URL, timeout=10)
                response.raise_for_status()
                all_releases = response.json()
            
                versions = []
                for release in all_releases:
                    try:
                        ver = release['tag_name'].lstrip('v').split('.')
                        ver_tuple = tuple(map(int, ver))
                        versions.append((ver_tuple, release))
                    except Exception:
                        continue
            
                versions.sort(reverse=True, key=lambda x: x[0])
            
                if not versions:
                    show_custom_dialog(self, "info", "Downgrade", "No valid versions found")
                    return

                current_tuple = tuple(map(int, current_version.lstrip('v').split('.')))
                latest_tuple = versions[0][0]
            
                current_index = next((i for i, (v, _) in enumerate(versions) if v == current_tuple), -1)

                if current_index == -1:
                    show_custom_dialog(self, "info", "Downgrade", f"Current version {current_version} not found in releases")
                    return

                if current_tuple < latest_tuple:
                    if _gui_yes_no(f"Your version {current_version} is outdated. Upgrade to {versions[0][1]['tag_name']} instead?"):
                        target_release = versions[0][1]
                    else:
                        if current_index + 1 >= len(versions):
                            show_custom_dialog(self, "info", "Downgrade", "No older versions available")
                            return
                        target_release = versions[current_index + 1][1]
                else:
                    if current_index + 1 >= len(versions):
                        show_custom_dialog(self, "info", "Downgrade", "No older versions available")
                        return
                    target_release = versions[current_index + 1][1]

                target_version = target_release['tag_name']
                if not _gui_yes_no(f"Install version {target_version}?"):
                    return

                zip_asset = next((a for a in target_release['assets'] if a['name'].endswith('.zip')), None)
                if not zip_asset:
                    raise Exception("No files found for older version")

                zip_path = DOWNLOADS_FOLDER / zip_asset['name']
                with requests.get(zip_asset['browser_download_url'], stream=True) as r:
                    with open(zip_path, 'wb') as f:
                        shutil.copyfileobj(r.raw, f)

                with zipfile.ZipFile(zip_path) as zip_ref:
                    temp_extract = ROOT_DIR / "update_temp"
                    temp_extract.mkdir(exist_ok=True)
                    zip_ref.extractall(temp_extract)                
                    extracted_folder = next(temp_extract.iterdir())
                    for item in extracted_folder.iterdir():
                        dest = ROOT_DIR / item.name
                        if dest.is_dir():
                            shutil.rmtree(dest, ignore_errors=True)
                        elif dest.exists():
                            dest.unlink()
                        shutil.move(str(item), str(dest))
                
                    shutil.rmtree(temp_extract, ignore_errors=True)

                ssg_path = ROOT_DIR / "SSG.py"
                if ssg_path.exists() and not sys.platform.startswith("win"):
                    os.chmod(ssg_path, 0o755)

                zip_path.unlink(missing_ok=True)
                VERSION_FILE.write_text(target_version, encoding="utf-8")
            
                if _gui_yes_no(f"Reverted to version {target_version}\n Restart now?"):
                    restart_application()

            except Exception as e:
                show_custom_dialog(self, "error", "Downgrade Failed", str(e))

    def _update_user_ini(self):
        ini_path = EXTRA_FOLDER / "configs.user.ini"
    
        if self.user_config.get("enabled", False):    
            lines = ["[user::general]"]
        
            if self.user_config.get("account_name"):
                lines.append(f"account_name={self.user_config.get('account_name')}")
            if self.user_config.get("steamid"):
                lines.append(f"account_steamid={self.user_config.get('steamid')}")
            language = self.user_config.get("language")
            if language:
                lines.append(f"language={language.lower()}")
            if self.user_config.get("country"):
                lines.append(f"ip_country={self.user_config.get('country')}")
        
            if len(lines) > 1:
                ini_path.write_text("\n".join(lines), encoding="utf-8")
            elif ini_path.exists():
                ini_path.unlink()
        else:
            if ini_path.exists():
                try:
                    ini_path.unlink()
                except Exception as e:
                    print(f"Error removing user config: {e}")

    def _toggle_config_fields(self):
        state = "normal" if self.enable_var.get() else "disabled"
        
        for child in self.fields_frame.winfo_children():
            for widget in child.winfo_children():
                if isinstance(widget, (Entry, ttk.Combobox)):
                    widget.configure(state=state)
        
        self.user_config.set("enabled", self.enable_var.get())
        self._update_user_ini()

    def _save_config(self, key, value):
        self.user_config.set(key, value)
        
        if key != "enabled" and value and not self.enable_var.get():
            self.enable_var.set(True)
            self._toggle_config_fields()

        if self.user_config.get("enabled", False):
            self._update_user_ini()

    def __init__(self, file_queue: queue.Queue):
        super().__init__()
        #------------------------------------
        # Set default theme
        default_theme = GENERAL_SETTINGS.get("default_theme", "dark")
        self.custom_theme = None
        self.dark_mode = True

        # Load custom theme if enabled and if it's the default
        if GENERAL_SETTINGS.get("custom_themes_enabled", False) and default_theme == "custom":
            selected_theme_name = validate_selected_ctheme()
            if selected_theme_name:
                custom_themes = load_custom_themes()
                self.custom_theme = custom_themes[selected_theme_name]
            else:
                # Custom theme not available, fall back to dark
                self.dark_mode = True
        elif default_theme == "light":
            self.dark_mode = False
        #------------------------------------
        self.menu_manager = MenuManager(self)
        self.selected_version = None
        self.steamless_releases_loaded = False
        self.steamless_window_id = None
        self.search_manager = SearchManager(self)
        self.search_manager.set_ui(self)
        self.search_language_var = tk.StringVar(value=GENERAL_SETTINGS.get("search_language", "english"))
        self.download_managers = {
            "steamless": DownloadManager("steamless", self),
            "gbe": DownloadManager("gbe", self),
            "gse": DownloadManager("gse", self),
        }
        self.dlm_releases_loaded = {
            "steamless": False,
            "gbe": False,
            "gse": False,
        }

        try:
            self.tk.eval('package require tkdnd')
            self.tkdnd_available = True
        except tk.TclError:
            self.tk.eval('namespace eval ::tkdnd {}')
            self.tk.eval('set ::tkdnd::initialized 1')
            self.tkdnd_available = False

        self.style = ttk.Style()
        self.style.theme_use('clam')

        self.style.configure(
            'darkred.Horizontal.TProgressbar',
            background=self.DARK_THEME['progress'],
            troughcolor=self.DARK_THEME['widget_bg']
        )
    
        self.style.configure(
            'lightgreen.Horizontal.TProgressbar',
            background=self.LIGHT_THEME['progress'],
            troughcolor=self.LIGHT_THEME['widget_bg']
        )

        self.title("SSG: Watching for HTML files")
        self.geometry("800x1000")
        self.resizable(False, False)

        self.top_bar = Frame(self)
        self.top_bar.pack(fill="x", padx=0, pady=(8, 5))

        self.search_mode_var = tk.StringVar(value="AppID")
        self.search_mode_dropdown = ttk.Combobox(
            self.top_bar,
            textvariable=self.search_mode_var,
            values=["AppID", "Local"],
            state="readonly",
            width=6,
            font=("Helvetica", 14)
        )
        self.search_mode_dropdown.pack(side="left", padx=(20, 2))
        self.search_mode_dropdown.bind("<<ComboboxSelected>>", self._on_search_mode_change)

        self.search_entry = Entry(
            self.top_bar,
            bg=self.LIGHT_THEME['widget_bg'],
            fg=self.LIGHT_THEME['fg'],
            insertbackground=self.LIGHT_THEME['fg'],
            bd=0
        )
        self.search_entry.pack(side="left", padx=(0, 0), pady=2, ipady=4, fill="x", expand=True)
        self.search_entry.bind("<Escape>", self._on_search_escape)
        self.search_entry.bind("<KeyRelease>", self._on_search_key_release)
        self.search_entry.bind("<Return>", self._on_search_enter)

        self.search_btn = Button(
            self.top_bar,
            text="🔍",
            command=self._perform_search,
            bg=self.LIGHT_THEME['button_bg'],
            fg=self.LIGHT_THEME['fg'],
            bd=0,
            relief='flat'
        )
        self.search_btn.pack(side="left", padx=(0, 30))

        self.filtered_html_files = []
        self.original_html_files = []

        self.control_bar = Frame(self)
        self.control_bar.pack(fill="x", padx=0, pady=(0, 10))

        self.mass_close_btn = Button(
            self.control_bar,
            text="🗳",
            font=('Arial', 8),
            command=self._confirm_remove_all,
            bd=0,
            relief='flat',
            bg=self.LIGHT_THEME['button_bg'],
            fg=self.LIGHT_THEME['fg']
        )
        self.mass_close_btn.pack(side="left", padx=(20, 0))

        self.counter_label = tk.Label(self.control_bar, text="Job Count: 0", font=("Helvetica", 12))
        self.counter_label.pack(side="left", expand=True)

        self.right_button_frame = Frame(self.control_bar)
        self.right_button_frame.pack(side="right", padx=(0, 20))

        self.theme_btn = Button(
            self.right_button_frame,
            text='🌞',
            font=('Arial', 8),
            command=self.toggle_theme,
            bd=0,
            relief='flat',
            bg=self.DARK_THEME['button_bg'],
            fg=self.DARK_THEME['fg']
        )
        self.theme_btn.pack(side="left", padx=(0, 10))

        self.log_btn = Button(
            self.right_button_frame,
            text="📜",
            font=('Arial', 8),
            command=lambda: self.menu_manager.toggle_menu('logs'),
            bd=0,
            relief='flat',
            bg=self.LIGHT_THEME['button_bg'],
            fg=self.LIGHT_THEME['fg']
        )
        self.log_btn.pack(side="left", padx=(0, 10))

        self.menu_manager.register_menu('logs', None, self.log_btn)

        self.settings_btn = Button(
            self.right_button_frame,
            text="⚙️",
            font=('Arial', 8),
            command=lambda: self.menu_manager.toggle_menu('settings'),
            bd=0,
            relief='flat',
            bg=self.LIGHT_THEME['button_bg'],
            fg=self.LIGHT_THEME['fg']
        )
        self.settings_btn.pack(side="left", padx=(0, 10))

        self.settings_frame = Frame(self, height=550)
        self.settings_frame.pack_propagate(False)

        self.menu_manager.register_menu('settings', self.settings_frame, self.settings_btn)

        self.list_frame = Frame(self)
        self.list_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.canvas = Canvas(self.list_frame, borderwidth=0, highlightthickness=0)
        self.scrollbar = Scrollbar(self.list_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.bind("<Button-4>", self._on_mousewheel)
        self.bind("<Button-5>", self._on_mousewheel)

        self.inner_frame = Frame(self.canvas)
        self._row_widgets: dict[Path, dict[str, ttk.Progressbar | Label]] = {}
        self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        self.attention_frame = Frame(self)
        self.attention_visible = False
        self.current_html_path = None

        self.menu_manager.register_menu('attention', self.attention_frame)

        self._update_mass_close_btn()

        self.after(300, self._refresh_counter)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._stop_requested = False

        self.inner_frame.grid_columnconfigure(0, weight=1)
        self.inner_frame.grid_rowconfigure(len(all_html_files), minsize=5)

        self.inner_frame.bind("<Configure>", self._update_scroll_region)

        self.user_settings = USER_SETTINGS
        self.general_settings = GENERAL_SETTINGS
        self.user_config = USER_SETTINGS

        self._init_game_config()

        self.stub_removal_selected_files = []
        self.stub_removal_options = {}
        self.tooltip_label = None

        self._apply_theme()

    # ------------------------------------------------------------------
    def _on_search_mode_change(self, event=None):
        self.search_entry.delete(0, tk.END)
        self.filtered_html_files = []
        self.refresh_file_list(all_html_files, file_status)

    def _on_search_key_release(self, event=None):
        if self.search_mode_var.get() != "Local":
            return

        search_text = self.search_entry.get().lower()
        self._filter_local_search(search_text)

    def _on_search_enter(self, event=None):
        if self.search_mode_var.get() == "AppID":
            self._perform_appid_search()

    def _on_search_escape(self, event=None):
        self.search_entry.delete(0, tk.END)
        self.filtered_html_files = []
        self.refresh_file_list(all_html_files, file_status)
        self.focus()

    def _perform_appid_search(self):
        appid = self.search_entry.get().strip()
        if appid:
            threading.Thread(target=self.search_manager.download_appid_html, args=(appid,), daemon=True).start()

    def _filter_local_search(self, search_text: str):
        filtered = self.search_manager.filter_local_search(search_text, all_html_files)

        self.filtered_html_files = filtered
        self.refresh_file_list(filtered, file_status)

    # ------------------------------------------------------------------
    def _refresh_counter(self):
        queued, active = job_tracker.snapshot()
        displayed = queued + active
        self.counter_label.config(text=f"Job Count: {displayed}")
        self.after(300, self._refresh_counter)

    # ------------------------------------------------------------------
    def start_job(self):
        self._busy = True

    def finish_job(self):
        self._busy = False

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _make_scrolling_label(self, parent, full_text, max_pixels):
        container = Frame(parent, width=max_pixels, height=20)
        container.pack_propagate(False)
        container.pack(side="left")  

        lbl = tk.Label(container, font=("Helvetica", 11, "bold"), anchor="w")
        lbl.full_text = full_text
        lbl.max_pixels = max_pixels
        lbl.current_offset = 0
        lbl.is_running = False

        test = tk.Label(container, font=("Helvetica", 11, "bold"))
        test.pack_forget()
        fit_len = 0
        for i in range(1, len(full_text) + 1):
            test.config(text=full_text[:i])
            test.update_idletasks()
            if test.winfo_reqwidth() > max_pixels:
                break
            fit_len = i
        test.destroy()

        lbl.config(text=full_text[:fit_len])
        lbl.pack(fill="both", expand=True)

        def _update_name_label():
            txt = lbl.full_text[lbl.current_offset:] + " " + lbl.full_text[:lbl.current_offset]
            lbl.config(text=txt)
            lbl.current_offset = (lbl.current_offset + 1) % len(lbl.full_text)
            if lbl.is_running:
                lbl.after_id = lbl.after(150, _update_name_label)

        def _start_name_label(event=None):
            if not lbl.is_running:
                lbl.is_running = True
                _update_name_label()

        def _stop_name_label(event=None):
            lbl.is_running = False
            if hasattr(lbl, "after_id"):
                lbl.after_cancel(lbl.after_id)
            lbl.config(text=lbl.full_text[:fit_len])

        lbl.bind("<Enter>", _start_name_label)
        lbl.bind("<Leave>", _stop_name_label)

        return lbl

    # ------------------------------------------------------------------
    def _on_mousewheel(self, event):
        if event.delta:
            scroll_amount = -1 * (event.delta // 120) if event.delta else 0
        else:
            scroll_amount = -1 if event.num == 4 else 1 if event.num == 5 else 0
            
        self.canvas.yview_scroll(scroll_amount, "units")

    def _update_scroll_region(self, event=None):
        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(scrollregion=(bbox[0], bbox[1], bbox[2], bbox[3] + 20))

    # ------------------------------------------------------------------
    def _update_mass_close_btn(self):
        num_items = len(self._row_widgets)
        if num_items < 2:
            self.mass_close_btn.config(state=tk.DISABLED)
        else:
            self.mass_close_btn.config(state=tk.NORMAL)

    # ------------------------------------------------------------------
    def refresh_file_list(self, html_files: list[Path], status_map: dict[Path, str]):
        if not self.winfo_exists() or self._stop_requested:
            return

        if self.search_mode_var.get() == "Local" and self.filtered_html_files:
            files_copy = self.filtered_html_files.copy()
        else:
            files_copy = html_files.copy()
        status_copy = status_map.copy()

        def _safe_refresh():
            if not self.winfo_exists() or self._stop_requested:
                return

            try:
                for widget in self.inner_frame.winfo_children():
                    try:
                        widget.destroy()
                    except tk.TclError:
                        continue
            
                self._row_widgets.clear()
            
                inset_pad = 20
                right_pad = 20
                row_width = 760 - inset_pad - right_pad
                title_max_px = 180

                current_progress_state = log_manager.load_progress_state()

                for idx, path in enumerate(files_copy):
                    try:

                        try:
                            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                            title = soup.find("h1", itemprop="name").get_text(strip=True)
                        except Exception:
                            title = path.stem
                            
                        game_dir = None
                        temp_file = TEMP_FOLDER / f"{path.name}.txt"
                        if temp_file.is_file():
                            try:
                                for line in temp_file.read_text().splitlines():
                                    if line.startswith("GAMEDIR="):
                                        game_dir = Path(line.split("=", 1)[1].strip())
                                        break
                            except Exception:
                                pass

                        if hasattr(self, "custom_theme") and self.custom_theme:
                            current_theme = self.custom_theme
                        else:
                            current_theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

                        game_folder_path = game_dir if game_dir else GAMES_ROOT / clean_title(title)
                        game_folder_pn = str(game_folder_path)

                        outer = Frame(self.inner_frame, bd=2, relief="groove", width=row_width, height=80, bg=current_theme['widget_bg'], highlightbackground=current_theme['border'])
                        outer.grid(row=idx, column=0, pady=8, padx=(inset_pad, right_pad))
                        outer.grid_propagate(False)

                        top = Frame(outer, bg=current_theme['widget_bg'])
                        top.pack(fill="x", padx=8, pady=4)

                        name_label = self._make_scrolling_label(top, title, title_max_px)
                        name_label.config(bg=current_theme['widget_bg'], fg=current_theme['fg'])
                        name_label.pack(side="left")

                        prog = ttk.Progressbar(top, orient="horizontal", length=380, mode="determinate", style=f'{current_theme["progress"]}.Horizontal.TProgressbar')
                        prog.pack(side="left", padx=12)

                        percent_lbl = Label(top, text="0%", width=4, bg=current_theme['widget_bg'], fg=current_theme['fg'])
                        percent_lbl.pack(side="left", padx=4)

                        attention_btn = Button(
                            top,
                            text="⚠️",
                            width=2,
                            bg=current_theme['button_bg'],
                            fg=current_theme['fg'],
                            command=lambda p=path: self._confirm_attention(p),
                        )
                        attention_btn.pack(side="left", padx=4)

                        close_btn = Button(
                            top,
                            text="🗑️",
                            width=2,
                            bg=current_theme['button_bg'],
                            fg=current_theme['fg'],
                            command=lambda p=path: self._confirm_remove(p),
                        )
                        close_btn.pack(side="left", padx=4)

                        bottom = Frame(outer, bg=current_theme['widget_bg'])
                        bottom.pack(fill="x", padx=8, pady=(0, 4))

                        path_lbl = Label(
                            bottom,
                            text=game_folder_pn,
                            fg=current_theme['fg'],
                            cursor="hand2",
                            font=("Helvetica", 12, "underline"),
                            bg=current_theme['button_bg'],
                            wraplength=700,
                            anchor="w"
                        )
                        path_lbl.pack(side="top", pady=1)
                        path_lbl.bind("<Button-1>", lambda e, p=game_folder_path: _open_folder(p))

                        self.progress_state = log_manager.load_progress_state()

                        saved = self.progress_state.get(path.name)
                        if saved:
                            percent = saved.get("percent", 0)
                            prog["maximum"] = 100
                            prog["value"] = percent
                            percent_lbl.config(text=f"{percent}%")
                
                        self._row_widgets[path] = {
                            "progress": prog,
                            "percent": percent_lbl,
                            "frame": outer,
                            "top_frame": top,
                            "bottom_frame": bottom,
                            "path_label": path_lbl,
                            "name_label": name_label,
                            "attention_btn": attention_btn,
                            "close_btn": close_btn,
                         }

                    except Exception as e:
                        print(f"Error creating widget for {path}: {e}")

                self.canvas.config(scrollregion=self.canvas.bbox("all"))
                self.update_idletasks()
            
            except Exception as e:
                print(f"Error rebuilding UI: {e}")

            self._update_scroll_region()

            self.after(100, self._update_mass_close_btn)

        self.after(0, _safe_refresh)

    # ------------------------------------------------------------
    def _confirm_attention(self, html_path: Path):
        if hasattr(self, 'active_menu') and self.active_menu:
            self.active_menu.unpost()
            self.active_menu = None
            self.active_menu_path = None

        progress_state = log_manager.load_progress_state()
        file_progress = progress_state.get(html_path.name, {}).get("percent", 0)

        queued, active = job_tracker.snapshot()
        in_progress = queued > 0 or active > 0

        if in_progress:
            current_files = [p.name for p in all_html_files]
            if html_path.name in current_files:
                return

        if file_progress < 100:
            if _gui_yes_no(f"Force {html_path.name} to be reprocessed.\n Current progress: {file_progress}%"):
                if html_path not in all_html_files:
                    all_html_files.append(html_path)
                    file_status[html_path] = "waiting"

                job_tracker.add_job()
                threading.Thread(target=lambda: _run_main_in_thread(html_path), daemon=True).start()

                self.after(100, lambda: self.refresh_file_list(all_html_files, file_status))
            return

        self.current_html_path = html_path
        self._show_attention_panel()

    def _show_attention_panel(self):
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        if hasattr(self, 'menu_manager'):
            if self.menu_manager.is_menu_open('attention'):
                self._hide_attention_panel()
                return
            self.menu_manager.show_menu('attention')
        else:
            if self.attention_visible:
                self._hide_attention_panel()
                return

        for widget in self.attention_frame.winfo_children():
            widget.destroy()

        self.attention_frame.config(bg=theme['bg'])

        title = Label(
            self.attention_frame,
            text="Game Actions",
            font=("Helvetica", 14, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        btn_frame = Frame(self.attention_frame, bg=theme['bg'])
        btn_frame.pack(fill="x", padx=20, pady=5)

        Button(
            btn_frame,
            text="Reprocess HTML",
            command=lambda: self._execute_attention_action("reprocess"),
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(side="left", padx=10, expand=True, fill="x")

        Button(
            btn_frame,
            text="Process Game",
            command=lambda: self._execute_attention_action("process"),
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(side="right", padx=10, expand=True, fill="x")

        Button(
            btn_frame,
            text="Stub Removal",
            command=lambda: self._show_stub_removal_panel(),
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(side="left", padx=10, expand=True, fill="x")

        separator = Frame(self.attention_frame, height=1, bg=theme['border'])
        separator.pack(fill="x", pady=10, padx=20)

        Button(
            self.attention_frame,
            text="Cancel",
            command=self._hide_attention_panel,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(pady=(0, 10))

        self.attention_frame.pack(fill="x", side="bottom", ipady=10)
        self.attention_visible = True

    def _hide_attention_panel(self):
        self.attention_frame.pack_forget()
        self.attention_visible = False
        if hasattr(self, 'menu_manager'):
            self.menu_manager._close_menu('attention')
            self.menu_manager._update_button_icons()

    def _execute_attention_action(self, action: str):
        html_path = self.current_html_path

        if action == "reprocess":
            progress_state = log_manager.load_progress_state()

            temp_file = TEMP_FOLDER / f"{html_path.name}.txt"
            temp_file.unlink(missing_ok=True)

            old_path = OLD_HTML_FOLDER / html_path.name
            if old_path.exists():
                shutil.move(str(old_path), str(html_path))

            folder_name = html_path.stem + "_files"
            old_folder = OLD_HTML_FOLDER / folder_name
            if old_folder.exists():
                shutil.move(str(old_folder), str(html_path.parent))

            with _prompt_handled_lock:
                _prompt_handled.pop(html_path, None)
            _download_done.pop(html_path, None)
            progress_state.pop(html_path.name, None)
            log_manager.save_progress_state(progress_state)

            if html_path in all_html_files:
                all_html_files.remove(html_path)
            file_status.pop(html_path, None)

            all_html_files.append(html_path)
            file_status[html_path] = "waiting"

            self.after(0, self.refresh_file_list, all_html_files, file_status)
            job_tracker.add_job()
            threading.Thread(target=lambda: _run_main_in_thread(html_path), daemon=True).start()
        elif action == "process":
            self.toggle_game_config(html_path)

        self._hide_attention_panel()

    def _show_stub_removal_panel(self):
        if hasattr(self, 'stub_removal_frame') and self.stub_removal_frame.winfo_exists():
            self.stub_removal_frame.destroy()

        self._hide_attention_panel()
        self._hide_stub_removal_options()

        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        self.stub_removal_selected_files = []
        self._stub_removal_html_path = self.current_html_path
        self.stub_removal_frame = Frame(self, bg=theme['bg'])
        self.stub_removal_frame.pack(fill="both", expand=True, padx=20, pady=20)

        title = Label(
            self.stub_removal_frame,
            text="Select Executables",
            font=("Helvetica", 14, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        info_label = Label(
            self.stub_removal_frame,
            text="Add one or more Windows executables to remove Steam stub from",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 10)
        )
        info_label.pack(pady=(0, 10))

        files_container = Frame(self.stub_removal_frame, bg=theme['bg'])
        files_container.pack(fill="both", expand=True, pady=(0, 10))
        files_canvas = Canvas(files_container, bg=theme['bg'], borderwidth=0, highlightthickness=0, height=100)
        files_scrollbar = Scrollbar(files_container, orient="vertical", command=files_canvas.yview)
        files_canvas.configure(yscrollcommand=files_scrollbar.set)
        files_scrollbar.pack(side="right", fill="y")
        files_canvas.pack(side="left", fill="both", expand=True)
        self.stub_removal_files_frame = Frame(files_canvas, bg=theme['bg'])
        files_canvas.create_window((0, 0), window=self.stub_removal_files_frame, anchor="nw")

        self.cols_per_row = 4
        def update_files_scrollregion(event=None):
            files_canvas.configure(scrollregion=files_canvas.bbox("all"))
        self.stub_removal_files_frame.bind("<Configure>", update_files_scrollregion)

        drop_frame = Frame(self.stub_removal_frame, bg=theme['bg'])
        drop_frame.pack(fill="x", pady=20)

        self.stub_drop_helper = DropZoneHelper(
            parent_widget=drop_frame,
            on_files_callback=self._handle_stub_removal_file,
            theme=theme,
            allowed_extensions=['.exe'],
            initial_text="Drop Windows .exe files here or click to browse\n(You can add multiple executables)",
            height=8,
            font_size=12
        )
        self.stub_removal_drop_label = self.stub_drop_helper.drop_label
        self.stub_removal_drop_label.focus_set()

        btn_frame = Frame(self.stub_removal_frame, bg=theme['bg'])
        btn_frame.pack(fill="x", pady=10)

        Button(
            btn_frame,
            text="Continue",
            command=self._show_stub_removal_options,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(side="left", padx=(0, 5), fill="x", expand=True)

        Button(
            btn_frame,
            text="Cancel",
            command=self._hide_stub_removal_panel,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(side="right", padx=(5, 0), fill="x", expand=True)

    def _hide_stub_removal_panel(self):
        if hasattr(self, 'stub_removal_frame') and self.stub_removal_frame.winfo_exists():
            self.stub_removal_frame.destroy()
        self.stub_removal_selected_files = []
        self.stub_removal_options = {}
        if hasattr(self, 'tooltip_label') and self.tooltip_label:
            self.tooltip_label.destroy()
            self.tooltip_label = None
        if hasattr(self, '_stub_removal_html_path'):
            self.current_html_path = self._stub_removal_html_path
        self._show_attention_panel()

    def _handle_stub_removal_file(self, path):
        file_path = Path(path)
        if not file_path.suffix.lower() == '.exe':
            show_custom_dialog(self, "warning", "Invalid File", "Only Windows .exe files are supported for stub removal")
            return
        self._add_stub_removal_file(file_path)

    def _add_stub_removal_file(self, file_path: Path):
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        if file_path in self.stub_removal_selected_files:
            return

        self.stub_removal_selected_files.append(file_path)
        index = len(self.stub_removal_selected_files) - 1
        row = index // self.cols_per_row
        col = index % self.cols_per_row

        file_frame = Frame(self.stub_removal_files_frame, bg=theme['bg'])
        file_frame.grid(row=row, column=col, padx=5, pady=5, sticky="w")

        file_label = Label(
            file_frame,
            text=f"{len(self.stub_removal_selected_files)}. {file_path.name}",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        )
        file_label.pack(side="left", padx=5)
        remove_btn = Button(
            file_frame,
            text="❌",
            command=lambda fp=file_path, ff=file_frame: self._remove_stub_removal_file(fp, ff),
            bg=theme['button_bg'],
            fg=theme['fg'],
            bd=0,
            relief='flat',
            padx=5,
            pady=2
        )
        remove_btn.pack(side="right", padx=5)

    def _remove_stub_removal_file(self, file_path: Path, file_frame):
        if file_path in self.stub_removal_selected_files:
            self.stub_removal_selected_files.remove(file_path)
        file_frame.destroy()

        for i, fp in enumerate(self.stub_removal_selected_files):
            row = i // self.cols_per_row
            col = i % self.cols_per_row
            for child in self.stub_removal_files_frame.winfo_children():
                if isinstance(child, Frame) and child.grid_info()["row"] == row and child.grid_info()["column"] == col:
                    for label in child.winfo_children():
                        if isinstance(label, Label):
                            label.config(text=f"{i + 1}. {fp.name}")

        if hasattr(self, 'stub_removal_files_frame'):
            self.stub_removal_files_frame.update_idletasks()
            files_canvas = self.stub_removal_files_frame.master
            files_canvas.configure(scrollregion=files_canvas.bbox("all"))

    def _show_stub_removal_options(self):
        if not self.winfo_exists() or self._stop_requested:
            return
        if not self.stub_removal_selected_files:
            show_custom_dialog(self, "warning", "No Files", "Please add at least one executable file")
            return

        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        if hasattr(self, 'stub_removal_frame') and self.stub_removal_frame.winfo_exists():
            self.stub_removal_frame.pack_forget()
        self.stub_removal_options_frame = Frame(self, bg=theme['bg'])
        self.stub_removal_options_frame.pack(fill="both", expand=True, padx=20, pady=20)

        title = Label(
            self.stub_removal_options_frame,
            text="Stub Removal - Options",
            font=("Helvetica", 14, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        files_text = ", ".join(fp.name for fp in self.stub_removal_selected_files)
        files_label = Label(
            self.stub_removal_options_frame,
            text=f"Selected files: {files_text}",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 10)
        )
        files_label.pack(pady=(0, 10))

        steamless_dir = APP_FOLDER / "steamless"
        version_folders = [f for f in steamless_dir.iterdir() if f.is_dir()] if steamless_dir.exists() else []
        if not version_folders:
            show_custom_dialog(self, "error", "Error", "No Steamless versions found. Please download at least one version in Settings > Steamless Config")
            self._hide_stub_removal_options()
            return
        version_folders.sort(key=lambda x: x.name, reverse=True)

        version_frame = Frame(self.stub_removal_options_frame, bg=theme['bg'])
        version_frame.pack(fill="x", pady=5)
        version_label = Label(
            version_frame,
            text="Select Steamless Version:",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        )
        version_label.pack(side="left", padx=(0, 10))

        self.stub_removal_version_helper = VersionDropdownHelper(
            version_frame,
            target="steamless",
            on_select_callback=lambda v: setattr(self, 'stub_removal_selected_version', v),
            theme=theme,
            disabled_if_single=False
        )
        self.stub_removal_selected_version = version_folders[0].name if version_folders else None

        options_label = Label(
            self.stub_removal_options_frame,
            text="Options",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 12, "bold")
        )
        options_label.pack(pady=(10, 5))

        self.stub_removal_options = {
            'keep_bind': tk.BooleanVar(value=False),
            'dump_payload': tk.BooleanVar(value=False),
            'dump_steamdrmp': tk.BooleanVar(value=False),
            'experimental': tk.BooleanVar(value=False),
            'no_realign': tk.BooleanVar(value=True),
            'zero_dos': tk.BooleanVar(value=True),
            'recalculate_checksum': tk.BooleanVar(value=False)
        }

        tooltip_descriptions = {
            'keep_bind': "What it does: By default .bind section is removed (which contains Steam-specific binding data) from the unpacked executable. Using this flag keeps that section intact in the output file.\n\nWhy use it: Some games may crash or fail to load if the .bind section is removed due to hardcoded section references. This flag preserves compatibility in those edge cases.",
            'dump_payload': "What it does: Extracts the payload (the actual unpacked code/data that the SteamDRM wrapper was protecting) and saves it as a separate binary file on disk. The output file will be named <original>_payload.bin (or similar).\n\nWhy use it: Useful for analyzing the payload separately, especially if you want to inspect the raw unpacked code without the executable wrapper.",
            'dump_steamdrmp': "What it does: Extracts the embedded SteamDRM.dll (the anti-tamper library) from the protected executable and saves it to disk as a separate file. The output will be named <original>_SteamDRM.dll.\n\nWhy use it: Useful for reverse engineering the DRM itself, or for comparison with other versions of the DRM.",
            'experimental': "What it does: Enables experimental/unstable code paths. These are features that are still being tested and might not work correctly on all files, but can sometimes handle edge cases that the stable version fails on.\n\nWhy use it: If the standard version fails to unpack a particular executable, try this flag. It may succeed but could also produce a corrupted output.",
            'no_realign': "What it does: This realigns the executable's sections to a standard alignment (usually 0x1000 for PE files). This flag disables that realignment, keeping the original (potentially non-standard) file alignment from the protected executable.\n\nWhy use it: Some debuggers or tools expect the original alignment. If the realigned file causes issues in your analysis tools, use this flag to keep the original layout.",
            'zero_dos': "What it does: The DOS stub (the small of a PE file that runs if you execute it in DOS mode) is often filled with data in protected files. This flag zeros out (sets to 0x00) all data in the DOS stub of the output file.\n\nWhy use it: Cleaner output, and it removes data or anti-analysis tricks stored in the DOS stub.",
            'recalculate_checksum': "What it does: After unpacking, the output file's checksum may be invalid. This flag recalculates the PE header checksum and patches it into the output file, making it valid.\n\nWhy use it: Some loaders or debuggers (like x64dbg) ignore invalid checksums, but others (like some anti-cheat or Windows loader checks) may reject the file. This ensures the file passes that validation."
        }

        options_frame = Frame(self.stub_removal_options_frame, bg=theme['bg'])
        options_frame.pack(fill="x", pady=5)
        for key, var in self.stub_removal_options.items():
            option_frame = Frame(options_frame, bg=theme['bg'])
            option_frame.pack(fill="x", pady=2)
            Checkbutton(
                option_frame,
                text=self._get_option_display_name(key),
                variable=var,
                bg=theme['bg'],
                fg=theme['fg'],
                selectcolor=theme['widget_bg']
            ).pack(side="left", padx=5)
            tooltip_btn = Label(
                option_frame,
                text="❓",
                bg=theme['bg'],
                fg=theme['fg'],
                cursor="question_arrow"
            )
            tooltip_btn.pack(side="left", padx=5)
            tooltip_btn.bind("<Enter>", lambda e, k=key: self._show_tooltip(e, tooltip_descriptions[k]))
            tooltip_btn.bind("<Leave>", self._hide_tooltip)
            tooltip_btn.bind("<Button-1>", lambda e, k=key: self._show_tooltip(e, tooltip_descriptions[k]))

        btn_frame = Frame(self.stub_removal_options_frame, bg=theme['bg'])
        btn_frame.pack(fill="x", pady=10)
        Button(
            btn_frame,
            text="Execute",
            command=self._execute_stub_removal,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(side="left", padx=(5, 0), fill="x", expand=True)
        Button(
            btn_frame,
            text="Back",
            command=self._show_stub_removal_panel,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=15,
            pady=8
        ).pack(side="right", padx=(5, 0), fill="x", expand=True)

    def _hide_stub_removal_options(self):
        if hasattr(self, 'stub_removal_options_frame') and self.stub_removal_options_frame.winfo_exists():
            self.stub_removal_options_frame.pack_forget()
            self.stub_removal_options_frame.destroy()
        if hasattr(self, 'tooltip_label') and self.tooltip_label:
            self.tooltip_label.destroy()
            self.tooltip_label = None

    def _get_option_display_name(self, key):
        display_names = {
            'keep_bind': "Keep Bind Section",
            'dump_payload': "Dump Payload To Disk",
            'dump_steamdrmp': "Dump SteamDRMP.dll To Disk",
            'experimental': "Use Experimental Features",
            'no_realign': "Don't Realign Sections",
            'zero_dos': "Zero DOS Stub Data",
            'recalculate_checksum': "Recalculate File Checksum"
        }
        return display_names.get(key, key)

    def _show_tooltip(self, event, text):
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        if hasattr(self, 'tooltip_label') and self.tooltip_label:
            self.tooltip_label.destroy()
            self.tooltip_label = None

        self.tooltip_label = tk.Toplevel(self)
        self.tooltip_label.overrideredirect(True)
        self.tooltip_label.wm_geometry(f"+{event.x_root + 20}+{event.y_root + 20}")

        tooltip_frame = Frame(self.tooltip_label, bg=theme['widget_bg'], borderwidth=1, relief="solid")
        tooltip_frame.pack()

        tooltip_text = Label(
            tooltip_frame,
            text=text,
            bg=theme['widget_bg'],
            fg=theme['fg'],
            justify="left",
            wraplength=400,
            font=("Helvetica", 10)
        )
        tooltip_text.pack(padx=5, pady=5)

        self.tooltip_label.lift()
        self.tooltip_label.attributes("-topmost", True)

    def _hide_tooltip(self, event=None):
        if hasattr(self, 'tooltip_label') and self.tooltip_label:
            try:
                self.tooltip_label.destroy()
            except tk.TclError:
                pass
            self.tooltip_label = None

    def _show_execution_output(self, output_text: str = "", log_paths: dict = None):
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        log_paths = log_paths or {}

        if hasattr(self, 'output_frame') and self.output_frame.winfo_exists():
            self.output_frame.destroy()

        self.output_frame = Frame(self, bg=theme['bg'])
        self.output_frame.pack(fill="both", expand=True, padx=20, pady=20)

        title = Label(
            self.output_frame,
            text="Steamless Stub Removal Output",
            font=("Helvetica", 14, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        text_frame = Frame(self.output_frame, bg=theme['bg'])
        text_frame.pack(fill="both", expand=True, pady=(0, 10))

        scrollbar = Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")

        self.output_text = tk.Text(
            text_frame,
            wrap="word",
            bg=theme['widget_bg'],
            fg=theme['fg'],
            insertbackground=theme['fg'],
            yscrollcommand=scrollbar.set,
            font=("Courier", 10),
            state="normal"
        )
        self.output_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.output_text.yview)

        if output_text:
            self.output_text.insert("end", output_text)
            self.output_text.see("end")

        btn_frame = Frame(self.output_frame, bg=theme['bg'])
        btn_frame.pack(fill="x", pady=5)

        Button(
            btn_frame,
            text="❌ Close",
            command=self._hide_execution_output,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        ).pack(side="right", padx=(5, 0))

        if not output_text:
            self.output_text.config(state="disabled")

    def _execute_stub_removal(self):
        if not self.stub_removal_selected_files:
            show_custom_dialog(self, "warning","No Files", "No executables selected for stub removal")
            return

        version = getattr(self, 'stub_removal_selected_version', self.stub_removal_version_helper.get_selected_version())
        steamless_dir = APP_FOLDER / "steamless" / version if version else APP_FOLDER / "steamless"
        steamless_cli = steamless_dir / "Steamless.CLI.exe"

        if not steamless_cli.exists():
            show_custom_dialog(self, "error", "Error", f"Steamless.CLI.exe not found in {steamless_dir}")
            return

        self._hide_stub_removal_options()

        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        if hasattr(self, 'output_frame') and self.output_frame.winfo_exists():
            self.output_frame.destroy()

        self.output_frame = Frame(self, bg=theme['bg'])
        self.output_frame.pack(fill="both", expand=True, padx=20, pady=20)

        title = Label(
            self.output_frame,
            text="Stub Removal Output",
            font=("Helvetica", 14, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        text_frame = Frame(self.output_frame, bg=theme['bg'])
        text_frame.pack(fill="both", expand=True, pady=(0, 10))

        scrollbar = Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")

        self.output_text = tk.Text(
            text_frame,
            wrap="word",
            bg=theme['widget_bg'],
            fg=theme['fg'],
            insertbackground=theme['fg'],
            yscrollcommand=scrollbar.set,
            font=("Courier", 10),
            state="normal"
        )
        self.output_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.output_text.yview)

        btn_frame = Frame(self.output_frame, bg=theme['bg'])
        btn_frame.pack(fill="x", pady=5)

        Button(
            btn_frame,
            text="📋 Copy Logs",
            command=lambda: self._copy_output_to_clipboard(),
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        ).pack(side="left", padx=(0, 5))

        Button(
            btn_frame,
            text="💾 Save Log",
            command=lambda: self._save_live_output(),
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        ).pack(side="left", padx=(0, 5))

        Button(
            btn_frame,
            text="❌ Close",
            command=self._hide_execution_output,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        ).pack(side="right", padx=(5, 0))

        log_paths = {}
        all_output = ""

        for exe_path in self.stub_removal_selected_files:
            if sys.platform.startswith("win"):
                cmd = ["Steamless.CLI.exe"]
            else:
                if shutil.which("wine"):
                    cmd = ["wine", "Steamless.CLI.exe"]
                else:
                    show_custom_dialog(self, "error", "Error", "Wine is required to run Steamless.CLI.exe on this platform")
                    return

            if self.stub_removal_options['keep_bind'].get():
                cmd.append("-k")
            if self.stub_removal_options['dump_payload'].get():
                cmd.append("-d")
            if self.stub_removal_options['dump_steamdrmp'].get():
                cmd.append("-s")
            if self.stub_removal_options['experimental'].get():
                cmd.append("-e")
            if self.stub_removal_options['no_realign'].get():
                cmd.append("-r")
            if self.stub_removal_options['zero_dos'].get():
                cmd.append("-z")
            if self.stub_removal_options['recalculate_checksum'].get():
                cmd.append("-c")

            cmd.append("-v")
            cmd.append(str(exe_path))

            log_path = exe_path.with_suffix('.exe_log.txt')

            self.output_text.insert("end", f"\n{'='*60}\nProcessing: {exe_path.name}\n{'='*60}\n\n")
            self.output_text.see("end")
            self.update_idletasks()

            try:
                if sys.platform.startswith("win"):
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                    process = subprocess.Popen(
                        cmd,
                        cwd=steamless_dir,
                        startupinfo=startupinfo,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )
                else:
                    process = subprocess.Popen(
                        cmd,
                        cwd=steamless_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )

                for line in process.stdout:
                    if line.strip():
                        self.output_text.insert("end", line)
                        self.output_text.see("end")
                        self.update_idletasks()

                return_code = process.wait()

                if return_code == 0:
                    base_name = str(exe_path)
                    bak_path = Path(base_name + ".bak")
                    counter = 1
                    while bak_path.exists():
                        bak_path = Path(f"{base_name}.bak{counter}")
                        counter += 1
                    exe_path.rename(bak_path)

                    unpacked_path = Path(base_name + ".unpacked.exe")
                    if unpacked_path.exists():
                        unpacked_path.rename(exe_path)

                    self.output_text.insert("end", f"\n✅ Completed successfully for {exe_path.name}\n")
                else:
                    self.output_text.insert("end", f"\n⚠️  Failed for {exe_path.name} (Return code: {return_code})\n")

                self.output_text.see("end")
                self.update_idletasks()

            except Exception as e:
                self.output_text.insert("end", f"\n❌ Failed to execute for {exe_path.name}: {str(e)}\n")
                self.output_text.see("end")
                self.update_idletasks()
                print(f"❌ Error processing {exe_path.name}: {e}")

        self.output_text.config(state="disabled")

        for btn_frame in self.output_frame.winfo_children():
            if isinstance(btn_frame, Frame):
                for btn in btn_frame.winfo_children():
                    if isinstance(btn, Button) and "Close" in btn.cget("text"):
                        btn.pack(side="right", padx=(5, 0))
                        break

    def _hide_execution_output(self):
        if hasattr(self, 'output_frame') and self.output_frame.winfo_exists():
            self.output_frame.destroy()
        if hasattr(self, 'output_text'):
            self.output_text = None

    def _copy_output_to_clipboard(self):
        if hasattr(self, 'output_text') and self.output_text:
            output = self.output_text.get("1.0", "end-1c")
            try:
                self.clipboard_clear()
                self.clipboard_append(output)
                show_custom_dialog(self, "info", "Copied", "Logs copied to clipboard!")
            except Exception as e:
                show_custom_dialog(self, "error", "Error", f"Failed to copy logs: {e}")

    def _save_live_output(self):
        if hasattr(self, 'output_text') and self.output_text:
            output = self.output_text.get("1.0", "end-1c")
            log_path = Path.home() / f"steamless_output_{time.strftime('%Y%m%d_%H%M%S')}.log"

            try:
                log_path.write_text(output, encoding='utf-8')
                show_custom_dialog(self, "info", "Saved", f"Logs saved to:\n{log_path}")
            except Exception as e:
                show_custom_dialog(self, "error", "Error", f"Failed to save logs: {e}")

    def _on_close(self):
        self._stop_requested = True

        if hasattr(self, '_active_dialog') and self._active_dialog:
            try:
                self._active_dialog.dialog_frame.destroy()
            except:
                pass
            self._active_dialog = None

        for attr in ['output_frame', 'attention_frame', 'stub_removal_frame', 'stub_removal_options_frame', 'game_config_frame', 'settings_frame', 'tooltip_label']:
            if hasattr(self, attr):
                widget = getattr(self, attr)
                if widget and hasattr(widget, 'winfo_exists'):
                    if widget.winfo_exists():
                        widget.destroy()
                elif widget and hasattr(widget, 'destroy'):
                    try:
                        widget.destroy()
                    except:
                        pass
                setattr(self, attr, None)

        for manager in self.download_managers.values():
            for version, entry in list(manager.download_status.items()):
                if entry.get('status') in ['downloading', 'extracting']:
                    entry['status'] = 'cancelled'
                if 'status_frame' in entry and entry['status_frame']:
                    try:
                        if entry['status_frame'].winfo_exists():
                            entry['status_frame'].destroy()
                    except:
                        pass
                    entry['status_frame'] = None

        self.stub_removal_selected_files = []
        self.stub_removal_options = {}

        self.destroy()

# ------------------------------------------------------------
    def show_log_viewer(self) -> None:
        if hasattr(self, 'log_viewer_frame') and self.log_viewer_frame is not None:
            if self.log_viewer_frame.winfo_exists():
                self.log_viewer_frame.destroy()
            self.log_viewer_frame = None

        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        self.log_viewer_frame = Frame(self, bg=theme['bg'])
        self.log_viewer_frame.pack(fill="both", expand=True, padx=20, pady=20)

        if hasattr(self, 'menu_manager'):
            self.menu_manager.menu_frames['logs'] = self.log_viewer_frame
            self.menu_manager.current_menu = 'logs'
            self.menu_manager.open_menus.add('logs')
            self.menu_manager._update_button_icons()

        canvas_frame = Frame(self.log_viewer_frame, bg=theme['bg'])
        canvas_frame.pack(fill="x", pady=(10, 0))

        Label(
            canvas_frame,
            text="Application Logs",
            font=("Helvetica", 14, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        ).pack(pady=(0, 2))

        self.log_files_canvas = Canvas(canvas_frame, bg=theme['bg'], borderwidth=0, highlightthickness=0, height=100)
        self.log_files_scrollbar = Scrollbar(canvas_frame, orient="vertical", command=self.log_files_canvas.yview)
        self.log_files_canvas.configure(yscrollcommand=self.log_files_scrollbar.set)
        self.log_files_scrollbar.pack(side="right", fill="y")
        self.log_files_canvas.pack(side="left", fill="both", expand=True)

        self.log_files_inner_frame = Frame(self.log_files_canvas, bg=theme['bg'])
        self.log_files_canvas.create_window((0, 0), window=self.log_files_inner_frame, anchor="nw")

        self.log_files_inner_frame.bind("<Configure>", lambda e: self.log_files_canvas.configure(scrollregion=self.log_files_canvas.bbox("all")))
        self.log_files_canvas.bind("<Configure>", lambda e: self.log_files_canvas.itemconfig("all", width=e.width))

        self._populate_log_files_list(theme)

        self.current_log_path = None

        text_frame = Frame(self.log_viewer_frame, bg=theme['bg'])
        text_frame.pack(fill="both", expand=False, pady=(8, 5))

        scrollbar = Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(
            text_frame,
            wrap="word",
            bg=theme['widget_bg'],
            fg=theme['fg'],
            insertbackground=theme['fg'],
            yscrollcommand=scrollbar.set,
            font=("Courier", 10),
            state="normal",
            takefocus=0
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

        session_log = log_manager.get_session_log()
        self.log_text.insert("end", session_log)
        self.log_text.config(state="disabled")
        self.log_text.see("end")

        btn_frame = Frame(self.log_viewer_frame, bg=theme['bg'])
        btn_frame.pack(fill="x", pady=5)

        Button(
            btn_frame,
            text="📋 Copy Log",
            command=self._copy_log,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        ).pack(side="left", padx=(0, 5))

        Button(
            btn_frame,
            text="💾 Save Log",
            command=self._save_log,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        ).pack(side="left", padx=(0, 5))

        self.open_file_btn = Button(
            btn_frame,
            text="📄 Open File",
            command=self._open_log_file_external,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        )

        self.close_log_btn = Button(
            btn_frame,
            text="❌ Close Log",
            command=self._close_current_log,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=10,
            pady=5
        )

    def hide_log_viewer(self) -> None:
        if hasattr(self, 'log_viewer_frame') and self.log_viewer_frame.winfo_exists():
            self.log_viewer_frame.destroy()
        self.log_viewer_frame = None
        if hasattr(self, 'log_text'):
            self.log_text = None
        if hasattr(self, 'current_log_path'):
            self.current_log_path = None
        if hasattr(self, 'menu_manager'):
            self.menu_manager._close_menu('logs')
            self.menu_manager._update_button_icons()

    def _populate_log_files_list(self, theme: dict) -> None:
        for widget in self.log_files_inner_frame.winfo_children():
            widget.destroy()

        log_files = log_manager.get_all_log_files()
        log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        for log_file in log_files:
            btn = Button(
                self.log_files_inner_frame,
                text=log_file.name,
                command=lambda lf=log_file: self._show_log_file(lf),
                bg=theme['button_bg'],
                fg=theme['fg'],
                bd=0,
                relief='flat',
                anchor="w"
            )
            btn.pack(fill="x", pady=2)

    def _show_log_file(self, log_path: Path) -> None:
        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        log_content = log_manager.read_log_file(log_path)
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, "end")
        self.log_text.insert("end", log_content)
        self.log_text.config(state="disabled")
        self.log_text.see("end")

        self.current_log_path = log_path

        self.open_file_btn.pack(side="left", padx=(0, 5))
        self.close_log_btn.pack(side="left", padx=(0, 5))

    def _close_current_log(self) -> None:
        session_log = log_manager.get_session_log()
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, "end")
        self.log_text.insert("end", session_log)
        self.log_text.config(state="disabled")
        self.log_text.see("end")

        self.open_file_btn.pack_forget()
        self.close_log_btn.pack_forget()
        self.current_log_path = None

    def _copy_log(self) -> None:
        if hasattr(self, 'log_text') and self.log_text:
            log_content = self.log_text.get("1.0", "end-1c")
            try:
                self.clipboard_clear()
                self.clipboard_append(log_content)
                show_custom_dialog(self, "info", "Copied", "Log copied to clipboard!")
            except Exception as e:
                show_custom_dialog(self, "error", "Error", f"Failed to copy log: {e}")

    def _save_log(self) -> None:
        if hasattr(self, 'log_text') and self.log_text:
            log_content = self.log_text.get("1.0", "end-1c")
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            log_path = log_manager.log_dir / f"saved_log_{timestamp}.log"

            try:
                log_path.write_text(log_content, encoding='utf-8')
                show_custom_dialog(self, "info", "Saved", f"Log saved to:\n{log_path}")
                # Refresh log files list
                self._populate_log_files_list(self.DARK_THEME if self.dark_mode else self.LIGHT_THEME)
            except Exception as e:
                show_custom_dialog(self, "error", "Error", f"Failed to save log: {e}")

    def _open_log_file_external(self) -> None:
        if hasattr(self, 'current_log_path') and self.current_log_path:
            if sys.platform.startswith("win"):
                try:
                    os.startfile(str(self.current_log_path))
                except Exception as e:
                    show_custom_dialog(self, "error", "Error", f"Failed to open file: {e}")
            else:
                editors = [
                    "xdg-open",
                    "pluma",
                    "gedit",
                    "mousepad",
                    "leafpad",
                    "kate",
                    "nano",
                    "vim",
                    "emacs",
                    "mousepad",
                    "geany"
                ]
                opened = False
                for editor in editors:
                    try:
                        subprocess.run([editor, str(self.current_log_path)], check=True)
                        opened = True
                        break
                    except Exception:
                        continue
                if not opened:
                    show_custom_dialog(self, "error", "Error", "Could not open file with any available editor.")

    # ------------------------------------------------------------
    def _init_game_config(self):
        self.game_config_frame = Frame(self)
        self.game_config_visible = False
        self.current_platform = None
        self.emu_var = None
        self.drop_label = None
        self.selected_file = None
        self.drop_zone_active = False
        self.last_clipboard_content = ""
        self.clipboard_check = None
        self.processing_step = 1

    def toggle_game_config(self, html_path: Path | None = None):
        if self.game_config_visible:
            if self.processing_step == 2:
                self.processing_step = 1
                self.selected_file = None
                self.platform_label.pack()
                self.emu_label.pack(side="left", padx=(0, 10))
                self.emu_dropdown.pack(side="left")
                if hasattr(self, 'game_drop_helper'):
                    self.game_drop_helper.update_text("Drop A Game Executable Here or Click to Browse")
                return
            else:
                if self.selected_file is not None:
                    try:
                        temp_file = TEMP_FOLDER / f"{self.current_html_path.name}.txt"
                        if temp_file.exists():
                            game_dir = None
                            for line in temp_file.read_text().splitlines():
                                if line.startswith("GAMEDIR="):
                                    game_dir = Path(line.split("=", 1)[1].strip())
                                    break
                            if game_dir:
                                gpfile = game_dir / ".gpfile"
                                if gpfile.exists():
                                    gpfile.unlink()

                    except Exception as e:
                        print(f"Error cleaning .gpfile on cancel: {e}")

                self.game_config_frame.pack_forget()
                self.game_config_visible = False
                self.selected_file = None
                self._show_attention_panel()
        else:
            if html_path:
                self.current_html_path = html_path
            self._populate_game_config()
            self.game_config_frame.pack(fill="both", expand=True)
            self.game_config_visible = True
            self.processing_step = 1

    def _populate_game_config(self):
        for widget in self.game_config_frame.winfo_children():
            widget.destroy()

        if hasattr(self, "custom_theme") and self.custom_theme:
            theme = self.custom_theme
        else:
            theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        self.game_config_frame.config(bg=theme['bg'])

        title = Label(
            self.game_config_frame,
            text="Game Configuration",
            font=("Helvetica", 16, "bold"),
            bg=theme['bg'],
            fg=theme['fg']
        )
        title.pack(pady=10)

        self.selection_container = Frame(self.game_config_frame, bg=theme['bg'])
        self.selection_container.pack(pady=10)

        self.platform_label = Label(
            self.selection_container,
            text="Detected Platform: None",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 12)
        )
        self.platform_label.pack()

        self.emu_frame = Frame(self.selection_container, bg=theme['bg'])
        self.emu_label = Label(
            self.emu_frame,
            text="Select Emulator:",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        )
        self.emu_label.pack(side="left", padx=(0, 10))

        self.emu_var = tk.StringVar()
        self.emu_dropdown = ttk.Combobox(
            self.emu_frame,
            textvariable=self.emu_var,
            values=["GBE", "GSE"],
            cursor="hand2",
            state="readonly",
            width=15
        )
        self.emu_dropdown.pack(side="left")
        self.emu_var.set("GBE")
        self.emu_frame.pack()

        self.version_frame = Frame(self.selection_container, bg=theme['bg'])
        self.version_frame.pack(pady=5)

        self.version_label = Label(
            self.version_frame,
            text="Version:",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        )
        self.version_label.pack(side="left", padx=(0, 10))

        self.version_dropdown_helper = VersionDropdownHelper(
            self.version_frame,
            target="gbe",
            on_select_callback=lambda v: setattr(self, 'selected_version', v),
            theme=theme
        )

        self.emu_dropdown.bind("<<ComboboxSelected>>", self._on_emulator_changed)

        self.arch_frame = Frame(self.selection_container, bg=theme['bg'])
        self.arch_label = Label(
            self.arch_frame,
            text="Select Architecture:",
            bg=theme['bg'],
            fg=theme['fg'],
            font=("Helvetica", 11)
        )
        self.arch_label.pack(side="left", padx=(0, 10))

        self.arch_var = tk.StringVar()
        self.arch_dropdown = ttk.Combobox(
            self.arch_frame,
            textvariable=self.arch_var,
            values=["32 Bit", "64 Bit"],
            cursor="hand2",
            state="readonly",
            width=15
        )
        self.arch_dropdown.pack(side="left")

        drop_frame = Frame(self.game_config_frame, bg=theme['bg'])
        drop_frame.pack(pady=10, fill="x", padx=20)

        self.game_drop_helper = DropZoneHelper(
            parent_widget=drop_frame,
            on_files_callback=self._handle_file,
            theme=theme,
            allowed_extensions=['.exe', '.x86', '.x86_64', '.sh', '.bin', ''],
            initial_text="Drop A Game Executable Here or Click to Browse",
            height=13,
            font_size=12
        )

        self.drop_label = self.game_drop_helper.drop_label
        self.drop_label.focus_set()

        btn_frame = Frame(self.game_config_frame, bg=theme['bg'])
        btn_frame.pack(fill="x", pady=10)

        Button(
            btn_frame,
            text="Process",
            command=lambda: threading.Thread(target=self._process_game, daemon=True).start(),
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=20
        ).pack(side="left", padx=(5, 0), fill="x", expand=True)

        Button(
            btn_frame,
            text="Cancel",
            command=self.toggle_game_config,
            bg=theme['button_bg'],
            fg=theme['fg'],
            padx=20
        ).pack(side="right", padx=(5, 0), fill="x", expand=True)

    def _on_emulator_changed(self, event):
        selected_emu = self.emu_var.get().lower()
        self.version_dropdown_helper.target = selected_emu

        for widget in self.version_frame.winfo_children():
            if widget != self.version_label:
                widget.destroy()

        self.version_dropdown_helper = VersionDropdownHelper(
            self.version_frame,
            target=selected_emu,
            on_select_callback=lambda v: setattr(self, 'selected_version', v),
            theme=self.DARK_THEME if self.dark_mode else self.LIGHT_THEME
        )

        if hasattr(self, 'drop_label') and self.drop_label.winfo_exists():
            self.drop_label.focus_set()

        if hasattr(self, 'selected_file') and self.selected_file is not None:
            self._handle_file(self.selected_file)

    def _is_wayland(self):
        return "wayland" in os.environ.get("XDG_SESSION_TYPE", "").lower()

    def _detect_platform(self, file_path: str):
        path = Path(file_path)
        if path.suffix.lower() == '.exe':
            self.current_platform = 'Windows'
            self.emu_dropdown['values'] = ['GBE', 'GSE']
            self.platform_label.config(text=f"Detected Platform: {self.current_platform}")
            self.emu_var.set('GBE')
        elif path.suffix.lower() in ('.x86', '.x86_64', '.sh', '.bin', ''):
            self.current_platform = 'Linux'
            self.emu_dropdown['values'] = ['GBE', 'GSE']
            self.platform_label.config(text=f"Detected Platform: {self.current_platform}")
            self.emu_var.set('GBE')
        else:
            self.current_platform = 'Unknown'
            self.platform_label.config(text="Detected Platform: Unknown")
            self.emu_dropdown['values'] = ['GBE', 'GSE']
            self.emu_var.set('GBE')
    
    def _console_file_prompt(self):
        log_manager.log_message("--- Console File Selection ---")
        log_manager.log_message("Please enter the full path to your game executable:")
        while True:
            path = input("File path: ").strip()
            if os.path.exists(path):
                return path
            print("File not found. Please try again.")

    def _open_file_dialog(self):
        if self.processing_step == 1:
            filetypes = [("Executables", "*.exe *.x86 *.x86_64 *.bin *.sh"), ("All files", "*.*")]
        else:
            if self.current_platform == "Windows":
                filetypes = [("DLL Files", "*.dll"), ("All files", "*.*")]
            elif self.current_platform == "Linux":
                filetypes = [("Shared Objects", "*.so"), ("All files", "*.*")]
            else:
                filetypes = [("All files", "*.*")]

        file_path = filedialog.askopenfilename(
            title="Select File",
            filetypes=filetypes
        )
        if file_path:
            self._handle_file(file_path)

    def _handle_file(self, path):
        self.selected_file = Path(path)
        self.drop_label.config(text=f"Selected: {self.selected_file.name}")

        current_emulator = self.emu_var.get()
        self._detect_platform(path)
        self.emu_var.set(current_emulator)

        try:
            temp_file = TEMP_FOLDER / f"{self.current_html_path.name}.txt"
            if not temp_file.exists():
                raise FileNotFoundError("No temp file found")

            game_dir = None
            for line in temp_file.read_text().splitlines():
                if line.startswith("GAMEDIR="):
                    game_dir = Path(line.split("=", 1)[1].strip())
                    break
        
            if game_dir and game_dir.exists():
                self._update_gpfile(game_dir, self.selected_file)
            
                gpfile = game_dir / ".gpfile"
                if gpfile.exists():
                    with gpfile.open() as f:
                        data = {}
                        for line in f:
                            if '=' in line:
                                key, value = line.split('=', 1)
                                data[key.strip()] = value.strip()
                    
                        if 'ARCHITECTURE' not in data and self.selected_file.suffix.lower() in ('.so', '.dll'):
                            self.platform_label.pack_forget()
                            self.emu_label.pack_forget()
                            self.emu_dropdown.pack_forget()
                            self.arch_frame.pack(pady=10)
                            self.arch_label.pack(side="left", padx=(0, 10))
                            self.arch_dropdown.pack(side="left")
                            self.arch_var.trace_add("write", self._on_architecture_selected)
            else:
                raise ValueError("Could not find valid GAMEDIR in temp file")
            
        except Exception as e:
            print(f"Error updating .gpfile: {e}")
            show_custom_dialog(self, "error", "GPFile Error", f"Could not create configuration:\n {str(e)}")

    def _on_architecture_selected(self, *args):
        if self.arch_var.get():
            try:
                temp_file = TEMP_FOLDER / f"{self.current_html_path.name}.txt"
                game_dir = Path(temp_file.read_text().split("GAMEDIR=",1)[1].split("\n",1)[0].strip())
                gpfile = game_dir / ".gpfile"
            
                content = []
                if gpfile.exists():
                    with gpfile.open('r') as f:
                        content = f.readlines()
            
                arch = 'x86' if self.arch_var.get() == '32 Bit' else 'x86_64'
                new_entry = f"ARCHITECTURE={arch}"
            
                content = [line for line in content if not line.startswith("ARCHITECTURE=")]
            
                if content and not content[-1].endswith('\n'):
                    content.append('\n')
                content.append(f"{new_entry}\n")
            
                with gpfile.open('w') as f:
                    f.writelines(content)
            
                self.processing_step = 2
            
            except Exception as e:
                show_custom_dialog(self, "error", "Error", f"Failed to save architecture: {str(e)}")

    def _generate_interface_file(self, game_dir: Path, gp_data: dict):
        try:
            library_path = Path(gp_data["LBP_PATH"])
            is_windows = library_path.suffix.lower() == '.dll'
            is_linux = library_path.suffix.lower() == '.so'
        
            if not (is_windows or is_linux):
                raise ValueError("Unsupported library type - must be .dll or .so")

            architecture = gp_data.get("ARCHITECTURE", "")
            tool_suffix = "x32" if architecture == "x86" else "x64"
            emulator = self.selected_emulator.lower()
            tools_dir = APP_FOLDER / "tools" / f"{emulator}_tools"

            if is_windows:
                tool_name = f"generate_interfaces_{tool_suffix}.exe"
                tool_path = tools_dir / tool_name
                if sys.platform.startswith("win"):
                    cmd = [str(tool_path), str(library_path)]
                else:
                    cmd = ["wine", str(tool_path), str(library_path)]
            else:
                tool_name = f"generate_interfaces_{tool_suffix}"
                tool_path = tools_dir / tool_name
                cmd = [str(tool_path), str(library_path)]

            steam_settings = game_dir / "steam_settings"
            steam_settings.mkdir(exist_ok=True)

            startupinfo = None
            if sys.platform.startswith("win"):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            subprocess.run(
                cmd,
                cwd=str(steam_settings),
                startupinfo=startupinfo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )

            log_manager.log_message(f"Successfully generated interface file for {library_path.name}")
            return True
    
        except subprocess.CalledProcessError as e:
            print(f"Interface generation failed: {e}")
            show_custom_dialog(self, "error", "Error", f"Failed to generate interface file: {e}")
        except Exception as e:
            print(f"Unexpected error during interface generation: {e}")
            show_custom_dialog(self, "error", "Error", f"Unexpected error: {e}")
    
        return False

    def _update_cold_loader_ini(self, game_dir: Path, exe_path: Path, appid: str):
        cold_loader_path = game_dir / "ColdClientLoader.ini"
        if not cold_loader_path.exists():
            return
  
        try:
            exe_str = str(exe_path)
            patterns = ["SteamLibrary", "steamapps", "common"]

            start_index = 0
            for pattern in patterns:
                idx = exe_str.find(pattern)
                if idx != -1:
                    start_index = idx + len(pattern) + 1

            relative_exe = exe_str[start_index:]
            parts = relative_exe.split(os.sep)
            if len(parts) > 1:
                relative_exe = os.sep.join(parts[1:])

            relative_exe = relative_exe.replace("/", "\\")

            lines = []
            with open(cold_loader_path, "r") as f:
                for line in f:
                    if line.startswith("Exe="):
                        lines.append(f"Exe={relative_exe}\n")
                    elif line.startswith("AppId="):
                        lines.append(f"AppId={appid}\n")
                    else:
                        lines.append(line)

            with open(cold_loader_path, "w") as f:
                f.writelines(lines)

            log_manager.log_message(f"Updated ColdClientLoader.ini with Exe={relative_exe} and AppId={appid}")

        except Exception as e:
            print(f"Error updating ColdClientLoader.ini: {e}")

    def _process_game(self):
        if self.processing_step == 1:
            if not self.selected_file:
                if self.winfo_exists():
                    show_custom_dialog(self, "warning", "Error", "Please select a game executable first")
                return

            try:
                self.selected_emulator = self.emu_var.get()
                if self.winfo_exists():
                    self.after(0, lambda: self.platform_label.pack_forget())
                    self.after(0, lambda: self.emu_label.pack_forget())
                    self.after(0, lambda: self.emu_dropdown.pack_forget())

                temp_file = TEMP_FOLDER / f"{self.current_html_path.name}.txt"
                if not temp_file.exists():
                    raise FileNotFoundError("No temp file found")

                game_dir = None
                for line in temp_file.read_text().splitlines():
                    if line.startswith("GAMEDIR="):
                        game_dir = Path(line.split("=", 1)[1].strip())
                        break
     
                if game_dir:
                    self._update_gpfile(game_dir, self.selected_file)

                gpfile = game_dir / ".gpfile"
                with gpfile.open() as f:
                    platform = next(line.split("=",1)[1].strip() for line in f if line.startswith("PLATFORM="))

                if platform == "Windows":
                    new_text = "Drop or select a steam_api.dll"
                elif platform == "Linux":
                    new_text = "Drop or select a libsteam_api.so"
            
                self.drop_label.config(text=new_text)
                self.processing_step = 2

            except Exception as e:
                if self.winfo_exists():
                    show_custom_dialog(self, "error", "Error", f"Initial setup failed: {str(e)}")

        elif self.processing_step == 2:
            try:
                temp_file = TEMP_FOLDER / f"{self.current_html_path.name}.txt"
                game_dir = Path(temp_file.read_text().split("GAMEDIR=",1)[1].split("\n",1)[0].strip())
                gpfile = game_dir / ".gpfile"

                app_id = None
                if temp_file.exists():
                    for line in temp_file.read_text().splitlines():
                        if line.startswith("appid="):
                            app_id = line.split("=", 1)[1].strip()
                            break

                app_id = None
                if temp_file.exists():
                    for line in temp_file.read_text().splitlines():
                        if line.startswith("appid="):
                            app_id = line.split("=", 1)[1].strip()
                            break

                gp_data = {}
                platform = None
                existing_arch = None
                if gpfile.exists():
                    with gpfile.open() as f:
                        for line in f:
                            if '=' in line:
                                key, value = line.strip().split('=', 1)
                                gp_data[key] = value
                                if key == "PLATFORM":
                                    platform = value
                                if key == "ARCHITECTURE":
                                    existing_arch = value

                arch = None
                if platform == "Linux" and not existing_arch:
                    arch = self._detect_architecture(self.selected_file)
            
                    if not arch:
                        if not self.arch_var.get():
                            self.arch_frame.pack(pady=10)
                            self.arch_label.pack(side="left", padx=(0, 10))
                            self.arch_dropdown.pack(side="left")
                            return
                        else:
                            arch = 'x86' if self.arch_var.get() == '32 Bit' else 'x86_64'

                if arch and not existing_arch:
                    content = []
                    if gpfile.exists():
                        with gpfile.open('r') as f:
                            content = f.readlines()

                    content = [line for line in content if not line.startswith("ARCHITECTURE=")]
            
                    arch_value = arch if arch else ('x86' if self.arch_var.get() == '32 Bit' else 'x86_64')
                    if content and not content[-1].endswith('\n'):
                        content.append('\n')
                    content.append(f"ARCHITECTURE={arch_value}\n")

                    with gpfile.open('w') as f:
                        f.writelines(content)

                    gp_data["ARCHITECTURE"] = arch

                arch_value = existing_arch or arch
                if not arch_value:
                    arch_value = 'x86' if self.arch_var.get() == '32 Bit' else 'x86_64'

                arch_dir = "x32" if arch_value in ["x86", "32"] else "x64"
                emulator = self.selected_emulator.lower()
                version = getattr(self, 'selected_version', self.version_dropdown_helper.get_selected_version())
                base_dir = APP_FOLDER / emulator / version if version else APP_FOLDER / emulator

                success = self._generate_interface_file(game_dir, gp_data)
                if not success:
                    return

                if platform == "Windows":
                    selected_arch = arch_value
                    api_file = self.selected_file
                    api_dir = api_file.parent

                    opposite_arch = "x86_64" if selected_arch == "x86" else "x86"
                    selected_dir   = "x64" if selected_arch == "x86_64" else "x32"
                    opposite_dir   = "x64" if opposite_arch == "x86_64" else "x32"
                    steam_api_sel   = "steam_api64.dll" if selected_arch == "x86_64" else "steam_api.dll"
                    steamclient_sel = "steamclient64.dll" if selected_arch == "x86_64" else "steamclient.dll"
                    steam_api_opp   = "steam_api64.dll" if opposite_arch == "x86_64" else "steam_api.dll"
                    steamclient_opp = "steamclient64.dll" if opposite_arch == "x86_64" else "steamclient.dll"

                    version = getattr(self, 'selected_version', self.version_dropdown_helper.get_selected_version())
                    base_dir = APP_FOLDER / emulator / version if version else APP_FOLDER / emulator
                    src_dir_sel = base_dir / "Windows" / selected_dir
                    for dll_name in (steam_api_sel, steamclient_sel):
                        src = src_dir_sel / dll_name
                        dst = api_dir / dll_name
                        if src.exists():
                            if dst.exists():
                                bak = dst.with_suffix(".dll.bak")
                                bak.unlink(missing_ok=True)
                                dst.rename(bak)
                                log_manager.log_message(f"Backed up {dll_name} to {bak.name}")
                            shutil.copy2(src, dst)
                            print(f"Copied {dll_name} to {api_dir} from {src_dir_sel}")

                    opp_api_exists   = (api_dir / steam_api_opp).exists()
                    opp_client_exists = (api_dir / steamclient_opp).exists()

                    if opp_api_exists or opp_client_exists:
                        src_dir_opp = base_dir / "Windows" / opposite_dir
                        for dll_name, src, dst in (
                            (steam_api_opp,   src_dir_opp / steam_api_opp,   api_dir / steam_api_opp),
                            (steamclient_opp, src_dir_opp / steamclient_opp, api_dir / steamclient_opp),):
                            if src.exists():
                                if dst.exists():
                                    bak = dst.with_suffix(".dll.bak")
                                    bak.unlink(missing_ok=True)
                                    dst.rename(bak)
                                    log_manager.log_message(f"Backed up {dll_name} to {bak.name}")
                                shutil.copy2(src, dst)
                                print(f"Copied {dll_name} to {api_dir} from {src_dir_opp}")

                    steam_settings_src = game_dir / "steam_settings"
                    if steam_settings_src.is_dir():
                        dest_steam_settings = api_dir / "steam_settings"
                        if dest_steam_settings.exists():
                            bak_dir = dest_steam_settings.with_name(dest_steam_settings.name + ".bak")
                            if bak_dir.is_dir():
                                shutil.rmtree(bak_dir, ignore_errors=True)
                            else:
                                bak_dir.unlink(missing_ok=True)

                            dest_steam_settings.rename(bak_dir)
                            log_manager.log_message(f"Backed up existing steam_settings → {bak_dir.name}")

                        shutil.copytree(steam_settings_src, dest_steam_settings, dirs_exist_ok=True)
                        log_manager.log_message(f"Copied whole steam_settings folder to {dest_steam_settings}")

                    old_dir = base_dir / "Windows" / "old"
                    steam_dll_src = old_dir / "Steam.dll"
                    steam_dll_dest = api_dir / "Steam.dll"

                    if steam_dll_dest.exists():
                        steam_dll_dest_bak = steam_dll_dest.with_suffix(".bak")
                        steam_dll_dest_bak.replace(steam_dll_dest)
                        log_manager.log_message(f"Renamed existing Steam.dll to Steam.dll.bak in {api_dir}")

                        shutil.copy2(steam_dll_src, steam_dll_dest)
                        log_manager.log_message(f"Copied Steam.dll from old folder to {api_dir}")

                    loader_suffix = "x64" if arch_value.lower() in ("x86_64", "64") else "x32"
                    client_src = base_dir / "Windows" / "client"
                    for item in client_src.iterdir():
                        if item.is_file() and not item.name.endswith('.bak'):
                            if "steamclient_loader" in item.name:
                                if f"_{loader_suffix}" in item.name:
                                    dest = game_dir / item.name
                                    shutil.copy2(item, dest)
                            else:
                                dest = game_dir / item.name
                                shutil.copy2(item, dest)

                    extra_dlls_src = client_src / "extra_dlls"
                    if extra_dlls_src.exists():
                        extra_dlls_dest = game_dir / "extra_dlls"
                        extra_dlls_dest.mkdir(exist_ok=True)
                        for dll in extra_dlls_src.glob("*.dll"):
                            if not dll.name.endswith('.bak'):
                                shutil.copy2(dll, extra_dlls_dest)

                    try:
                        temp_file = TEMP_FOLDER / f"{self.current_html_path.name}.txt"
                        appid = None
                        if temp_file.exists():
                            for line in temp_file.read_text().splitlines():
                                if line.startswith("appid="):
                                    appid = line.split("=",1)[1].strip()
                                    break
        
                        if appid:
                            gpfile = game_dir / ".gpfile"
                            exe_path = None
                            if gpfile.exists():
                                for line in gpfile.read_text().splitlines():
                                    if line.startswith("EXE_PATH="):
                                        exe_path = Path(line.split("=",1)[1].strip())
                                        break
            
                            if exe_path and exe_path.exists():
                                self._update_cold_loader_ini(game_dir, exe_path, appid)
                    except Exception as e:
                        log_manager.log_message(f"Error preparing ColdClientLoader update: {e}")

                    try:
                       gpfile = game_dir / ".gpfile"
                       exe_path = None
                       if gpfile.is_file():
                           for line in gpfile.read_text(encoding="utf-8").splitlines():
                               if line.startswith("EXE_PATH="):
                                   exe_path = Path(line.split("=", 1)[1].strip())
                                   break

                       if exe_path is None or not exe_path.is_file():
                           raise RuntimeError("Executable path not found in .gpfile")
                       real_game_root = exe_path.parent

                       if exe_path.parent == api_file.parent:
                           game_subfolder = real_game_root / "game"
                           game_subfolder.mkdir(parents=True, exist_ok=True)

                           log_manager.log_message(f"Creating game folder at: {game_subfolder}")
                           for item in real_game_root.iterdir():
                               if item == game_subfolder:
                                   continue
                               try:
                                   shutil.move(str(item), str(game_subfolder / item.name))
                                   log_manager.log_message(f"Moved {item.name} to {game_subfolder}")
                               except Exception as e:
                                   print(f"Could not move {item.name}: {e}")

                       cold_ini = game_dir / "ColdClientLoader.ini"
                       if cold_ini.is_file():
                           try:
                              if exe_path.parent == api_file.parent:
                                  rel_exe = f"game\\{exe_path.name}"
                              else:
                                  rel_exe = exe_path.relative_to(real_game_root).as_posix().replace("/", "\\")

                              new_lines = []
                              for line in cold_ini.read_text(encoding="utf-8").splitlines():
                                  if line.startswith("Exe="):
                                      new_lines.append(f"Exe={rel_exe}")
                                  else:
                                      new_lines.append(line)
                              cold_ini.write_text("\n".join(new_lines), encoding="utf-8")
                           except Exception as e:
                               print(f"Could not patch ColdClientLoader.ini: {e}")

                       loader_path = None
                       for cand in ("steamclient_loader_x64.exe", "steamclient_loader_x32.exe"):
                           p = game_dir / cand
                           if p.is_file():
                               loader_path = p
                               break

                       launcher_path = None
                       if loader_path:
                           game_name = real_game_root.name
                           launcher_name = f"{real_game_root.name} Launcher.exe"
                           launcher_path = game_dir / launcher_name
                           try:
                               if loader_path.name != launcher_name:
                                   if launcher_path.exists():
                                       launcher_path.unlink()
                                   loader_path.rename(launcher_path)
                                   log_manager.log_message(f"Renamed loader to {launcher_name}")
                               else:
                                   print(f"Loader already named {launcher_name}")
                           except Exception as e:
                               print(f"Could not rename loader exe: {e}")
                       else:
                           launcher_path = None

                       if exe_path and exe_path.exists():
                           real_game_root = exe_path.parent

                       files_to_copy = [cold_ini, game_dir / "GameOverlayRenderer.dll", game_dir / "GameOverlayRenderer64.dll", game_dir / "steamclient.dll", game_dir / "steamclient64.dll", launcher_path]

                       for src in files_to_copy:
                           if src and src.is_file():
                               try:
                                   dest = real_game_root / src.name
                                   shutil.copy2(str(src), str(dest))
                                   log_manager.log_message(f"Copied {src.name} to {real_game_root}")
                               except Exception as e:
                                   print(f"Could not copy {src.name} to {real_game_root}: {e}")

                    except Exception as e:
                        print(f"Additional Windows‑specific post‑processing failed: {e}")

                elif platform == "Linux":
                    lbp_path = Path(gp_data["LBP_PATH"])
                    lib_dir = lbp_path.parent
                    steam_settings_src = game_dir / "steam_settings"
                    dest_steam_settings = lbp_path.parent / "steam_settings"

                    linux_files = ["libsteam_api.so", "steamclient.so"]
                    lbp_name = gp_data.get("LBP_NAME", "")

                    if lbp_name and lbp_name.endswith('.so') and lbp_name not in linux_files:
                        linux_files.append(lbp_name)

                    for file_name in linux_files:
                        file_path = lib_dir / file_name
                        if file_path.exists():
                            bak_path = file_path.with_suffix(".so.bak")
                            if bak_path.exists():
                                bak_path.unlink()
                            file_path.rename(bak_path)
                            log_manager.log_message(f"Backed up {file_name} to {bak_path.name}")

                    if dest_steam_settings.exists():
                        bak_dir = dest_steam_settings.with_name(dest_steam_settings.name + ".bak")

                        if bak_dir.is_dir():
                            shutil.rmtree(bak_dir, ignore_errors=True)
                        else:
                            bak_dir.unlink(missing_ok=True)

                        dest_steam_settings.rename(bak_dir)
                        log_manager.log_message(f"Backed up existing steam_settings → {bak_dir.name}")

                    if steam_settings_src.is_dir():
                        shutil.copytree(steam_settings_src, dest_steam_settings, dirs_exist_ok=True)
                        log_manager.log_message(f"Copied whole steam_settings folder to {dest_steam_settings}")

                    src_dir = base_dir / "Linux" / arch_dir
                    for item in src_dir.iterdir():
                        if item.is_file() and not item.name.endswith('.bak'):
                            dest = lib_dir / item.name
                            shutil.copy2(item, dest)
                            log_manager.log_message(f"Copied {item.name} to {lib_dir}")

                            if lbp_name and lbp_name.endswith('.so') and lbp_name not in ["libsteam_api.so", "steamclient.so"]:

                                standard_name = None
                                if lbp_name.startswith("libsteam_api"):
                                    standard_name = "libsteam_api.so"
                                elif lbp_name.startswith("steamclient"):
                                    standard_name = "steamclient.so"

                                if standard_name and item.name == standard_name:
                                    new_dest = lib_dir / lbp_name
                                    if new_dest.exists():
                                        new_dest.unlink()
                                    dest.rename(new_dest)
                                    log_manager.log_message(f"Renamed {item.name} to {lbp_name}")

                if platform in ["Windows", "Linux"]:
                    if self.winfo_exists():
                        show_custom_dialog(self, "info", "Success", f"{emulator.upper()} files installed")

                self.game_config_frame.pack_forget()
                self.game_config_visible = False
                self.processing_step = 0
                self.selected_file = None
                self.current_html_path = None
                self.arch_frame.pack_forget()

            except Exception as e:
                if self.winfo_exists():
                    show_custom_dialog(self, "error", "Error", f"Installation failed: {str(e)}")

    def _update_gpfile(self, game_dir: Path, exe_path: Path):
        gpfile = game_dir / ".gpfile"

        data = {}    
        if gpfile.exists():
            try:
                with gpfile.open() as f:
                    for line in f:
                        if '=' in line:
                            key, value = line.split('=', 1)
                            data[key.strip()] = value.strip()
            except Exception as e:
                print(f"Error reading .gpfile: {e}")

        if not hasattr(self, 'original_architecture'):
            self.original_architecture = data.get('ARCHITECTURE')

        if self.processing_step == 1:
            data["EXE_PATH"] = str(exe_path.resolve())
            data["EXE_NAME"] = exe_path.name
        elif self.processing_step == 2:
            data["LBP_PATH"] = str(exe_path.resolve())
            data["LBP_NAME"] = exe_path.name

        if self.processing_step == 1:
            data["PLATFORM"] = self.current_platform
    
        arch = self._detect_architecture(exe_path)
        if arch:
            data["ARCHITECTURE"] = arch

        content = "\n".join([f"{key}={value}" for key, value in data.items()])
        try:
            gpfile.write_text(content, encoding="utf-8")
            log_manager.log_message(f"Updated .gpfile at {gpfile}")
        except Exception as e:
            print(f"Error writing .gpfile: {e}")
            raise

    def _detect_architecture(self, exe_path: Path) -> str | None:
        path = exe_path.resolve()
        path_str = str(path).lower()
        name = path.name.lower()

        if name in ("steam_api.dll", "steamclient.dll"):
            return "x86"

        if name in ("steam_api64.dll", "steamclient64.dll"):
            return "x86_64"

        if path.suffix == '.dll':
            if '64' in name or 'x64' in name:
                return 'x86_64'
            if '32' in name or 'x86' in name or 'x32' in name:
                return 'x86'

        if path.suffix == '.so' or path.suffix == '':
            for parent in path.parents:
                parent_str = parent.name.lower()
                if any(kw in parent_str for kw in ["x86_64", "64", "lib64", "amd64"]):
                    return 'x86_64'
                if any(kw in parent_str for kw in ["x86", "32", "lib32", "i386"]):
                    return 'x86'

        if ".x86_64" in path_str or ".x64" in path_str:
            return "x86_64"
        if ".x86" in path_str or ".x32" in path_str:
            return "x86"

        for parent in path.parents:
            parent_str = parent.name.lower()
            if "x86_64" in parent_str or "64" in parent_str:
                return "x86_64"
            if "x86" in parent_str or "32" in parent_str:
                return "x86"

        return None

    # ------------------------------------------------------------
    def _confirm_remove_all(self):
        if not _gui_yes_no("⚠️ WARNING: This will delete ALL HTML files and game folders! Are you absolutely sure?"):
            return
    
        files_to_delete = list(self._row_widgets.keys())
    
        for html_path in files_to_delete:
            temp_txt = TEMP_FOLDER / f"{html_path.name}.txt"

            temp_data: dict[str, str] = {}
            if temp_txt.is_file():
                for line in temp_txt.read_text(encoding="utf-8").splitlines():
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    temp_data[k.strip()] = v.strip()

            if "HTMLFOLDER" in temp_data:
                html_folder_path = OLD_HTML_FOLDER / temp_data["HTMLFOLDER"]
                if html_folder_path.is_dir():
                    try:
                        shutil.rmtree(html_folder_path, ignore_errors=True)
                    except Exception:
                        pass

            if "HTMLFile" in temp_data:
                html_file_path = OLD_HTML_FOLDER / temp_data["HTMLFile"]
                if html_file_path.is_file():
                    try:
                        html_file_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            if {"GAMEDIR", "appid"}.issubset(temp_data):
                game_dir = pathlib.Path(temp_data["GAMEDIR"])
                steam_settings = game_dir / "steam_settings"
                appid_file = steam_settings / "steam_appid.txt"

                hidden_path = game_dir / f".{temp_data['appid']}"
                if hidden_path.is_file():
                    try:
                        shutil.rmtree(game_dir, ignore_errors=True)
                    except Exception:
                        pass
                else:
                    if not appid_file.is_file():
                        try:
                            shutil.rmtree(game_dir, ignore_errors=True)
                        except Exception:
                            pass
                    else:
                        try:
                            stored_appid = appid_file.read_text(encoding="utf-8").strip()
                        except Exception:
                            stored_appid = ""

                        if stored_appid == temp_data["appid"]:
                            try:
                                shutil.rmtree(game_dir, ignore_errors=True)
                            except Exception:
                                pass

            try:
                prog_path = PROGRESS_STATE_FILE
                if prog_path.is_file():
                    prog_data = json.loads(prog_path.read_text(encoding="utf-8"))
                    if html_path.name in prog_data:
                        del prog_data[html_path.name]
                        with prog_path.open("w", encoding="utf-8") as f:
                            json.dump(prog_data, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

            try:
                html_path.unlink(missing_ok=True)
            except Exception:
                pass

            try:
                for candidate in HTML_FOLDER.iterdir():
                    if candidate.is_dir() and candidate.name.startswith(html_path.stem):
                        shutil.rmtree(candidate, ignore_errors=True)
            except Exception:
                pass

            if html_path in self._row_widgets:
                try:
                    self._row_widgets[html_path]["frame"].destroy()
                except Exception:
                    pass
                self._row_widgets.pop(html_path, None)

            if html_path in all_html_files:
                all_html_files.remove(html_path)
            if html_path in file_status:
                file_status.pop(html_path, None)

            try:
                if temp_txt.is_file():
                    temp_txt.unlink(missing_ok=True)
            except Exception:
                pass

        all_html_files.clear()
        file_status.clear()
        self._row_widgets.clear()
    
        with _prompt_handled_lock:
            for html_path in files_to_delete:
                _prompt_handled.pop(html_path, None)
        for html_path in files_to_delete:
            _download_done.pop(html_path, None)

        self.refresh_file_list(all_html_files, file_status)
        self.after(100, self._update_mass_close_btn)

        removed_files = log_manager.load_removed_files()
        for html_path in files_to_delete:
            removed_files.add(html_path.name)
        log_manager.save_removed_files(removed_files)

    # ------------------------------------------------------------
    def _confirm_remove(self, html_path: Path) -> None:
        if not _gui_yes_no(f"Do you really want to delete {html_path.name}?"):
            return

        temp_txt = TEMP_FOLDER / f"{html_path.name}.txt"

        temp_data: dict[str, str] = {}
        if temp_txt.is_file():
            for line in temp_txt.read_text(encoding="utf-8").splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                temp_data[k.strip()] = v.strip()

        if "HTMLFOLDER" in temp_data:
            html_folder_path = OLD_HTML_FOLDER / temp_data["HTMLFOLDER"]
            if html_folder_path.is_dir():
                try:
                    shutil.rmtree(html_folder_path, ignore_errors=True)
                    log_manager.log_message(f"🗑️  Deleted HTML folder {html_folder_path}")
                except Exception as e:
                    print(f"⚠️  Could not delete HTML folder {html_folder_path}: {e}")

        if "HTMLFile" in temp_data:
            html_file_path = OLD_HTML_FOLDER / temp_data["HTMLFile"]
            if html_file_path.is_file():
                try:
                    html_file_path.unlink(missing_ok=True)
                    log_manager.log_message(f"🗑️  Deleted HTML file {html_file_path}")
                except Exception as e:
                    print(f"⚠️  Could not delete HTML file {html_file_path}: {e}")

        if {"GAMEDIR", "appid"}.issubset(temp_data):
            game_dir = pathlib.Path(temp_data["GAMEDIR"])
            steam_settings = game_dir / "steam_settings"
            appid_file = steam_settings / "steam_appid.txt"

            hidden_path = game_dir / f".{temp_data['appid']}"
            if hidden_path.is_file():
                try:
                    shutil.rmtree(game_dir, ignore_errors=True)
                    log_manager.log_message(f"🗑️  Deleted game folder {game_dir} (found hidden .{temp_data['appid']})")
                except Exception as e:
                    print(f"⚠️  Could not delete game folder {game_dir}: {e}")
            else:
                if not appid_file.is_file():
                    try:
                        shutil.rmtree(game_dir, ignore_errors=True)
                        log_manager.log_message(f"🗑️  Deleted game folder {game_dir} (steam_appid.txt missing)")
                    except Exception as e:
                        print(f"⚠️  Could not delete game folder {game_dir}: {e}")
                else:
                    try:
                        stored_appid = appid_file.read_text(encoding="utf-8").strip()
                    except Exception as e:
                        stored_appid = ""
                        print(f"⚠️  Could not read {appid_file}: {e}")

                    if stored_appid == temp_data["appid"]:
                        try:
                            shutil.rmtree(game_dir, ignore_errors=True)
                            log_manager.log_message(f"🗑️  Deleted game folder {game_dir} (appid match)")
                        except Exception as e:
                            print(f"⚠️  Could not delete game folder {game_dir}: {e}")
                    else:
                        try:
                            shutil.rmtree(game_dir, ignore_errors=True)
                            log_manager.log_message(f"🗑️  Deleted game folder {game_dir} (fallback to GAMEDIR from temp file)")
                        except Exception as e:
                            print(f"⚠️  Could not delete game folder {game_dir}: {e}")

        try:
            prog_path = PROGRESS_STATE_FILE
            if prog_path.is_file():
                prog_data = json.loads(prog_path.read_text(encoding="utf-8"))
                if html_path.name in prog_data:
                    del prog_data[html_path.name]
                    with prog_path.open("w", encoding="utf-8") as f:
                        json.dump(prog_data, f, indent=2, ensure_ascii=False)
                    log_manager.log_message(f"🗑️  Removed {html_path.name} from progress.json")
        except Exception as e:
            print(f"⚠️  Could not update progress.json: {e}")

        if isinstance(self.progress_state, dict):
            self.progress_state.pop(html_path.name, None)
        try:
            global progress_state
            if isinstance(progress_state, dict):
                progress_state.pop(html_path.name, None)
        except NameError:
            pass

        try:
            html_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"⚠️  Could not delete HTML file {html_path}: {e}")

        try:
            for candidate in HTML_FOLDER.iterdir():
                if candidate.is_dir() and candidate.name.startswith(html_path.stem):
                    shutil.rmtree(candidate, ignore_errors=True)
        except Exception as e:
            print(f"⚠️  Could not delete side folder(s): {e}")

        if html_path in self._row_widgets:
            self._row_widgets[html_path]["frame"].destroy()
            self._row_widgets.pop(html_path, None)

        all_html_files[:] = [p for p in all_html_files if p != html_path]
        file_status.pop(html_path, None)

        self.refresh_file_list(all_html_files, file_status)
        self.after(100, self._update_mass_close_btn)

        try:
            if temp_txt.is_file():
                temp_txt.unlink(missing_ok=True)
                log_manager.log_message(f"🗑️  Deleted temporary file {temp_txt}")
        except Exception as e:
            print(f"⚠️  Could not delete temporary file {temp_txt}: {e}")

        with _prompt_handled_lock:
            _prompt_handled.pop(html_path, None)
        _download_done.pop(html_path, None)

        removed_files = log_manager.load_removed_files()
        removed_files.add(html_path.name)
        log_manager.save_removed_files(removed_files)

    # ------------------------------------------------------------
    def _on_close(self):
        self._stop_requested = True

        if hasattr(self, 'attention_dialog') and self.attention_dialog.winfo_exists():
            self.attention_dialog.grab_release()
            self.attention_dialog.destroy()
        if hasattr(self, 'stub_removal_frame') and self.stub_removal_frame.winfo_exists():
            self.stub_removal_frame.destroy()
        if hasattr(self, 'stub_removal_options_frame') and self.stub_removal_options_frame.winfo_exists():
            self.stub_removal_options_frame.destroy()
        if hasattr(self, 'tooltip_label') and self.tooltip_label:
            self.tooltip_label.destroy()
        if hasattr(self, 'menu_manager'):
            self.menu_manager.close_all_menus()
        self.destroy()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

global_ui = None
log_manager.set_ui(global_ui)

# ------------------------------------------------------------
def _watch_worker(folder: Path, file_queue: queue.Queue, stop_flag: threading.Event):
    global all_html_files, file_status
    progress_state = log_manager.load_progress_state()
    _progress_path = PROGRESS_STATE_FILE
    _last_mtime = _progress_path.stat().st_mtime if _progress_path.is_file() else 0

    def _progress_reload_json():
        nonlocal progress_state, _last_mtime
        try:
            if _progress_path.is_file():
                cur_mtime = _progress_path.stat().st_mtime
                if cur_mtime != _last_mtime:
                    progress_state = log_manager.load_progress_state()
                    _last_mtime = cur_mtime
        except Exception:
            pass

    while not stop_flag.is_set():
        _progress_reload_json()
        processed = set(all_html_files)
        removed_files = log_manager.load_removed_files()
        current_html = {p for p in folder.iterdir() if p.suffix.lower() == ".html"}
        new_files = current_html - processed

        if new_files:
            removed_files = log_manager.load_removed_files()
            log_manager.log_message(f"Detected new HTML files: {new_files}")
            for new_html in sorted(new_files):
                if new_html.name in removed_files:
                    if _gui_yes_no("You are about to download a game you removed. Would you like to continue?"):
                        removed_files.discard(new_html.name)
                        log_manager.save_removed_files(removed_files)
                        processed.add(new_html)
                        all_html_files.append(new_html)
                        file_status[new_html] = "waiting"
                        job_tracker.add_job()

                        def _process(p):
                            if global_ui is not None:
                                global_ui.start_job()
                            _run_main_in_thread(p)
                            if global_ui is not None:
                                global_ui.finish_job()

                        threading.Thread(target=_process, args=(new_html,), daemon=True).start()
                    else:
                        try:
                            new_html.unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue
                else:
                    processed.add(new_html)
                    all_html_files.append(new_html)
                    file_status[new_html] = "waiting"
                    job_tracker.add_job()

                    def _process(p):
                        if global_ui is not None:
                            global_ui.start_job()
                        _run_main_in_thread(p)
                        if global_ui is not None:
                            global_ui.finish_job()

                    threading.Thread(target=_process, args=(new_html,), daemon=True).start()

            if global_ui is not None:
                global_ui.after(0, global_ui.refresh_file_list, all_html_files, file_status)

        time.sleep(1)

if __name__ == "__main__":
    if GENERAL_SETTINGS.get("auto_update", True):
        log_manager.save_update_check_time()

    if GENERAL_SETTINGS.get("auto_update", True):
        threading.Thread(target=check_for_updates, daemon=True).start()

    if len(sys.argv) > 1 and Path(sys.argv[1]).suffix.lower() == ".html":
        main()
    else:
        APP_FOLDER = pathlib.Path(__file__).resolve().parent / ".app"
        APP_FOLDER.mkdir(parents=True, exist_ok=True)
        HTML_FOLDER = pathlib.Path(__file__).resolve().parent / "HTML"
        HTML_FOLDER.mkdir(parents=True, exist_ok=True)
        TEMP_FOLDER = APP_FOLDER / "temp"
        TEMP_FOLDER.mkdir(parents=True, exist_ok=True)
        GAMES_ROOT = pathlib.Path(__file__).resolve().parent / "Games"
        GAMES_ROOT.mkdir(parents=True, exist_ok=True)

        progress_state = check_existing_completions()

        all_html_files = list(HTML_FOLDER.glob("*.html"))
        
        for html_name, state in progress_state.items():
            if state.get("percent") == 100:
                html_path = HTML_FOLDER / html_name
                if html_path not in all_html_files:
                    all_html_files.append(html_path)
                    file_status[html_path] = "done"
        
        for path in all_html_files:
            if path not in file_status:
                file_status[path] = "waiting"

        file_queue = queue.Queue()
        stop_event = threading.Event()

        try:
            global_ui = WatcherUI(file_queue)
            progress_manager.set_ui(global_ui)
            log_manager._ui = global_ui
            global_ui.refresh_file_list(all_html_files, file_status)
        except Exception as e:
            print(f"Error during GUI initialization: {e}")
            sys.exit(1)

        watcher_thread = threading.Thread(
            target=_watch_worker,
            args=(HTML_FOLDER, file_queue, stop_event),
            daemon=True,
        )
        watcher_thread.start()

    if not VERSION_FILE.exists():
        VERSION_FILE.write_text("v0.5", encoding="utf-8")
    
    DOWNLOADS_FOLDER.mkdir(parents=True, exist_ok=True)

    global_ui.mainloop()
