#!/usr/bin/env python3
import os, sys, re, json, argparse, difflib, pathlib, requests, shutil, subprocess, threading, queue, time, webbrowser
from tkinter import Canvas, Scrollbar, Frame, Label, PhotoImage
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urljoin
from bs4 import BeautifulSoup

all_html_files: list[Path] = []
file_status: dict[Path, str] = {}
_prompt_handled: dict[Path, bool] = {}
_download_done: dict[Path, bool] = {}

def _terminal_progress(current: int, total: int) -> None:
    percent = int(current / total * 100)
    filled = int(current / total * 30)
    bar = "·" + "·" * (30 - 1)
    bar = bar[:filled] + "●" + bar[filled + 1 :] if filled < 30 else bar
    sys.stdout.write(f"\r[{bar}] {percent:3d}%")
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")
        temp_dir = pathlib.Path(__file__).resolve().parent / ".temp"
        state = load_progress_state(temp_dir)
        html_path = globals().get("html_path")
        if html_path:
            state[html_path.name] = {"percent": 100}
            save_progress_state(state, temp_dir)

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

global_ui = None
html_path = None

try:
    import tkinter as tk
    from tkinter import messagebox, ttk, Button
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
    r'<link\s+rel=["\']canonical["\']\s+href=["\']https?://steamdb\.info/app/(\d+)/stats/["\']',
    re.IGNORECASE,
)
IMG_PATTERN = re.compile(r'([a-f0-9]{40})\.jpg', re.IGNORECASE)

APP_URL_TEMPLATE = "https://shared.fastly.steamstatic.com/community_assets/images/apps/{app_id}/"
TEMP_FOLDER = pathlib.Path(__file__).resolve().parent / ".temp"
TEMP_FOLDER.mkdir(parents=True, exist_ok=True)
PROGRESS_STATE_FILE = TEMP_FOLDER / "progress.json"
HTML_FOLDER = pathlib.Path(__file__).resolve().parent / "HTML"
HTML_FOLDER.mkdir(parents=True, exist_ok=True)
GAMES_ROOT = pathlib.Path(__file__).resolve().parent / "Games"
GAMES_ROOT.mkdir(parents=True, exist_ok=True)


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
        folder = pathlib.Path(__file__).resolve().parent / ".temp"
    file_path = folder / "progress.json"
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
        folder = pathlib.Path(__file__).resolve().parent / ".temp"
    file_path = folder / "progress.json"
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
        m = re.search(r"/app/(\d+)/stats/?", link_tag["href"], re.IGNORECASE)
        if m:
            return m.group(1)

    m = HTML_PATTERN.search(str(soup))
    if m:
        return m.group(1)

    return None


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
    temp_dir = pathlib.Path(__file__).resolve().parent / ".temp"

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

        html_path = globals().get("html_path")
        if isinstance(html_path, Path):
            percent = int(i / total * 100)
            state = load_progress_state(temp_dir)
            state[html_path.name] = {"percent": percent}
            save_progress_state(state, temp_dir)

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
    perc.config(text=f"{percent}%")
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
            prog = widgets["progress"]
            perc = widgets["percent"]
            prog["maximum"] = tot
            prog["value"] = cur
            perc.config(text=f"{int(cur / tot * 100)}%")
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
    html_content = read_local_file(str(html_path))
    soup = BeautifulSoup(html_content, "html.parser")

    script_dir = pathlib.Path(__file__).resolve().parent
    progress_state = load_progress_state()
    base_folder = GAMES_ROOT / clean_title(soup.find("h1", itemprop="name").text)
    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"
    
    TEMP_FOLDER = pathlib.Path(__file__).resolve().parent / ".temp"
    TEMP_FOLDER.mkdir(parents=True, exist_ok=True)

    processed_folder = script_dir
    progress_state = load_progress_state(processed_folder)

    steam_settings.mkdir(parents=True, exist_ok=True)
    achievement_images.mkdir(parents=True, exist_ok=True)

    extra_folder = script_dir / ".extra"
    if extra_folder.is_dir():
        for root, dirs, files in os.walk(extra_folder):
            rel_path = pathlib.Path(root).relative_to(extra_folder)
            dest_dir = steam_settings / rel_path
            dest_dir.mkdir(parents=True, exist_ok=True)

            for f in files:
                src_file = pathlib.Path(root) / f
                dst_file = dest_dir / f
                shutil.copy2(src_file, dst_file) 

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

    if not _prompt_handled.get(html_path, False):
        if multiplayer_achievements and not already_done:
            if _gui_yes_no("Multiplayer achievements found. Remove them?"):
                achievements = [a for a in achievements if not a["is_multiplayer"]]

        if has_hidden_prefix:
            if not already_done:
                if _hidden_cleanup_needed(html_path.name, processed_html_names):
                    if _gui_yes_no('Clean descriptions that start with "Hidden achievement:"?'):
                        for a in achievements:
                            if a["description"].startswith("Hidden achievement:"):
                                a["description"] = a["description"][len("Hidden achievement:"):].lstrip()
                else:
                    for a in achievements:
                        if a["description"].startswith("Hidden achievement:"):
                            a["description"] = a["description"][len("Hidden achievement:"):].lstrip()

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
    closest = _closest_folder(html_folder, html_path.name)

    if closest:
        already_have = _copy_existing_images(
            json_path,
            closest,
            achievement_images,
        )
    else:
        already_have = set()
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
        else:
            progress_cb = _get_progress_cb(app_id, html_path) or _terminal_progress
            downloaded_cnt = download_images(
                app_id,
                missing_filenames,
                achievement_images,
                progress_cb=progress_cb,
            )
            print(f"Downloaded {len(missing_filenames)} missing image(s) to {achievement_images}")
            _download_done[html_path] = True
            missing_filenames = []
    elif app_id:
        print("All required images already present – no download needed.")
    else:
        print("No Steam app‑id found – image download skipped.")

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

        temp_dir = pathlib.Path(__file__).resolve().parent / ".temp"
        state = load_progress_state(temp_dir)
        html_path = globals().get("html_path")
        if isinstance(html_path, Path):
            percent = int(i / len(filenames) * 100)
            state[html_path.name] = {"percent": percent}
            save_progress_state(state, temp_dir)

    dlc_numbers = set()
    for m in re.finditer(r'>\s*DLC\s+(\d+)\s*<', html_content, flags=re.IGNORECASE):
        dlc_numbers.add(int(m.group(1)))
    for m in re.finditer(r'\b\w*DLC\w*\b[^()]*\(\s*(\d+)\s*\)', html_content, flags=re.IGNORECASE):
        dlc_numbers.add(int(m.group(1)))

    row_pattern = re.compile(
        r'<tr[^>]*\sdata-appid="(?P<id>\d+)"[^>]*>.*?'
        r'<td>\s*<a[^>]*>\s*(?P=id)\s*</a>\s*</td>\s*'
        r'<td>\s*(?P<title>[^<]+?)\s*</td>',
        flags=re.DOTALL | re.IGNORECASE,
    )

    dlc_info = OrderedDict()
    for m in row_pattern.finditer(html_content):
        dlc_id = int(m.group("id"))
        title = m.group("title").strip()
        if dlc_id in dlc_numbers:
            dlc_info[dlc_id] = title

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
        print("No DLC entries found – skipping DLC.txt and configs.app.ini creation.")

    processed = load_processed_log(processed_folder)

    processed.add(args.html_path.name)

    if app_id:
        processed.add(app_id)

    progress_state[args.html_path.name] = {"percent": 100}
    save_progress_state(progress_state)

    save_processed_log(processed_folder, processed)

