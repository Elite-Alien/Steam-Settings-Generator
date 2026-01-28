#!/usr/bin/env python3
import os, sys, re, json, argparse, difflib, pathlib, requests, shutil, threading, queue, time
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urljoin
from bs4 import BeautifulSoup

try:
    import tkinter as tk
    from tkinter import messagebox
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
    # First try the explicit <link> tag
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


def download_images(app_id: str, filenames: list[str], dest_folder: Path):
    base_url = APP_URL_TEMPLATE.format(app_id=app_id)
    dest_folder.mkdir(parents=True, exist_ok=True)

    for fname in filenames:
        url = urljoin(base_url, fname)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            (dest_folder / fname).write_bytes(resp.content)
        except Exception as e:
            print(f"Failed {url}: {e}")


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

        if filenames:
            download_images(app_id, filenames, achievement_images)
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

class WatcherUI(tk.Tk):
    def __init__(self, file_queue: queue.Queue):
        super().__init__()
        self.title("SSG – Watching for HTML files")
        self.geometry("300x80")
        self.resizable(False, False)

        self.file_queue = file_queue

        self.counter_label = tk.Label(self, text="Files waiting: 0", font=("Helvetica", 12))
        self.counter_label.pack(expand=True, pady=12)

        self.after(300, self._refresh_counter)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._stop_requested = False

    def _refresh_counter(self):
        pending = self.file_queue.qsize()
        self.counter_label.config(text=f"Files waiting: {pending}")
        self.after(300, self._refresh_counter)

    def _on_close(self):
        self._stop_requested = True
        self.destroy()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

def _watch_worker(folder: Path, file_queue: queue.Queue, stop_flag: threading.Event):
    processed: set[Path] = set()

    while not stop_flag.is_set():
        current_html = {p for p in folder.iterdir() if p.suffix.lower() == ".html"}
        new_files = current_html - processed

        for new_html in sorted(new_files):
            file_queue.put(new_html)
            processed.add(new_html)

        time.sleep(2.0)

def _run_gui_watcher():
    folder = Path(__file__).resolve().parent
    q: queue.Queue[Path] = queue.Queue()
    stop_event = threading.Event()

    watcher_thread = threading.Thread(
        target=_watch_worker,
        args=(folder, q, stop_event),
        daemon=True,
    )
    watcher_thread.start()

    ui = WatcherUI(q)
    while not ui.stop_requested:
        try:
            html_path = q.get(timeout=0.5)
        except queue.Empty:
            ui.update()
            continue

        sys.argv = [sys.argv[0], str(html_path)]
        try:
            main()
        except SystemExit:
            pass
        except Exception as exc:
            print(f"Error processing {html_path}: {exc}")

        ui.update()

    stop_event.set()
    watcher_thread.join()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        _run_gui_watcher()
