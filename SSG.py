#!/usr/bin/env python3
import os, sys, re, json, argparse, difflib, pathlib, requests, shutil, subprocess, threading, queue, time, webbrowser, zipfile
from collections import defaultdict
from tkinter import Canvas, Scrollbar, Frame, Label, ttk, Checkbutton, Entry
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urljoin
from bs4 import BeautifulSoup

all_html_files: list[Path] = []
file_status: dict[Path, str] = {}
_prompt_handled = {}
_prompt_handled_lock = threading.Lock()
game_content_lock = defaultdict(threading.Lock)
dlc_lock = threading.Lock()
_download_done: dict[Path, bool] = {}

def _terminal_progress(current: int, total: int) -> None:
    percent = int(current / total * 100)
    filled = int(current / total * 30)
    bar = "·" + "·" * (30 - 1)
    bar = bar[:filled] + "●" + bar[filled + 1 :] if filled < 30 else bar
    sys.stdout.write(f"\r[{bar}] {percent:3d}%")
    sys.stdout.flush()

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
                    print(f"Tried method but got error: {e}")

            print(f"❌ Could not open folder. Path: {path}")
            print("   Tried all known methods. Please open manually.")
    except Exception as e:
        print(f"⚠️ Error opening folder: {e}")

def check_existing_completions() -> dict:
    print("⏳ Checking for existing completed games...")
    progress_state = load_progress_state()
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
                    print(f"✅ Found complete installation for {html_path.name}")
                    
            except Exception as e:
                print(f"⚠️ Error checking {html_path}: {e}")
    
    if updated:
        save_progress_state(progress_state)
        print("💾 Updated progress state with existing completions")
    
    return progress_state

def update_progress(percent: int, html_path: Path) -> None:
    state = load_progress_state(TEMP_FOLDER)
    state[html_path.name] = {"percent": percent}
    save_progress_state(state, TEMP_FOLDER)
    
    if global_ui and hasattr(global_ui, '_row_widgets'):
        def _safe_update():
            if not global_ui.winfo_exists() or html_path not in global_ui._row_widgets:
                return
                
            widgets = global_ui._row_widgets[html_path]
            if widgets["progress"].winfo_exists():
                widgets["progress"]["value"] = percent
            if widgets["percent"].winfo_exists():
                widgets["percent"].config(text=f"{percent}%")
                
            if percent == 100:
                ctrl_btn = widgets.get("ctrl")
                if ctrl_btn and ctrl_btn.winfo_exists():
                    ctrl_btn.destroy()
                    widgets.pop("ctrl", None)
            
            global_ui.update_idletasks()
        
        global_ui.after(0, _safe_update)

global_ui = None
html_path = None

try:
    import tkinter as tk
    from tkinter import messagebox, ttk, Button, Checkbutton, Entry
except Exception:
    tk = None

import shutil

if shutil.which("zenity") is None:
    print(
        "⚠️  'zenity' not found – GUI prompts will fall back to console input "
        "if tkinter cannot open a window."
    )

def _gui_yes_no(question: str) -> bool:
    if tk is None:
        while True:
            resp = input(f"{question} (Y/N): ").strip().lower()
            if resp in ("y", "yes"):
                return True
            if resp in ("n", "no"):
                return False
            print("Please answer Yes or No.")
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

#----------------------------------------------------------------------
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
            progress_cb = _get_progress_cb("", html_path)
            globals()["_terminal_progress"] = progress_cb

        main()
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
VERSION_FILE = APP_FOLDER / "version.txt"
GBE_VERSION_FILE = APP_FOLDER / "gbe.txt"
LATEST_RELEASE_URL = "https://api.github.com/repos/Elite-Alien/Steam-Settings-Generator/releases/latest"
GBE_LATEST_RELEASE_URL = "https://api.github.com/repos/Detanup01/gbe_fork/releases/latest"
GBE_FOLDER = APP_FOLDER / "gbe"
GBE_LINUX = GBE_FOLDER / "Linux"
GBE_LINUX.mkdir(parents=True, exist_ok=True)
GBE_WINDOWS = GBE_FOLDER / "Windows"
GBE_WINDOWS.mkdir(parents=True, exist_ok=True)
GBE_WINDOWS_CLIENT = GBE_WINDOWS / "client"
GBE_WINDOWS_CLIENT.mkdir(parents=True, exist_ok=True)
DOWNLOADS_FOLDER = APP_FOLDER / "downloads"
DOWNLOADS_FOLDER.mkdir(parents=True, exist_ok=True)
TEMP_FOLDER = APP_FOLDER / "temp"
TEMP_FOLDER.mkdir(parents=True, exist_ok=True)
EXTRA_FOLDER = pathlib.Path(__file__).resolve().parent / "Extra"
EXTRA_FOLDER.mkdir(parents=True, exist_ok=True)
PROGRESS_STATE_FILE = APP_FOLDER / "progress.json"
HTML_FOLDER = pathlib.Path(__file__).resolve().parent / "HTML"
HTML_FOLDER.mkdir(parents=True, exist_ok=True)
GAMES_ROOT = pathlib.Path(__file__).resolve().parent / "Games"
GAMES_ROOT.mkdir(parents=True, exist_ok=True)
OLD_HTML_FOLDER = TEMP_FOLDER / "old_html"
OLD_HTML_FOLDER.mkdir(parents=True, exist_ok=True)
TOOLS_FOLDER = APP_FOLDER / "tools"
TOOLS_FOLDER.mkdir(parents=True, exist_ok=True)
USER_CONFIG_FILE = APP_FOLDER / "userconfig.json"
GENERAL_SETTINGS_FILE = APP_FOLDER / "general_settings.json"