# ------------------------------------------------------------
def _mark_complete_if_success(html_path: Path):
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

    extra_src = pathlib.Path(__file__).resolve().parent / ".extra"
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
    old_argv = sys.argv[:]
    sys.argv = [sys.argv[0], str(html_path)]
    try:
        job_tracker.start_job()
        main()

        if global_ui is not None:
            if _mark_complete_if_success(html_path):
                _load_progress_state_fresh()[html_path.name] = {"percent": 100}
                temp_dir = pathlib.Path(__file__).resolve().parent / ".temp"
                save_progress_state(progress_state, temp_dir)

                widgets = global_ui._row_widgets.get(html_path)
                if widgets:
                    prog = widgets["progress"]
                    perc = widgets["percent"]
                    prog["maximum"] = 100
                    prog["value"] = 100
                    perc.config(text="100%")
                    ctrl_btn = widgets.get("ctrl")
                    if ctrl_btn:
                        ctrl_btn.destroy()
                        widgets.pop("ctrl", None)
            else:
                progress_state.pop(html_path.name, None)
                save_progress_state(state=progress_state)

            global_ui.update_idletasks()
    finally:
        job_tracker.finish_job()
        sys.argv = old_argv

# ------------------------------------------------------------
class WatcherUI(tk.Tk):
    def __init__(self, file_queue: queue.Queue):
        super().__init__()
        self.title("SSG: Watching for HTML files")
        self.geometry("800x800")
        self.resizable(False, False)

        self.progress_state = progress_state
        self.file_queue = file_queue
        self._busy = False

        self.counter_label = tk.Label(self, text="Job Count: 0",
                                      font=("Helvetica", 12))
        self.counter_label.pack(pady=(10, 0))

        self.list_frame = Frame(self)
        self.list_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.canvas = Canvas(self.list_frame, borderwidth=0, highlightthickness=0)
        self.scrollbar = Scrollbar(self.list_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner_frame = Frame(self.canvas)
        self._row_widgets: dict[Path, dict[str, ttk.Progressbar | Label]] = {}
        self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        self._thumb_refs: dict[Path, PhotoImage] = {}

        self.after(300, self._refresh_counter)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._stop_requested = False

        self.inner_frame.grid_columnconfigure(0, weight=1)
        self.inner_frame.grid_rowconfigure(len(all_html_files), minsize=5)

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
    def update_progress(self, current: int, total: int):
        self.progress["maximum"] = total
        self.progress["value"] = current
        self.update_idletasks()

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
    def refresh_file_list(self, html_files: list[Path], status_map: dict[Path, str]):
        for widget in self.inner_frame.winfo_children():
            widget.destroy()
        self.update_idletasks()

        inset_pad = 20
        right_pad = 20
        row_width = 760 - inset_pad - right_pad
        title_max_px = 180

        for idx, path in enumerate(html_files):
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

            game_folder_path = game_dir if game_dir else path.parent
            game_folder_pn = str(game_folder_path)

            outer = Frame(self.inner_frame, bd=2, relief="groove", width=row_width, height=80)
            outer.grid(row=idx, column=0, pady=8, padx=(inset_pad, right_pad))
            outer.grid_propagate(False)

            top = Frame(outer)
            top.pack(fill="x", padx=8, pady=4)

            name_label = self._make_scrolling_label(top, title, title_max_px)
            name_label.pack(side="left")

            prog = ttk.Progressbar(top, orient="horizontal", length=380, mode="determinate")
            prog.pack(side="left", padx=12)

            percent_lbl = Label(top, text="0%", width=4)
            percent_lbl.pack(side="left", padx=4)

            attention_btn = Button(
                top,
                text="⚠️",
                width=2,
                fg="red",
                command=lambda p=path: self._confirm_attention(p),
            )
            attention_btn.pack(side="left", padx=4)

            close_btn = Button(
                top,
                text="🗑️",
                width=2,
                fg="red",
                command=lambda p=path: self._confirm_remove(p),
            )
            close_btn.pack(side="left", padx=4)

            bottom = Frame(outer)
            bottom.pack(fill="x", padx=8, pady=(0, 4))

            path_lbl = Label(
                bottom,
                text=game_folder_pn,
                fg="blue",
                cursor="hand2",
                font=("Helvetica", 12, "underline"),
            )
            path_lbl.pack(side="top", pady=2)
            path_lbl.bind("<Button-1>", lambda e, p=game_folder_path: _open_folder(p))

            self._row_widgets[path] = {
                "progress": prog,
                "percent": percent_lbl,
                "frame": outer,
            }

            saved = self.progress_state.get(path.name)
            if saved:
                percent = saved.get("percent", 0)
                prog["maximum"] = 100
                prog["value"] = 100
                percent_lbl.config(text=f"{percent}%")

    # ------------------------------------------------------------

    # ------------------------------------------------------------
    def _confirm_remove(self, html_path: Path) -> None:
        if not _gui_yes_no(f"Do you really want to delete {html_path.name}?"):
            return

        temp_dir = pathlib.Path(__file__).resolve().parent / ".temp"
        temp_txt = temp_dir / f"{html_path.name}.txt"

        temp_data: dict[str, str] = {}
        if temp_txt.is_file():
            for line in temp_txt.read_text(encoding="utf-8").splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                temp_data[k.strip()] = v.strip()

        if "HTMLFOLDER" in temp_data:
            html_folder_path = HTML_FOLDER / temp_data["HTMLFOLDER"]
            if html_folder_path.is_dir():
                try:
                    shutil.rmtree(html_folder_path, ignore_errors=True)
                    print(f"🗑️  Deleted HTML folder {html_folder_path}")
                except Exception as e:
                    print(f"⚠️  Could not delete HTML folder {html_folder_path}: {e}")

        if "HTMLFile" in temp_data:
            html_file_path = HTML_FOLDER / temp_data["HTMLFile"]
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
                                f"🗑️  Deleted game folder {game_dir} (fallback to GAMEDIR from .temp file)"
                            )
                        except Exception as e:
                            print(f"⚠️  Could not delete game folder {game_dir}: {e}")

        try:
            prog_path = pathlib.Path(__file__).resolve().parent / ".temp" / "progress.json"
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
    processed: set[str] = set()

    progress_state = load_progress_state()
    _progress_path = pathlib.Path(__file__).resolve().parent / ".temp" / "progress.json"
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
        new_files = {p for p in current_html if p.name not in processed}

        if new_files:
            print(f"Detected new HTML files: {new_files}")
            for new_html in sorted(new_files):
                try:
                    prog_path = pathlib.Path(__file__).resolve().parent / ".temp" / "progress.json"
                    if prog_path.is_file():
                        prog_data = json.loads(prog_path.read_text(encoding="utf-8"))
                        if new_html.name in prog_data:
                            del prog_data[new_html.name]
                            with prog_path.open("w", encoding="utf-8") as f:
                                json.dump(prog_data, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass

                if isinstance(progress_state, dict):
                    progress_state.pop(new_html.name, None)

                all_html_files.append(new_html)
                file_status[new_html] = "waiting"
                file_queue.put(new_html)
                processed.add(new_html.name)

                job_tracker.add_job()

                def _process(p):
                    if global_ui is not None:
                        global_ui.start_job()
                    _run_main_in_thread(p)
                    if global_ui is not None:
                        global_ui.finish_job()

                threading.Thread(target=_process, args=(new_html,), daemon=True).start()

            if global_ui is not None:
                global_ui.refresh_file_list(all_html_files, file_status)

        try:
            msg = file_queue.get_nowait()
        except queue.Empty:
            msg = None

        if isinstance(msg, tuple) and msg[0] == "__NEW__":
            new_path = msg[1]
            if new_path not in all_html_files:
                all_html_files.append(new_path)
            file_status[new_path] = "waiting"
            if global_ui is not None:
                global_ui.refresh_file_list(all_html_files, file_status)
            continue

        if isinstance(msg, Path):
            html_path = msg
            if html_path not in all_html_files:
                all_html_files.append(html_path)
            file_status[html_path] = "waiting"
            if global_ui is not None:
                global_ui.refresh_file_list(all_html_files, file_status)

            sys.argv = [sys.argv[0], str(html_path)]

            try:
                if global_ui is not None:
                    global_ui.start_job()
                file_status[html_path] = "processing"
                if global_ui is not None:
                    global_ui.refresh_file_list(all_html_files, file_status)

                globals()["html_path"] = html_path

                threading.Thread(
                    target=_run_main_in_thread,
                    args=(html_path,),
                    daemon=True,
                ).start()
            except SystemExit:
                pass
            except Exception as exc:
                print(f"Error processing {html_path}: {exc}")
            finally:
                if global_ui is not None:
                    global_ui.finish_job()

                file_status[html_path] = "done"
                if global_ui is not None:
                    global_ui.refresh_file_list(all_html_files, file_status)

                if _mark_complete_if_success(html_path):
                    _load_progress_state_fresh()[html_path.name] = {"percent": 100}
                    temp_dir = pathlib.Path(__file__).resolve().parent / ".temp"
                    save_progress_state(progress_state, temp_dir)

                    widgets = global_ui._row_widgets.get(html_path)
                    if widgets:
                        prog = widgets["progress"]
                        perc = widgets["percent"]
                        prog["maximum"] = 100
                        prog["value"] = 100
                        perc.config(text="100%")
                        ctrl_btn = widgets.get("ctrl")
                        if ctrl_btn:
                            ctrl_btn.destroy()
                            widgets.pop("ctrl", None)
                else:
                    progress_state.pop(html_path.name, None)
                    save_progress_state(state=progress_state)

                global_ui.update_idletasks()
                job_tracker.finish_job()

        time.sleep(1)

if __name__ == "__main__":
    if len(sys.argv) > 1 and Path(sys.argv[1]).suffix.lower() == ".html":
        main()
    else:
        HTML_FOLDER = pathlib.Path(__file__).resolve().parent / "HTML"
        HTML_FOLDER.mkdir(parents=True, exist_ok=True)

        for existing in HTML_FOLDER.iterdir():
            if existing.suffix.lower() == ".html":
                all_html_files.append(existing)
                file_status[existing] = "waiting"

        file_queue = queue.Queue()
        stop_event = threading.Event()

        script_dir = HTML_FOLDER
        all_html_files: list[Path] = []
        file_status: dict[Path, str] = {}
        progress_state = load_progress_state()

        try:
            global_ui = WatcherUI(file_queue)
            global_ui.progress_state = load_progress_state()
        except Exception as e:
            print(f"Error during GUI initialization: {e}")
            sys.exit(1)

        watcher_thread = threading.Thread(
            target=_watch_worker,
            args=(HTML_FOLDER, file_queue, stop_event),
            daemon=True,
        )
        watcher_thread.start()

        global_ui.mainloop()
