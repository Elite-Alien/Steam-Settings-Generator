#!/usr/bin/env python3
import os
import sys
import re
import json
import argparse
import pathlib
import requests
import shutil
from collections import OrderedDict
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
HTML_PATTERN = re.compile(
    r'<link\s+rel=["\']canonical["\']\s+href=["\']https?://steamdb\.info/app/(\d+)/stats/["\']',
    re.IGNORECASE,
)
IMG_PATTERN = re.compile(r'([a-f0-9]{40})\.jpg', re.IGNORECASE)

APP_URL_TEMPLATE = "https://shared.fastly.steamstatic.com/community_assets/images/apps/{app_id}/"
PROCESSED_LOG = "blacklist"

# ----------------------------------------------------------------------
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

defdef extract_app_id(soup: BeautifulSoup) -> str | None:
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
        description="Process SteamDB HTML file for achievements and DLC."
    )
    parser.add_argument(
        "html_path",
        type=pathlib.Path,
        help="Full path to the HTML file (must end with .html)",
    )
    args = parser.parse_args()

    if not args.html_path.is_file():
        print(f"File not found: {args.html_path}")
        sys.exit(1)
    if args.html_path.suffix.lower() != ".html":
        print("The supplied file must have a .html extension")
        sys.exit(1)

    html_content = read_local_file(str(args.html_path))
    soup = BeautifulSoup(html_content, "html.parser")

    h1_tag = soup.find("h1", itemprop="name")
    if not h1_tag:
        print("No <h1 itemprop='name'> tag found – cannot create output folder.")
        sys.exit(1)

    game_title = clean_title(h1_tag.text)
    script_dir = pathlib.Path(__file__).resolve().parent
    base_folder = script_dir / game_title
    steam_settings = base_folder / "steam_settings"
    achievement_images = steam_settings / "achievement_images"

    steam_settings.mkdir(parents=True, exist_ok=True)
    achievement_images.mkdir(parents=True, exist_ok=True)

    extra_folder = script_dir / "extra"
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
        resp = input(
            "Multiplayer achievements found. Remove them? (Y/N): "
        ).strip().lower()
        if resp == "y":
            achievements = [a for a in achievements if not a["is_multiplayer"]]

    has_hidden_prefix = any(
        a["description"].startswith("Hidden achievement:") for a in achievements
    )
    if has_hidden_prefix:
        resp = input(
            'Clean descriptions that start with "Hidden achievement:"? (Y/N): '
        ).strip().lower()
        if resp == "y":
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

    if args.html_path.name in processed or (app_id and app_id in processed):
        print("Images already downloaded for this file or app‑id – skipping.")
    else:
        if not app_id:
            print("No Steam app‑id found – image download skipped.")
        else:
            filenames = collect_image_names(soup)
            if filenames:
                download_images(app_id, filenames, achievement_images)
                print(f"Downloaded {len(filenames)} image(s) to {achievement_images}")
            else:
                print("No image filenames detected – nothing to download.")

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

if __name__ == "__main__":
    main()
