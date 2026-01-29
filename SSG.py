#!/usr/bin/env python3
import os, sys, re, json, argparse, difflib, pathlib, requests, shutil, threading, queue, time, random
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urljoin
from bs4 import BeautifulSoup

global_ui = None

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
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

    try:
        import subprocess
        result = subprocess.run(
            ["zenity", "--question", "--title=Confirm", f"--text={question}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except FileNotFoundError:
        pass
    except Exception:
        pass

    resp = input(f"{question} (Y/N): ").strip().lower()
    return resp == "y"

#----------------------------------------------------------------------
def _show_error(msg: str) -> None:
    if tk is not None:
        try:
            messagebox.showerror("Error", msg)
        except Exception:
            pass
    else:
        print(msg)

#----------------------------------------------------------------------
def parse_appid(arg: str) -> str | None:
    if arg.isdigit():
        return arg

    m = re.search(r"/app/(\d+)", arg)
    if m:
        return m.group(1)

    return None

import random
import time

PROXIES = None

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/123.0",
]

def _fetch_steamdb_html(appid: str) -> str | None:
    url = f"https://steamdb.info/app/{appid}/stats/"

    time.sleep(random.uniform(0.5, 1.2))

    headers = {
        "User-Agent": random.choice(UA_LIST),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://steamdb.info/",
        "Connection": "keep-alive",
    }

    try:
        resp = requests.get(url, timeout=20, headers=headers, proxies=PROXIES)
        resp.raise_for_status()
        return resp.text
    except requests.HTTPError:
        if resp.status_code == 403:
            print(f"⚠️  SteamDB returned 403 for AppID {appid}.")
        else:
            print(f"HTTP error {resp.status_code} for AppID {appid}.")
    except Exception as exc:
        print(f"Failed to fetch SteamDB page for AppID {appid}: {exc}")

    return None

def _fetch_steamdb_html(appid: str) -> str | None:
    """
    Retrieve the SteamDB stats page for *appid*.
    Returns the raw HTML or ``None`` on failure.
    """
    url = f"https://steamdb.info/app/{appid}/stats/"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://steamdb.info/",
        "Connection": "keep-alive",
    }

    time.sleep(0.3)

    try:
        resp = requests.get(url, timeout=20, headers=headers)
        resp.raise_for_status()
        return resp.text
    except requests.HTTPError as http_err:
        if resp.status_code == 403:
            print(
                f"⚠️  SteamDB returned 403 for AppID {appid}. "
                "Try again later or use a VPN/proxy."
            )
        else:
            print(f"HTTP error for AppID {appid}: {http_err}")
    except Exception as exc:
        print(f"Failed to fetch SteamDB page for AppID {appid}: {exc}")

    return None