# ----------------------------------------------------------------------
def check_for_updates(manual=False, gbe=False):
    config = {
        "version_file": VERSION_FILE if not gbe else GBE_VERSION_FILE,
        "release_url": LATEST_RELEASE_URL if not gbe else GBE_LATEST_RELEASE_URL,
        "auto_setting": "auto_update" if not gbe else "auto_update_gbe",
        "asset_patterns": [r"\.zip$"] if not gbe else [r"linux.*\.tar\.bz2$", r"win.*\.7z$"],
        "success_msg": "Application" if not gbe else "GBE"
    }
    
    if not manual and not GENERAL_SETTINGS.get(config["auto_setting"], True):
        return

    try:
        current_version = ""
        if config["version_file"].exists():
            current_version = config["version_file"].read_text(encoding="utf-8").strip()
            if manual:
                print(f"Current {config['success_msg']} version: {current_version}")

        response = requests.get(config["release_url"], timeout=10)
        response.raise_for_status()
        release_data = response.json()
        latest_tag = release_data["tag_name"]
        
        if manual:
            print(f"Latest {config['success_msg']} version: {latest_tag}")

        if latest_tag != current_version:
            msg = f"New {config['success_msg']} version available: {latest_tag}\n Download and install?"
            if _gui_yes_no(msg):
                assets = []
                patterns = [re.compile(p, re.I) for p in config["asset_patterns"]]
                
                for asset in release_data.get("assets", []):
                    if any(pattern.search(asset["name"]) for pattern in patterns):
                        assets.append(asset)

                if len(assets) < len(config["asset_patterns"]):
                    error_msg = f"Missing required assets for {config['success_msg']} update"
                    raise Exception(error_msg)

                if not gbe:
                    zip_asset = next((a for a in assets if a["name"].endswith('.zip')), None)
                    zip_path = DOWNLOADS_FOLDER / zip_asset["name"]
                    
                    response = requests.get(zip_asset["browser_download_url"], stream=True, timeout=30)
                    response.raise_for_status()
                    with open(zip_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    with zipfile.ZipFile(zip_path, "r") as zip_ref:
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
                            shutil.move(str(item), str(ROOT_DIR))
                        
                        shutil.rmtree(temp_extract, ignore_errors=True)

                    ssg_path = ROOT_DIR / "SSG.py"
                    if ssg_path.exists() and not sys.platform.startswith("win"):
                        os.chmod(ssg_path, 0o755)

                    zip_path.unlink(missing_ok=True)
                    
                    config["version_file"].write_text(latest_tag, encoding="utf-8")
                    
                    if manual:
                        if global_ui is not None:
                            def _restart_prompt():
                                if messagebox.askyesno("Update Complete", "Update installed successfully. Restart now?"):
                                    restart_application()
                            global_ui.after(0, _restart_prompt)
                        else:
                            if _gui_yes_no("Update installed successfully. Restart now?"):
                                restart_application()
                    else:
                        messagebox.showinfo("Update Complete", "Update installed successfully. The application will now restart.")
                        restart_application()

                else:
                    linux_extract = DOWNLOADS_FOLDER / "Linux_Extract"
                    windows_extract = DOWNLOADS_FOLDER / "Windows_Extract"
    
                    release_patterns = [r"^emu-linux-release\.tar\.bz2$", r"^emu-win-release\.7z$"]
                    release_assets = [a for a in assets if any(re.fullmatch(p, a["name"], re.I) for p in release_patterns)]

                    for asset in release_assets:
                        dl_path = DOWNLOADS_FOLDER / asset["name"]
        
                        response = requests.get(asset["browser_download_url"], stream=True)
                        response.raise_for_status()
                        with open(dl_path, "wb") as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
        
                        try:
                            if "linux" in asset["name"].lower():
                                if linux_extract.exists():
                                    shutil.rmtree(linux_extract)
                                linux_extract.mkdir()
                
                                subprocess.run(["tar", "xjf", str(dl_path), "-C", str(linux_extract)])

                                for interface_file in linux_extract.rglob("generate_interfaces_x*"):
                                    if interface_file.is_file() and not interface_file.suffix:
                                        dest = TOOLS_FOLDER / interface_file.name
                                        if dest.exists():
                                            bak_file = dest.with_name(dest.name + '.bak')
                                            if bak_file.exists():
                                                bak_file.unlink()
                                            dest.rename(bak_file)
                                        dest.unlink(missing_ok=True)
                                        shutil.move(str(interface_file), str(dest))
                                        os.chmod(dest, 0o755)

                                for experimental_path in linux_extract.rglob("experimental"):
                                    if experimental_path.is_dir():
                                        print(f"Found experimental directory at: {experimental_path}")
                                        for arch in ["x32", "x64"]:
                                            arch_path = experimental_path / arch
                                            if arch_path.is_dir():
                                                print(f"Checking Linux {arch} folder: {arch_path}")
                                                required_files = {"libsteam_api.so", "steamclient.so"}
                                                found_files = {f.name for f in arch_path.iterdir() if f.is_file()}

                                                alt_names = set()
                                                for fname in found_files:
                                                    alt_name = fname.replace("_x64", "").replace("_x32", "")
                                                    if alt_name in required_files:
                                                        alt_names.add(alt_name)
                                                found_files.update(alt_names)
                
                                                print(f"Files present: {', '.join(found_files)}")
                                                has_all = required_files.issubset(found_files)
                
                                                if has_all:
                                                    dest_dir = GBE_LINUX / arch
                                                    print(f"Moving Linux {arch} files to {dest_dir}")
                                                    try:
                                                        dest_dir.mkdir(parents=True, exist_ok=True)
                                                        for src_file in arch_path.iterdir():
                                                            if src_file.is_file():
                                                                dest_file = dest_dir / src_file.name
                                                                if dest_file.exists():
                                                                    bak_file = dest_file.with_name(dest_file.name + '.bak')
                                                                    if bak_file.exists():
                                                                        bak_file.unlink()
                                                                    dest_file.rename(bak_file)
                                                                shutil.move(str(src_file), str(dest_file))
                                                        print(f"Successfully moved Linux {arch} files")
                                                    except Exception as e:
                                                        print(f"Error moving files: {e}")
                                                else:
                                                    missing = required_files - found_files
                                                    print(f"Missing required: {', '.join(missing)}")

                            elif "win" in asset["name"].lower():
                                if windows_extract.exists():
                                    shutil.rmtree(windows_extract)
                                windows_extract.mkdir()
                
                                if sys.platform.startswith("win"):
                                    subprocess.run(["7z", "x", str(dl_path), f"-o{str(windows_extract)}", "-y"])
                                else:
                                    subprocess.run(["7zr", "x", str(dl_path), f"-o{str(windows_extract)}", "-y"])
                
                                for interface_file in windows_extract.rglob("generate_interfaces_x*.exe"):
                                    dest = TOOLS_FOLDER / interface_file.name
                                    if dest.exists():
                                        bak_file = dest.with_name(dest.name + ".bak")
                                        if bak_file.exists():
                                            bak_file.unlink()
                                        dest.rename(bak_file)
                                    shutil.move(str(interface_file), str(dest))
                                    print(f"Moved {interface_file} to {dest} (Backup: {bak_file})" if dest.exists() else "")

                                for experimental_path in windows_extract.rglob("experimental"):
                                    if experimental_path.is_dir():
                                       print(f"Found experimental directory at: {experimental_path}")
                                       for arch in ["x32", "x64"]:
                                           arch_path = experimental_path / arch
                                           if arch_path.is_dir():
                                               print(f"Checking Windows {arch} folder: {arch_path}")
                                               required = {
                                                   "x32": ["steam_api.dll", "steamclient.dll"],
                                                   "x64": ["steam_api64.dll", "steamclient64.dll"]
                                               }[arch]
                    
                                               found_files = {f.name for f in arch_path.iterdir() if f.is_file()}
                                               alt_names = set()
                                               for fname in found_files:
                                                   alt_name = fname.replace("_x64", "").replace("_x32", "")
                                                   if alt_name in required:
                                                       alt_names.add(alt_name)
                                               found_files.update(alt_names)
                    
                                               print(f"Files present: {', '.join(found_files)}")
                                               has_all = set(required).issubset(found_files)
                    
                                               if has_all:
                                                   dest_dir = GBE_WINDOWS / arch
                                                   print(f"Moving {arch} files to {dest_dir}")
                                                   try:
                                                       dest_dir.mkdir(parents=True, exist_ok=True)
                                                       for src_file in arch_path.iterdir():
                                                           if src_file.is_file():
                                                               dest_file = dest_dir / src_file.name
                                                               if dest_file.exists():
                                                                   bak_file = dest_file.with_name(dest_file.name + '.bak')
                                                                   if bak_file.exists():
                                                                       bak_file.unlink()
                                                                   dest_file.rename(bak_file)
                                                               shutil.move(str(src_file), str(dest_file))
                                                       print(f"Successfully moved Windows {arch} files")
                                                   except Exception as e:
                                                       print(f"Error moving files: {e}")
                                               else:
                                                   missing = set(required) - found_files
                                                   print(f"Missing required: {', '.join(missing)}")

                                steamclient_src = windows_extract / "steamclient_experimental"
                                for steamclient_src in windows_extract.glob("**/steamclient_experimental"):
                                    print(f"Found steamclient_experimental at: {steamclient_src}")
                                    if steamclient_src.is_dir():
                                        client_files = [
                                            "steamclient_loader_x64.exe", "steamclient_loader_x32.exe",
                                            "steamclient64.dll", "steamclient.dll",
                                            "GameOverlayRenderer64.dll", "GameOverlayRenderer.dll",
                                            "ColdClientLoader.ini"
                                        ]
                                        for fname in client_files:
                                            src_file = steamclient_src / fname
                                            if src_file.exists():
                                                dest_file = GBE_WINDOWS_CLIENT / fname
                                                print(f"Moving client file: {src_file} to {dest_file}")
                                                if dest_file.exists():
                                                    bak_file = dest_file.with_name(dest_file.name + '.bak')
                                                    if bak_file.exists():
                                                        bak_file.unlink()
                                                    dest_file.rename(bak_file)
                                                shutil.move(str(src_file), str(dest_file))

                                        extra_dlls = steamclient_src / "extra_dlls"
                                        if extra_dlls.exists():
                                            dest_dlls = GBE_WINDOWS_CLIENT / "extra_dlls"
                                            print(f"Merging extra DLLs from {extra_dlls} to {dest_dlls}")
                                            dest_dlls.mkdir(parents=True, exist_ok=True)
                                            for src_dll in extra_dlls.iterdir():
                                                if src_dll.is_file():
                                                    dest_dll = dest_dlls / src_dll.name

                                                    if dest_dll.exists():
                                                        bak_dll = dest_dll.with_name(dest_dll.name + '.bak')
                                                        if bak_dll.exists():
                                                            bak_dll.unlink()
                                                        dest_dll.rename(bak_dll)
                                                    shutil.move(str(src_dll), str(dest_dll))
                                            try:
                                                extra_dlls.rmdir()
                                            except OSError:
                                                pass
                        finally:
                            dl_path.unlink(missing_ok=True)

                    for folder in [linux_extract, windows_extract]:
                        if folder.exists():
                            shutil.rmtree(folder, ignore_errors=True)

                    config["version_file"].write_text(latest_tag, encoding="utf-8")
    
                    if manual:
                        messagebox.showinfo("GBE Update Complete", "GBE files updated!")
            else:
                print(f"{config['success_msg']} update canceled by user")
        else:
            if manual:
                msg = f"You have the latest {config['success_msg']} version"
                if global_ui is not None:
                    def _show_info():
                        messagebox.showinfo(f"{config['success_msg']} Update Check", msg)
                    global_ui.after(0, _show_info)
                else:
                    messagebox.showinfo(f"{config['success_msg']} Update Check", msg)
            else:
                print(f"You have the latest {config['success_msg']} version")
            
    except Exception as e:
        print(f"⚠️ {config['success_msg']} update failed: {e}")
        if manual:
            error_msg = f"Failed to update {config['success_msg']}: {str(e)}"
            if global_ui is not None:
                def _show_error():
                    messagebox.showerror(f"{config['success_msg']} Update Error", error_msg)
                global_ui.after(0, _show_error)
            else:
                messagebox.showerror(f"{config['success_msg']} Update Error", error_msg)

def restart_application():
    python = sys.executable
    os.execl(python, python, *sys.argv)

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
                print(f"Copied existing image {img_name} from {src_folder}")
            except Exception as e:
                print(f"Could not copy {img_name}: {e}")

    return found


def move_to_old(html_path: Path):
    try:
        if html_path.parent == HTML_FOLDER:
            dest_html = OLD_HTML_FOLDER / html_path.name
            if html_path.exists():
                shutil.move(str(html_path), str(dest_html))
                print(f"🗂️ Moved HTML file to {dest_html}")
            
            folder_name = html_path.stem + "_files"
            src_folder = html_path.parent / folder_name
            if src_folder.exists():
                dest_folder = OLD_HTML_FOLDER / folder_name
                shutil.move(str(src_folder), str(dest_folder))
                print(f"🗂️ Moved associated folder to {dest_folder}")

            progress_state = load_progress_state()
            progress_state[html_path.name] = {"percent": 100}
            save_progress_state(progress_state)

            file_status[html_path] = "done"

            if global_ui:
                global_ui.refresh_file_list(all_html_files, file_status)

    except Exception as e:
        print(f"⚠️ Error moving files to old folder: {e}")

def read_local_file(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

def load_processed_log(folder: Path) -> set:
    return set()

def _hidden_cleanup_needed(html_name: str, processed: set) -> bool:
    return html_name not in processed

def save_processed_log(folder: Path, processed: set) -> None:
    pass

def _load_progress_state_fresh() -> dict:
    return load_progress_state()

def load_progress_state(folder: Path | None = None) -> dict:
    if folder is None:
        folder = TEMP_FOLDER
    file_path = PROGRESS_STATE_FILE
    if not file_path.is_file():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _make_json_serialisable(obj):
    if isinstance(obj, dict):
        return {str(k): _make_json_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_make_json_serialisable(i) for i in obj]
    if isinstance(obj, pathlib.Path):
        return str(obj)
    return obj


def save_progress_state(state: dict, folder: Path | None = None) -> None:
    if folder is None:
        folder = APP_FOLDER
    file_path = PROGRESS_STATE_FILE
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    existing = {}
    if file_path.is_file():
        try:
            existing = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    merged = {**existing, **state}
    serialisable_state = _make_json_serialisable(merged)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(serialisable_state, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass

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
                print(f"Downloaded {file_path.name} to {dest_folder}")
            except Exception as e:
                print(f"Failed {url}: {e}")

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

def _noop_progress(_: int, __: int) -> None:
    pass

def _ui_progress(cur: int, tot: int, html_path: Path, ui: WatcherUI, folder: Path):
    if _prompt_handled.get(html_path, False):
        return
    widgets = ui._row_widgets.get(html_path)
    if not widgets:
        return
    prog = widgets["progress"]
    perc = widgets["percent"]
    prog["maximum"] = tot
    prog["value"] = cur
    percent = int(cur / tot * 100)
    percent_lbl.config(text=f"{percent}%")
    ui.update_idletasks()
    state = _load_progress_state_fresh()
    state[html_path.name] = {"percent": percent}
    save_progress_state(state, folder)

# ----------------------------------------------------------------------
def _choose_progress_cb(app_id: str, html_path: Path) -> callable:
    if sys.stdout.isatty() and global_ui is None:
        return _terminal_progress

def _get_progress_cb(app_id: str, html_path: Path) -> callable:
    if global_ui is not None and hasattr(global_ui, "_row_widgets"):
        def _ui_row_progress(cur: int, tot: int, p=html_path):
            widgets = global_ui._row_widgets.get(p)
            if not widgets:
                return
            if widgets["progress"].winfo_exists():
                widgets["progress"]["maximum"] = tot
                widgets["progress"]["value"] = cur
            if widgets["percent"].winfo_exists():
                widgets["percent"].config(text=f"{int(cur / tot * 100)}%")
            global_ui.update_idletasks()
        return _ui_row_progress

    return _terminal_progress

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
        print(f"File not found: {args.html_path}")
        sys.exit(1)

    html_path = args.html_path
    update_progress(0, html_path)
    if not html_path.is_file():
        old_path = OLD_HTML_FOLDER / html_path.name
        if old_path.is_file():
            html_path = old_path
        else:
            print(f"File not found: {args.html_path}")
            sys.exit(1)

    html_content = read_local_file(str(html_path))
    soup = BeautifulSoup(html_content, "html.parser")
    
    TEMP_FOLDER = APP_FOLDER / "temp"

    app_id = extract_app_id(soup)
    if app_id:
        temp_files = TEMP_FOLDER.glob("*.txt")
        for temp_file in temp_files:
            if temp_file.stem == html_path.stem:
                continue
                
            try:
                for line in temp_file.read_text().splitlines():
                    if line.startswith("appid="):
                        existing_appid = line.split("=", 1)[1].strip()
                        if existing_appid == app_id:
                            print(f"⚠️ App ID {app_id} already processed with a different HTML file. Deleting duplicate.")
                            try:
                                html_path.unlink(missing_ok=True)
                                folder_name = html_path.stem + "_files"
                                folder_path = html_path.parent / folder_name
                                if folder_path.exists():
                                    shutil.rmtree(folder_path, ignore_errors=True)
                                print(f"🗑️ Deleted duplicate HTML file and folder for app ID {app_id}")

                                if html_path in all_html_files:
                                    all_html_files.remove(html_path)
                                if html_path in file_status:
                                    file_status.pop(html_path, None)
                            
                                if global_ui:
                                    global_ui.after(0, global_ui.refresh_file_list, all_html_files, file_status)
                            
                                progress_state = load_progress_state()
                                if html_path.name in progress_state:
                                    del progress_state[html_path.name]
                                    save_progress_state(progress_state)                        
                                return
                            except Exception as e:
                                print(f"⚠️ Error deleting duplicate files: {e}")
                            return
            except Exception:
                continue
    else:
        print("No Steam app‑id found – steam_appid.txt not created.")
        return

    script_dir = pathlib.Path(__file__).resolve().parent
    progress_state = load_progress_state()
    base_folder = GAMES_ROOT / clean_title(soup.find("h1", itemprop="name").text)
    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"
    
    TEMP_FOLDER = APP_FOLDER / "temp"

    processed_folder = script_dir
    progress_state = load_progress_state(processed_folder)

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

    update_progress(20, html_path)

    app_id = extract_app_id(soup)
    if app_id:
        appid_path = steam_settings / "steam_appid.txt"
        try:
            appid_path.write_text(app_id, encoding="utf-8")
            print(f"Steam app‑id written to {appid_path}")
        except Exception as e:
            print(f"Failed to write app‑id file: {e}")

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
            print(f"Failed to create hidden app‑id file {hidden_appid}: {e}")
        try:
            temp_file_path.write_text(temp_content, encoding="utf-8")
        except Exception:
            pass
    else:
        print("No Steam app‑id found – steam_appid.txt not created.")

    update_progress(30, html_path)
    progress_cb = None

    achievements = []
    achievement_divs = soup.find_all(
        "div", id=lambda x: x and x.startswith("achievement-")
    )

    if not achievement_divs:
        print("No achievements found in the provided HTML file.")
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

        update_progress(40, html_path)

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
        print(f"❌ Critical: Missing required icon at {hidden_icon_src}")
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
                if _gui_yes_no("Multiplayer achievements found. Remove them?"):
                    achievements = [a for a in achievements if not a["is_multiplayer"]]
                    update_progress(max(current_progress, 50), html_path)
                _prompt_handled[html_path] = True

            if has_hidden_prefix:
                if not already_done:
                    if _hidden_cleanup_needed(html_path.name, processed_html_names):
                        if _gui_yes_no('Clean descriptions that start with "Hidden achievement:"?'):
                            for a in achievements:
                                if a["description"].startswith("Hidden achievement:"):
                                    a["description"] = a["description"][len("Hidden achievement:"):].lstrip()
                        update_progress(max(current_progress, 50), html_path)
                    else:
                        for a in achievements:
                            if a["description"].startswith("Hidden achievement:"):
                                a["description"] = a["description"][len("Hidden achievement:"):].lstrip()
                        update_progress(max(current_progress, 50), html_path)
                _prompt_handled[html_path] = True

    for a in achievements:
        a.pop("is_multiplayer", None)

    json_path = steam_settings / "achievements.json"
    json_path.write_text(
        json.dumps(achievements, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Achievements JSON written to {json_path}")
     
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
        update_progress(70, html_path)
        print("No similar folder with images found; will download all needed files.")

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
            update_progress(90, html_path)
        else:
            progress_cb = _get_progress_cb(app_id, html_path) or _terminal_progress
            downloaded_cnt = download_images(
                app_id,
                missing_filenames,
                achievement_images,
                progress_cb=progress_cb,
            )
            print(f"Downloaded {len(missing_filenames)} missing image(s) to {achievement_images}")
            update_progress(90, html_path)
            _download_done[html_path] = True
            missing_filenames = []
            
            if progress_cb:
                progress_cb(1, 1)
    elif app_id:
        print("All required images already present - no download needed.")
        update_progress(90, html_path)
        progress_cb = _get_progress_cb(app_id, html_path) or _terminal_progress
        if progress_cb:
            progress_cb(1, 1)
    else:
        print("No Steam app-id found - image download skipped.")
        update_progress(90, html_path)
        progress_cb = _get_progress_cb("", html_path) or _terminal_progress
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
                return
            
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
                    
                print(f"DLC.txt and configs.app.ini written in {steam_settings}")
            else:
                print("No DLC entries found, skipping DLC file creation.")
            
        except Exception as e:
            print(f"⚠️ Error during DLC processing: {e}")

    update_progress(95, html_path)
 
def _wrapped_download(app_id: str, filenames: list[str], dest: Path, cb: callable = _terminal_progress):
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
            print(f"Failed {url}: {e}")

        cb(i, len(filenames))

        state = load_progress_state(TEMP_FOLDER)
        html_path = globals().get("html_path")
        if isinstance(html_path, Path):
            percent = int(i / len(filenames) * 100)
            state[html_path.name] = {"percent": percent}
            save_progress_state(state, TEMP_FOLDER)

    update_progress(100, html_path)

    if html_path.parent == HTML_FOLDER:
        try:
            move_to_old(html_path)
            print(f"🗂️ Moved processed files for {html_path.name} to old_html folder")
        except Exception as e:
            print(f"⚠️ Error moving files to old folder: {e}")

    state = load_progress_state(TEMP_FOLDER)
    state[html_path.name] = {"percent": 100}
    save_progress_state(state, TEMP_FOLDER)

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
                print(f"Error moving files: {e}")

        file_status[html_path] = "done"
        update_progress(100, html_path)

    finally:
        job_tracker.finish_job()
        sys.argv = old_argv

# ------------------------------------------------------------
class SettingsManager:
    def __init__(self, config_file: Path, default_settings: dict):
        self.config_file = config_file
        self.default_settings = default_settings
        self.settings = default_settings.copy()
        self.load()
    
    def load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.settings = {**self.default_settings, **json.load(f)}
        except Exception as e:
            print(f"Error loading {self.config_file.name}: {e}")
            self.settings = self.default_settings.copy()
    
    def save(self):
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"Error saving {self.config_file.name}: {e}")
    
    def get(self, key, default=None):
        return self.settings.get(key, self.default_settings.get(key, default))
    
    def set(self, key, value, autosave=True):
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
        "auto_update_gbe": True
    }
)

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

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

        self.mass_close_btn.config(
            bg=theme['button_bg'],
            fg=theme['fg']
        )
        
        self.settings_btn.config(
            bg=theme['button_bg'],
            fg=theme['fg']
        )

        self.style.configure('TNotebook', background=theme['bg'])
        self.style.configure('TNotebook.Tab', background=theme['widget_bg'], foreground=theme['fg'], lightcolor=theme['border'], borderwidth=0)
        self.style.configure('TNotebook.Tab', background=theme['widget_bg'], foreground=theme['fg'], lightcolor=theme['border'])
        self.style.map('TNotebook.Tab', background=[('selected', theme['widget_bg'])], foreground=[('selected', theme['fg'])])
        self.style.configure('TCombobox', fieldbackground=theme['widget_bg'], background=theme['widget_bg'], foreground=theme['fg'])
        self.style.map('TCombobox', fieldbackground=[('readonly', theme['widget_bg'])], selectbackground=[('readonly', theme['widget_bg'])], selectforeground=[('readonly', theme['fg'])], arrowcolor=[('readonly', theme['fg'])])

        def update_widget_colors(widget):
            try:
                if isinstance(widget, (Frame, Canvas)):
                    widget.config(bg=theme['bg'])
                elif isinstance(widget, (Label, Checkbutton)):
                    widget.config(bg=theme['bg'], fg=theme['fg'])
                elif isinstance(widget, Entry):
                    widget.config(
                        bg=theme['widget_bg'],
                        fg=theme['fg'],
                        insertbackground=theme['fg']
                    )
                elif isinstance(widget, Checkbutton):
                    widget.config(
                        bg=theme['bg'],
                        fg=theme['fg'],
                        activebackground=theme['bg'],
                        activeforeground=theme['fg'],
                        selectcolor=theme['widget_bg']
                    )
            
                if isinstance(widget, ttk.Combobox):
                    widget.config(style='TCombobox')

            except Exception as e:
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
            btn.config(
                bg=theme['button_bg'],
                fg=theme['fg'],
                activebackground=theme['active_bg']
            )

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

        self.settings_frame.config(height=500, bg=theme['bg'])
        self.settings_frame.pack_propagate(False)

        self.configure(bg=theme['bg'])
        self.counter_label.config(bg=theme['bg'], fg=theme['fg'])
        self.list_frame.config(bg=theme['bg'])
        self.canvas.config(bg=theme['bg'])
        self.inner_frame.config(bg=theme['bg'])
        self.scrollbar.config(
            bg=theme['widget_bg'],
            troughcolor=theme['bg']
        )
        
        self.theme_btn.config(
            text='🌞' if self.dark_mode else '🌚',
            bg=theme['button_bg'],
            fg=theme['fg']
        )
        
        self.mass_close_btn.config(
            bg=theme['button_bg'],
            fg=theme['fg']
        )

        for path, widgets in self._row_widgets.items():
            if widgets['frame'].winfo_exists():
                widgets['frame'].config(
                    bg=theme['widget_bg'], 
                    highlightbackground=theme['border']
                )
            if widgets['top_frame'].winfo_exists():
                widgets['top_frame'].config(bg=theme['widget_bg'])
            if widgets['bottom_frame'].winfo_exists():
                widgets['bottom_frame'].config(bg=theme['widget_bg'])
            if widgets['progress'].winfo_exists():
                widgets['progress'].configure(style=f'{theme["progress"]}.Horizontal.TProgressbar')
            if widgets['percent'].winfo_exists():
                widgets['percent'].config(
                    bg=theme['widget_bg'],
                    fg=theme['fg']
                )
            if widgets['name_label'].winfo_exists():
                widgets['name_label'].config(
                    bg=theme['widget_bg'],
                    fg=theme['fg']
                )
            if widgets['path_label'].winfo_exists():
                widgets['path_label'].config(
                    bg=theme['button_bg'],
                    fg=theme['fg']
                )
            if widgets['attention_btn'].winfo_exists():
                widgets['attention_btn'].config(
                    bg=theme['button_bg'],
                    fg=theme['fg']
                )
            if widgets['close_btn'].winfo_exists():
                widgets['close_btn'].config(
                    bg=theme['button_bg'],
                    fg=theme['fg']
                )

    def _toggle_auto_update(self, gbe=False):
        if gbe:
            self.general_settings.set("auto_update_gbe", self.auto_update_gbe_var.get())
        else:
            self.general_settings.set("auto_update", self.auto_update_var.get())
        
        self._update_manual_btn_visibility()

    def _update_manual_btn_visibility(self):
        auto_update = self.general_settings.get("auto_update", True)
        self.manual_update_btn.config(state=tk.DISABLED if auto_update else tk.NORMAL)

        auto_update_gbe = self.general_settings.get("auto_update_gbe", True)
        self.manual_update_gbe_btn.config(state=tk.DISABLED if auto_update_gbe else tk.NORMAL)

    def toggle_settings_menu(self):
        if self.settings_frame.winfo_ismapped():
            self.settings_btn.lift()
            self.settings_frame.pack_forget()
            self.settings_btn.config(text="⚙️")
        else:
            self.settings_frame.pack(fill="both", expand=True)
            self.settings_btn.config(text="❌")
            self.populate_settings()

    def populate_settings(self):
        for widget in self.settings_frame.winfo_children():
            widget.destroy()
        
        theme = self.DARK_THEME if self.dark_mode else self.LIGHT_THEME
        
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

        general_container = Frame(self.general_tab, bg=theme['bg'])
        general_container.pack(pady=10, padx=20, fill="x")

        update_frame = Frame(general_container, bg=theme['bg'])
        update_frame.pack(fill="x", pady=5)
        
        self.auto_update_var = tk.BooleanVar(value=self.general_settings.get("auto_update", True))
        Checkbutton(
            update_frame,
            text="Automatic Update Check",
            variable=self.auto_update_var,
            command=lambda: self._toggle_auto_update(gbe=False),
            bg=theme['bg'],
            fg=theme['fg'],
            activebackground=theme['bg'],
            activeforeground=theme['fg'],
            selectcolor=theme['widget_bg']
        ).pack(side="left")
        
        self.manual_update_btn = Button(
            update_frame,
            text="Manual Update",
            command=lambda: threading.Thread(target=check_for_updates, args=(True, False), daemon=True).start(),
            bg=theme['button_bg'],
            fg=theme['fg'],
            state=tk.NORMAL if not self.auto_update_var.get() else tk.DISABLED
        )
        self.manual_update_btn.pack(side="right")
        
        self.downgrade_btn = Button(
            update_frame,
            text="Downgrade",
            command=lambda: self.downgrader("app"),
            bg=theme['button_bg'],
            fg=theme['fg'],
            state=tk.NORMAL if not self.auto_update_var.get() else tk.DISABLED
        )
        self.downgrade_btn.pack(side="right", padx=10)

        gbe_frame = Frame(general_container, bg=theme['bg'])
        gbe_frame.pack(fill="x", pady=5)
    
        self.auto_update_gbe_var = tk.BooleanVar(value=self.general_settings.get("auto_update_gbe", True))
        Checkbutton(
            gbe_frame,
            text="Automatic Update GBE",
            variable=self.auto_update_gbe_var,
            command=lambda: self._toggle_auto_update(gbe=True),
            bg=theme['bg'],
            fg=theme['fg'],
            activebackground=theme['bg'],
            activeforeground=theme['fg'],
            selectcolor=theme['widget_bg']
        ).pack(side="left")
    
        self.manual_update_gbe_btn = Button(
            gbe_frame,
            text="Manual Update",
            command=lambda: threading.Thread(target=check_for_updates, args=(True, True), daemon=True).start(),
            bg=theme['button_bg'],
            fg=theme['fg'],
            state=tk.NORMAL if not self.auto_update_gbe_var.get() else tk.DISABLED
        )
        self.manual_update_gbe_btn.pack(side="right")

        self.downgrade_gbe_btn = Button(
            gbe_frame,
            text="Downgrade",
            command=lambda: self.downgrader("gbe"),
            bg=theme['button_bg'],
            fg=theme['fg'],
            state=tk.NORMAL if not self.auto_update_gbe_var.get() else tk.DISABLED
        )
        self.downgrade_gbe_btn.pack(side="right", padx=10)

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

    def downgrader(self, target: str = "gbe"):
        if target == "app":
            if not _gui_yes_no("Downgrade the application to the previous version?"):
                return

            try:
                current_version = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "v0.0"
                releases = requests.get(LATEST_RELEASE_URL).json()
                versions = [r['tag_name'] for r in releases.get('releases', [])]
                current_index = versions.index(current_version) if current_version in versions else -1
                if current_index <= 0:
                    messagebox.showinfo("Downgrade", "No older version available")
                    return
                
                target_version = versions[current_index - 1]
                asset = next((a for a in releases['assets'] if a['name'].endswith('.zip')), None)
                if not asset:
                    raise Exception("No zip asset found for version")

                zip_path = DOWNLOADS_FOLDER / asset['name']
                with requests.get(asset['browser_download_url'], stream=True) as r:
                    with open(zip_path, 'wb') as f:
                        shutil.copyfileobj(r.raw, f)

                with zipfile.ZipFile(zip_path) as zip_ref:
                    zip_ref.extractall(ROOT_DIR)

                zip_path.unlink()
                VERSION_FILE.write_text(target_version)
                messagebox.showinfo("Downgrade", f"Reverted to version {target_version}\n Restarting...")
                restart_application()

            except Exception as e:
                messagebox.showerror("Downgrade Failed", str(e))

        elif target == "gbe":
            if not _gui_yes_no("Do you want to downgrade GBE files?"):
                return

            target_folders = [TOOLS_FOLDER, GBE_FOLDER]
            try:
                for folder in target_folders:
                    if not folder.exists():
                        continue
                    for file_path in folder.rglob('*'):
                        if file_path.is_file() and not file_path.name.endswith('.bak'):
                            try:
                                file_path.unlink()
                            except Exception as e:
                                print(f"Error removing {file_path}: {e}")

                    for file_path in folder.rglob('*.bak'):
                        try:
                            new_name = file_path.with_name(file_path.name[:-4])
                            file_path.rename(new_name)
                        except Exception as e:
                            print(f"Error restoring {file_path}: {e}")

                if GBE_VERSION_FILE.exists():
                    GBE_VERSION_FILE.unlink()
                messagebox.showinfo("Downgrade Complete", "GBE files restored from backups")
            except Exception as e:
                messagebox.showerror("Downgrade Error", f"Failed: {str(e)}")

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
        self.dark_mode = False

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
        self.geometry("800x800")
        self.resizable(False, False)

        self.mass_close_btn = Button(
            self,
            text="🗳",
            font=('Arial', 8),
            command=self._confirm_remove_all,
            bd=0,
            relief='flat',
            bg=self.LIGHT_THEME['button_bg'],
            fg=self.LIGHT_THEME['fg']
        )
        self.mass_close_btn.place(x=20, y=10)

        self.progress_state = progress_state
        self.file_queue = file_queue
        self._busy = False

        self.theme_btn = Button(
            self,
            text='🌞',
            font=('Arial', 8),
            command=self.toggle_theme,
            bd=0,
            relief='flat',
            bg=self.DARK_THEME['button_bg'],
            fg=self.DARK_THEME['fg']
        )
        self.theme_btn.place(x=690, y=10)

        self.settings_btn = Button(
            self,
            text="⚙️",
            font=('Arial', 8),
            command=self.toggle_settings_menu,
            bd=0,
            relief='flat',
            bg=self.LIGHT_THEME['button_bg'],
            fg=self.LIGHT_THEME['fg']
        )
        self.settings_btn.place(x=735, y=10)

        self.settings_frame = Frame(self)
        self.settings_frame.pack_propagate(False)

        self.counter_label = tk.Label(self, text="Job Count: 0", font=("Helvetica", 12))
        self.counter_label.pack(pady=(10, 0))

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

        self._update_mass_close_btn()

        self.after(300, self._refresh_counter)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._stop_requested = False

        self.inner_frame.grid_columnconfigure(0, weight=1)
        self.inner_frame.grid_rowconfigure(len(all_html_files), minsize=5)

        self.inner_frame.bind("<Configure>", self._update_scroll_region)

        self.user_settings = USER_SETTINGS
        self.general_settings = GENERAL_SETTINGS
        self.dark_mode = self.general_settings.get("dark_mode", False)
        self.user_config = USER_SETTINGS

        self.toggle_theme()

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

        lbl = tk.Label(container, font=("Helvetica", 11, "bold"),
                       anchor="w")
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

                current_progress_state = load_progress_state()

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
                            bg=current_theme['button_bg']
                        )
                        path_lbl.pack(side="top", pady=2)
                        path_lbl.bind("<Button-1>", lambda e, p=game_folder_path: _open_folder(p))

                        self.progress_state = load_progress_state()

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
    
        self.refresh_file_list(all_html_files, file_status)
        self.after(100, self._update_mass_close_btn)

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
                    print(f"🗑️  Deleted HTML folder {html_folder_path}")
                except Exception as e:
                    print(f"⚠️  Could not delete HTML folder {html_folder_path}: {e}")

        if "HTMLFile" in temp_data:
            html_file_path = OLD_HTML_FOLDER / temp_data["HTMLFile"]
            if html_file_path.is_file():
                try:
                    html_file_path.unlink(missing_ok=True)
                    print(f"🗑️  Deleted HTML file {html_file_path}")
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
                    print(f"🗑️  Deleted game folder {game_dir} (found hidden .{temp_data['appid']})")
                except Exception as e:
                    print(f"⚠️  Could not delete game folder {game_dir}: {e}")
            else:
                if not appid_file.is_file():
                    try:
                        shutil.rmtree(game_dir, ignore_errors=True)
                        print(f"🗑️  Deleted game folder {game_dir} (steam_appid.txt missing)")
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
                            print(f"🗑️  Deleted game folder {game_dir} (appid match)")
                        except Exception as e:
                            print(f"⚠️  Could not delete game folder {game_dir}: {e}")
                    else:
                        try:
                            shutil.rmtree(game_dir, ignore_errors=True)
                            print(
                                f"🗑️  Deleted game folder {game_dir} (fallback to GAMEDIR from temp file)"
                            )
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
                    print(f"🗑️  Removed {html_path.name} from progress.json")
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
                print(f"🗑️  Deleted temporary file {temp_txt}")
        except Exception as e:
            print(f"⚠️  Could not delete temporary file {temp_txt}: {e}")

    # ------------------------------------------------------------
    def _on_close(self):
        self._stop_requested = True
        self.destroy()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

global_ui = None

# ------------------------------------------------------------
def _watch_worker(folder: Path, file_queue: queue.Queue, stop_flag: threading.Event):
    global all_html_files, file_status
    processed: set[Path] = set(all_html_files)

    progress_state = load_progress_state()
    _progress_path = PROGRESS_STATE_FILE
    _last_mtime = _progress_path.stat().st_mtime if _progress_path.is_file() else 0

    def _progress_reload_json():
        nonlocal progress_state, _last_mtime
        try:
            if _progress_path.is_file():
                cur_mtime = _progress_path.stat().st_mtime
                if cur_mtime != _last_mtime:
                    progress_state = load_progress_state()
                    _last_mtime = cur_mtime
        except Exception:
            pass

    while not stop_flag.is_set():
        _progress_reload_json()
        current_html = {p for p in folder.iterdir() if p.suffix.lower() == ".html"}
        new_files = current_html - processed

        if new_files:
            print(f"Detected new HTML files: {new_files}")
            for new_html in sorted(new_files):
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
        VERSION_FILE.write_text("v0.3", encoding="utf-8")
    
    DOWNLOADS_FOLDER.mkdir(parents=True, exist_ok=True)

    global_ui.mainloop()
