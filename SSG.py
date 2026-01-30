#!/usr/bin/env python3
import os, sys, re, json, argparse, difflib, pathlib, requests, shutil, threading, queue, time, webbrowser
from tkinter import Canvas, Scrollbar, Frame, Label, PhotoImage
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urljoin
from bs4 import BeautifulSoup

all_html_files: list[Path] = []
file_status: dict[Path, str] = {}

def _terminal_progress(current: int, total: int, width: int = 30) -> None:
    percent = int(current / total * 100)
    filled = int(current / total * width)
    bar = "·" + "·" * (width - 1)
    bar = bar[:filled] + "●" + bar[filled + 1 :] if filled < width else bar
    sys.stdout.write(f"\r[{bar}] {percent:3d}%")
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")

def _open_folder(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform.startswith("darwin"):
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass

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
    if tk is not None:
        try:
            root = tk.Tk()
            root.withdraw()
            answer = messagebox.askyesno("Confirm", question)
            root.destroy()
            return answer
        except Exception:
            pass

    resp = input(f"{question} (Y/N): ").strip().lower()
    return resp == "y"

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

_pause_state: dict[Path, tuple[bool, int]] = {}

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
PROCESSED_LOG = "blacklist"
PROGRESS_STATE_FILE = "progress.json"
HTML_FOLDER = pathlib.Path(__file__).resolve().parent / "HTML"
HTML_FOLDER.mkdir(parents=True, exist_ok=True)

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
    log_path = folder / PROCESSED_LOG
    if not log_path.exists():
        return set()

    try:
        lines = log_path.read_text().splitlines()
        return {line.strip() for line in lines if line.strip()}
    except OSError as e:
        print(f"Warning: could not read processed log ({log_path}): {e}")
        return set()

def _hidden_cleanup_needed(html_name: str, processed: set) -> bool:
    return html_name not in processed

def save_processed_log(folder: Path, processed: set):
    log_path = folder / PROCESSED_LOG
    try:
        sorted_entries = sorted(processed)
        log_path.write_text("\n".join(sorted_entries) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"Warning: could not write processed log ({log_path}): {e}")

def load_progress_state(folder: Path) -> dict:
    p = folder / PROGRESS_STATE_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_progress_state(folder: Path, state: dict) -> None:
    p = folder / PROGRESS_STATE_FILE
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")

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
):
    base_url = APP_URL_TEMPLATE.format(app_id=app_id)
    dest_folder.mkdir(parents=True, exist_ok=True)

    total = len(filenames)
    for i, fname in enumerate(filenames, start=1):
        url = urljoin(base_url, fname)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            (dest_folder / fname).write_bytes(resp.content)
        except Exception as e:
            print(f"Failed {url}: {e}")

        if progress_cb is not None:
            try:
                progress_cb(i, total)
            except Exception:
                pass

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
    state = load_progress_state(folder)
    state[html_path.name] = {"percent": percent}
    save_progress_state(folder, state)

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
    progress_state = load_progress_state(script_dir)
    base_folder = script_dir / clean_title(soup.find("h1", itemprop="name").text)
    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"

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
    if multiplayer_achievements:
        if _gui_yes_no("Multiplayer achievements found. Remove them?"):
            achievements = [a for a in achievements if not a["is_multiplayer"]]

    has_hidden_prefix = any(
        a["description"].startswith("Hidden achievement:") for a in achievements
    )
    if has_hidden_prefix:
        if _hidden_cleanup_needed(html_path.name, processed_html_names):
            if _gui_yes_no('Clean descriptions that start with "Hidden achievement:"?'):
                for a in achievements:
                    if a["description"].startswith("Hidden achievement:"):
                        a["description"] = a["description"][
                           len("Hidden achievement:") :
                        ].lstrip()
        else:
            for a in achievements:
                if a["description"].startswith("Hidden achievement:"):
                    a["description"] = a["description"][
                        len("Hidden achievement:") :
                    ].lstrip()

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
        progress_cb = _get_progress_cb(app_id, html_path) or _terminal_progress
        download_images(
            app_id,
            missing_filenames,
            achievement_images,
            progress_cb=progress_cb,
        )
        print(f"Downloaded {len(missing_filenames)} missing image(s) to {achievement_images}")
    elif app_id:
        print("All required images already present – no download needed.")
    else:
        print("No Steam app‑id found – image download skipped.")

        filenames = [f for f in filenames if f not in already_present]

        if app_id:
            filenames = collect_image_names(soup)
            filenames = [f for f in filenames if f not in already_have]

            progress_cb = _get_progress_cb(app_id, html_path) or _terminal_progress

            if global_ui is not None and hasattr(global_ui, "_row_widgets"):
                progress_cb = lambda cur, tot: _ui_progress(cur, tot, html_path, global_ui, script_dir)
            def _row_progress(cur: int, tot: int):
                cb = _get_progress_cb(app_id, html_path)
                if cb:
                   try:
                      cb(cur, tot)
                      return
                   except Exception:
                       pass

                _terminal_progress(cur, tot)

            download_images(
                app_id,
                filenames,
                achievement_images,
                progress_cb=_row_progress,
            )

        def _wrapped_download(app_id: str, filenames: list[str], dest: Path, cb: callable = _terminal_progress):
            if cb is None:
                cb = progress_cb
            start = _pause_state.get(html_path, (False, 0))[1]
            for i, fname in enumerate(filenames[start:], start=start + 1):
                if _pause_state.get(html_path, (False, 0))[0]:
                    break
                url = urljoin(APP_URL_TEMPLATE.format(app_id=app_id), fname)
                try:
                    resp = requests.get(url, timeout=15)
                    resp.raise_for_status()
                    (dest / fname).write_bytes(resp.content)
                except Exception as e:
                    print(f"Failed {url}: {e}")
                cb(i, len(filenames))

        if app_id:
            all_filenames = collect_image_names(soup)

            existing_local = {
                p.name
                for p in achievement_images.iterdir()
                if p.is_file() and p.suffix.lower() == ".jpg"
            }

            already_present = already_have.union(existing_local)
            filenames = [f for f in all_filenames if f not in already_present]
            progress_cb = _get_progress_cb(app_id, html_path)

            if filenames:
                progress_cb = _get_progress_cb(app_id, html_path) or _terminal_progress
                _wrapped_download(app_id, filenames, achievement_images, cb=progress_cb)
                print(f"Downloaded {len(filenames)} missing image(s) to {achievement_images}")
            else:
                print("All required images already present – no download needed.")
        else:
            print("No Steam app‑id found – image download skipped.")


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
    save_progress_state(processed_folder, progress_state)

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
                progress_state[html_path.name] = {"percent": 100}
                save_progress_state(script_dir, progress_state)

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
                save_progress_state(script_dir, progress_state)

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

    def refresh_file_list(self, html_files: list[Path], status_map: dict[Path, str]):
        for widget in self.inner_frame.winfo_children():
            widget.destroy()
        self.update_idletasks()

        for idx, path in enumerate(html_files):
            try:
                soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                title = soup.find("h1", itemprop="name").get_text(strip=True)
            except Exception:
                title = path.stem

            icon_url = None
            img_tag = soup.find("img", class_="app-icon")
            if img_tag:
                icon_url = img_tag.get("src")

            row = Frame(self.inner_frame, bd=1, relief="solid", padx=5, pady=5)
            row.grid(row=idx, column=0, sticky="ew", pady=4)
            row.columnconfigure(2, weight=1)

            if icon_url:
                placeholder = PhotoImage(width=32, height=32)
                icon_label = Label(row, image=placeholder)
                icon_label.image = placeholder
                icon_label.grid(row=0, column=0, rowspan=2, padx=4)

                def _load_icon(url: str, lbl: Label):
                    try:
                        data = requests.get(url, timeout=10).content
                        img = PhotoImage(data=data)
                        lbl.configure(image=img)
                        lbl.image = img
                    except Exception:
                        pass

                threading.Thread(
                    target=_load_icon,
                    args=(icon_url, icon_label),
                    daemon=True,
                ).start()
            else:
                Label(row, width=4).grid(row=0, column=0, rowspan=2, padx=4)

            Label(row, text=title, font=("Helvetica", 11, "bold")).grid(
                row=0, column=1, sticky="w"
            )

            prog = ttk.Progressbar(row, orient="horizontal", length=150, mode="determinate")
            prog.grid(row=0, column=2, sticky="ew", padx=8)

            percent_lbl = Label(row, text="0%")
            percent_lbl.grid(row=0, column=3, padx=4)

            saved = self.progress_state.get(path.name)
            if saved:
                percent = saved.get("percent", 0)
                prog["maximum"] = 100
                prog["value"] = 100
                percent_lbl.config(text=f"{percent}%")

            state = _pause_state.get(path, (False, 0))
            btn_text = "▶" if state[0] else "⏸"

            if saved and saved.get("percent") == 100:
                ctrl_btn = None
            else:
                ctrl_btn = Button(
                    row,
                    text=btn_text,
                    width=2,
                    command=lambda p=path: self._toggle_pause(p),
                )
                ctrl_btn.grid(row=0, column=4, padx=2)

            self._row_widgets[path] = {
                "progress": prog,
                "percent": percent_lbl,
                **({"ctrl": ctrl_btn} if ctrl_btn else {})
            }

            close_btn = Button(
                row,
                text="✕",
                width=2,
                fg="red",
                command=lambda p=path: self._confirm_remove(p),
            )
            close_btn.grid(row=0, column=5, padx=2)

            path_lbl = Label(
                row,
                text=str(path.parent),
                fg="blue",
                cursor="hand2",
                font=("Helvetica", 9, "underline"),
            )
            path_lbl.grid(row=1, column=1, columnspan=5, sticky="w", pady=(2, 0))
            path_lbl.bind(
                "<Button-1>",
                lambda e, p=path.parent: _open_folder(p),
            )

            self._row_widgets[path] = {
                "progress": prog,
                "percent": percent_lbl,
                **({"ctrl": ctrl_btn} if ctrl_btn else {})
            }

    # ------------------------------------------------------------
    def _remove_entry(self, path: Path):
        for widget in self.inner_frame.winfo_children():
            info = widget.grid_info()
            if info.get("row") is not None and path in self._row_widgets:
                widget.destroy()
        self._row_widgets.pop(path, None)

        new_q = queue.Queue()
        while not self.file_queue.empty():
            item = self.file_queue.get()
            if item != path and not (isinstance(item, tuple) and item[1] == path):
                new_q.put(item)
        self.file_queue = new_q

    # ------------------------------------------------------------
    def _toggle_pause(self, path: Path):
        paused, idx = _pause_state.get(path, (False, 0))
        _pause_state[path] = (not paused, idx)
        btn = self._row_widgets[path]["ctrl"]
        btn.config(text="▶" if not paused else "⏸")

    # ------------------------------------------------------------
    def _confirm_remove(self, path: Path):
        if not _gui_yes_no(f"Do you really want to delete {path.name}?"):
            return

        try:
            base = pathlib.Path(__file__).resolve().parent / clean_title(
                BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                .find("h1", itemprop="name")
                .text
            )
            shutil.rmtree(base, ignore_errors=True)
            path.unlink(missing_ok=True)
        except Exception:
            pass

        self._row_widgets.pop(path, None)
        if path in all_html_files:
            all_html_files.remove(path)
        file_status.pop(path, None)
        self.refresh_file_list(all_html_files, file_status)

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
    while not stop_flag.is_set():
        current_html = {p for p in folder.iterdir() if p.suffix.lower() == ".html"}
        new_files = {p for p in current_html if p.name not in processed}

        if new_files:
            print(f"Detected HTML files: {new_files}")
            for new_html in sorted(new_files):
                all_html_files.append(new_html)
                file_status[new_html] = "waiting"
            if global_ui is not None:
                global_ui.refresh_file_list(all_html_files, file_status)

        for new_html in sorted(new_files):
            job_tracker.add_job()
            file_queue.put(new_html)
            processed.add(new_html.name)

            threading.Thread(
                target=_run_main_in_thread,
                args=(new_html,),
                daemon=True,
            ).start()

        if isinstance(msg, tuple) and msg[0] == "__NEW__":
            new_path = msg[1]
            if new_path not in all_html_files:
                all_html_files.append(new_path)
            file_status[new_path] = "waiting"
            global_ui.refresh_file_list(all_html_files, file_status)
            continue

        html_path: Path = msg
        if html_path not in all_html_files:
            all_html_files.append(html_path)
        file_status[html_path] = "waiting"
        global_ui.refresh_file_list(all_html_files, file_status)

        sys.argv = [sys.argv[0], str(html_path)]

        try:
            global_ui.start_job()
            file_status[html_path] = "processing"
            global_ui.refresh_file_list(all_html_files, file_status)

            globals()["html_path"] = html_path
            _pause_state[html_path] = (False, 0)

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
            global_ui.finish_job()
            file_status[html_path] = "done"
            global_ui.refresh_file_list(all_html_files, file_status)
            job_tracker.finish_job()

            if global_ui is not None:
                if _mark_complete_if_success(html_path):
                    progress_state[html_path.name] = {"percent": 100}
                    save_progress_state(script_dir, progress_state)

                    widgets = global_ui._row_widgets.get(html_path)
                    if widgets:
                        prog = widgets["progress"]
                        perc = widgets["percent"]
                        prog["maximum"] = tot
                        prog["value"] = cur
                        percent = int(cur / tot * 100)
                        perc.config(text="100%")
                        ctrl_btn = widgets.get("ctrl")
                        if ctrl_btn:
                            ctrl_btn.destroy()
                            widgets.pop("ctrl", None)
                else:
                    progress_state.pop(html_path.name, None)
                    save_progress_state(script_dir, progress_state)

                global_ui.update_idletasks()

            _run_main_with_progress()

            if global_ui is not None:
                progress_state[html_path.name] = {"percent": 100}
                save_progress_state(script_dir, progress_state)
                
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

                    global_ui.update_idletasks()

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
        progress_state = load_progress_state(script_dir)

        try:
            global_ui = WatcherUI(file_queue)
            global_ui.progress_state = progress_state
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