def download_stats_html(appid: str) -> Path | None:
    html_dir = Path(__file__).resolve().parent / ".html"
    html_dir.mkdir(exist_ok=True)

    html_text = _fetch_steamdb_html(appid)
    if html_text is None:
        return None

    class _DummyResp:
        def __init__(self, text: str):
            self.text = text
            self.content = text.encode("utf-8")

    resp = _DummyResp(html_text)

    html_path = html_dir / f"{appid}.html"
    html_path.write_bytes(resp.content)

    achievements = parse_steam_community(resp.text)

    need_hidden = any(a["hidden"] == 0 for a in achievements)

    if need_hidden:
        hidden_map = parse_steamdb_hidden(resp.text)
        for ach in achievements:
            if ach["name"] in hidden_map:
                ach["hidden"] = hidden_map[ach["name"]]

    soup = BeautifulSoup(resp.text, "html.parser")
    base_folder = Path(__file__).resolve().parent / clean_title(
        soup.find("h1", itemprop="name").text
        if soup.find("h1", itemprop="name")
        else f"App_{appid}"
    )
    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"

    steam_settings.mkdir(parents=True, exist_ok=True)
    achievement_images.mkdir(parents=True, exist_ok=True)

    json_path = steam_settings / "achievements.json"
    json_path.write_text(
        json.dumps(achievements, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Achievements JSON written to {json_path}")

    filenames = collect_image_names(soup)
    download_images(appid, filenames, achievement_images, progress_cb=_noop_progress)

    return html_path

#----------------------------------------------------------------------
def parse_steam_community(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    achievements = []

    for div in soup.find_all("div", id=lambda v: v and v.startswith("achievement-")):
        name = div["id"].split("-", 1)[1]

        display_tag = div.find(class_="achievement_name")
        display_name = display_tag.text.strip() if display_tag else "Unknown"

        desc_tag = div.find(class_="achievement_desc")
        description = desc_tag.text.strip() if desc_tag else "No description"

        icon_tag = div.find(class_="achievement_image")
        icon = get_image_filename(icon_tag)

        icon_small_tag = div.find(class_="achievement_image_small")
        icon_small = get_image_filename(icon_small_tag)

        hidden = 0

        is_multiplayer = (
            div.find("div", class_="achievement_group")
            and div.find("div", class_="achievement_group").text.strip()
            == "Multiplayer"
        )

        achievements.append(
            {
                "name": name,
                "defaultvalue": 0,
                "displayName": display_name,
                "hidden": hidden,
                "description": description,
                "icon": icon,
                "icongray": icon_small,
                "icon_gray": icon_small,
                "is_multiplayer": is_multiplayer,
            }
        )
    return achievements

#----------------------------------------------------------------------
def parse_steamdb_hidden(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    hidden_map = {}

    for div in soup.find_all("div", id=lambda v: v and v.startswith("achievement-")):
        name = div["id"].split("-", 1)[1]
        if div.find("span", class_="achievement_spoiler") or div.find(
            "i", string="Hidden achievement:"
        ):
            hidden_map[name] = 1
    return hidden_map

#----------------------------------------------------------------------
def _rewrite_image_urls(html: str, folder_name: str) -> str:
    """
    Replace absolute Steam image URLs with relative paths that point to
    ``folder_name`` (e.g. "achievement_images/…").
    """
    def repl(match):
        filename = Path(match.group(1)).name
        return f'src="{folder_name}/{filename}"'

    pattern = re.compile(r'src="[^"]*?/([^/]+\.jpg)"')
    return pattern.sub(repl, html)


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
        self._busy = True
        self.progress["value"] = 0
        self.progress.pack()
        self.entry.configure(state="disabled")

    def finish_job(self):
        self._busy = False
        self.progress.pack_forget()
        self.entry.configure(state="normal")

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

def save_processed_log(folder: Path, processed: set):
    log_path = folder / PROCESSED_LOG
    try:
        sorted_entries = sorted(processed)
        log_path.write_text("\n".join(sorted_entries) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"Warning: could not write processed log ({log_path}): {e}")

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
    html_files = sorted(p for p in script_dir.iterdir() if p.suffix.lower() == ".html")

    if not html_files:
        print("No .html files found next to the script.")
        sys.exit(0)

    for html_path in html_files:
        if not html_path.is_file():
            print(f"File not found: {html_path}")
            continue
        if html_path.suffix.lower() != ".html":
            print("The supplied file must have a .html extension")
            continue

        html_content = read_local_file(str(html_path))
        soup = BeautifulSoup(html_content, "html.parser")

        h1_tag = soup.find("h1", itemprop="name")
        if not h1_tag:
            print("No <h1 itemprop='name'> tag found – cannot create output folder.")
            continue

    game_title = clean_title(h1_tag.text)
    script_dir = pathlib.Path(__file__).resolve().parent
    html_files = sorted(p for p in script_dir.iterdir() if p.suffix.lower() == ".html")

    if not html_files:
        print("No .html files found next to the script.")
        sys.exit(0)

    for html_path in html_files:
        if not html_path.is_file():
            print(f"File not found: {html_path}")
            continue

    base_folder = script_dir / game_title
    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"

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

    multiplayer_achievements = [a for a in achievements if a["is_multiplayer"]]
    if multiplayer_achievements:
        if _gui_yes_no("Multiplayer achievements found. Remove them?"):
            achievements = [a for a in achievements if not a["is_multiplayer"]]

    has_hidden_prefix = any(
        a["description"].startswith("Hidden achievement:") for a in achievements
    )
    if has_hidden_prefix:
        if _gui_yes_no('Clean descriptions that start with "Hidden achievement:"?'):
            for a in achievements:
                if a["description"].startswith("Hidden achievement:"):
                    a["description"] = a["description"][
                        len("Hidden achievement:")
                    :].lstrip()

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

    if app_id:
        filenames = collect_image_names(soup)
        filenames = [f for f in filenames if f not in already_have]

        progress_fn = (
            global_ui.update_progress
            if (global_ui is not None and hasattr(global_ui, "update_progress"))
            else _noop_progress
        )

        download_images(
            app_id,
            filenames,
            achievement_images,
            progress_cb=global_ui.update_progress
                         if global_ui is not None else _noop_progress,
        )
        
        if filenames:
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

    save_processed_log(processed_folder, processed)


# ------------------------------------------------------------
def _run_main_in_thread(html_path: Path):
    old_argv = sys.argv[:]
    sys.argv = [sys.argv[0], str(html_path)]
    try:
        main()
    finally:
        sys.argv = old_argv

# ------------------------------------------------------------
def parse_appid(arg: str) -> str | None:
    if arg.isdigit():
        return arg
    m = re.search(r"/app/(\d+)", arg)
    return m.group(1) if m else None

# ------------------------------------------------------------
class WatcherUI(tk.Tk):
    def __init__(self, file_queue: queue.Queue):
        super().__init__()
        self.title("SSG: Watching for HTML files")
        self.geometry("500x500")
        self.resizable(False, False)

        self.file_queue = file_queue
        self._busy = False

        self.input_var = tk.StringVar()
        input_frame = tk.Frame(self)
        input_frame.pack(pady=(10, 0), fill="x", padx=10)

        tk.Label(
            input_frame,
            text="Enter Steam/SteamDB Link or AppID:",
            anchor="w",
        ).pack(side="top", anchor="w")

        self.entry = tk.Entry(
            input_frame,
            textvariable=self.input_var,
            width=50,
        )
        self.entry.pack(side="top", fill="x", pady=5)
        self.entry.bind("<Return>", self._on_enter)

        tk.Frame(self, height=2, bg="#cccccc").pack(fill="x", pady=8)

        self.progress = ttk.Progressbar(self, orient="horizontal",
                                        length=260, mode="determinate")
        self.progress.pack(pady=10)
        self.progress["value"] = 0
        self.progress.pack_forget()

        self.counter_label = tk.Label(
            self,
            text="Job Count: 0",
            font=("Helvetica", 12),
        )
        self.counter_label.pack(pady=(10, 0))

        self.after(300, self._refresh_counter)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._stop_requested = False
    # ------------------------------------------------------------------
    def _on_enter(self, event=None):
        if self._busy:
            return

        raw = self.input_var.get().strip()
        if not raw:
            return

        appid = parse_appid(raw)
        if not appid:
            print(f"Could not extract AppID from '{raw}'")
            return

        html_path = download_stats_html(appid)
        if html_path is None:
            _show_error(f"Download failed for AppID {appid}")
            return

        self.input_var.set("")
        self.file_queue.put(html_path)
    # ------------------------------------------------------------------
    def _refresh_counter(self):
        queued, active = job_tracker.snapshot()
        displayed = queued + active
        self.counter_label.config(text=f"Job Count: {displayed}")
        self.after(300, self._refresh_counter)
    # ------------------------------------------------------------------
    def start_job(self):
        self._busy = True
        self.progress["value"] = 0
        self.progress.pack()

    def finish_job(self):
        self._busy = False
        self.progress.pack_forget()

    # ------------------------------------------------------------------
    def update_progress(self, current: int, total: int):
        self.progress["maximum"] = total
        self.progress["value"] = current
        self.update_idletasks()

    # ------------------------------------------------------------------
    def _on_close(self):
        self._stop_requested = True
        self.destroy()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

global_ui = None

def _watch_worker(folder: Path, file_queue: queue.Queue, stop_flag: threading.Event):
    processed: set[Path] = set()
    while not stop_flag.is_set():

        current_html = {p for p in folder.iterdir() if p.suffix.lower() == ".html"}
        new_files = current_html - processed

        for new_html in sorted(new_files):

            job_tracker.add_job()

            file_queue.put(new_html)
            processed.add(new_html)

        time.sleep(2.0)

def _run_gui_watcher():
    global global_ui
    folder = Path(__file__).resolve().parent

    q: queue.Queue[Path] = queue.Queue()
    global_ui = WatcherUI(q)
    ui = global_ui

    stop_event = threading.Event()
    watcher_thread = threading.Thread(
        target=_watch_worker,
        args=(folder, q, stop_event),
        daemon=True,
    )
    watcher_thread.start()

    while not ui.stop_requested:
        try:
            html_path = q.get(timeout=0.5)
        except queue.Empty:
            ui.update()
            continue

        sys.argv = [sys.argv[0], str(html_path)]

        try:
            ui.start_job()
            job_tracker.start_job()
            _run_main_with_progress()
        except SystemExit:
            pass
        except Exception as exc:
            print(f"Error processing {html_path}: {exc}")
        finally:
            ui.finish_job()
            job_tracker.finish_job()
        ui.update()

    stop_event.set()
    watcher_thread.join()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Steam achievement scraper – GUI watcher or single‑run mode."
    )
    parser.add_argument("-appid", type=str, help="Numeric Steam AppID")
    parser.add_argument("-link", type=str, help="SteamDB or Steam store URL")
    args, _ = parser.parse_known_args()

    # --------------------------------------------------------------
    if args.appid or args.link:
        raw = args.appid or args.link
        appid = parse_appid(raw)

        if not appid:
            print(f"Could not extract AppID from '{raw}'")
            sys.exit(1)

        html_path = download_stats_html(appid)
        if html_path is None:
            sys.exit(1) 

        sys.argv = [sys.argv[0], str(html_path)]
        try:
            main()
        finally:
            try:
                if html_path.is_file():
                    html_path.unlink()
                sibling = html_path.parent / html_path.stem
                if sibling.is_dir():
                    shutil.rmtree(sibling)
            except Exception:
                pass
        sys.exit(0)

    if len(sys.argv) > 1:
        main()
    else:
        _run_gui_watcher()
