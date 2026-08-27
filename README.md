# Steam Settings Generator
This is a Steam Emulator Configuration and Management Tool. It has many features that other tools do not. It's packed full of extra goodies to make it simple to get achievement data and much more. Along with automating a handful of the work for you.

# System Requirements
### Optional
Steam API Key: Not required, but optional if you choose to use it for AppID searches.

### Linux
Some packages may very based on Distro. If I did not properly cover a Distro and it's packages, please let me know. I tried to get information on a number of them, some I do not even use.

### Ubuntu / Debian
```
sudo apt install python3 python3-tk python3-requests python3-bs4 python3-cryptography python3-pillow python3-bcrypt
```
### Fedora
```
sudo dnf install python3 python3-tkinter python3-requests python3-beautifulsoup4 python3-cryptography python3-pillow python3-bcrypt
```
### Arch
```
sudo pacman -S python python-requests python-beautifulsoup4 python-cryptography python-pillow python-bcrypt
```

### OpenSUSE
```
sudo zypper install python3 python3-tk python3-requests python3-beautifulsoup4 python3-cryptography python3-Pillow python3-bcrypt
```

### Gentoo
```
sudo emerge --ask dev-lang/python dev-python/requests dev-python/beautifulsoup4 dev-python/cryptography media-libs/pillow dev-python/bcrypt
```

### Windows
I've put some code into making a potential Window port at some point or have someone come along and contribute to this as a stepping stone for them. However, currently this isn't made with Windows completely in mind. Windows is sort of the second class citizen, sorry. If you wish to try this on Windows. I suggest using WSL and downloading a Linux Distro into Windows.

# How To Use
### Extra Folder
If you have any files like the "configs.overlay.ini", "sounds" (folder), "fonts" (folder), or other files this script does not make those or download sounds for you. It does make a "configs.user.ini" but that is based on your entries in the application currently via settings. Place all desired files inside the Extra folder located in the folder where SSG.py is at. Every time you run SSG.py it will copy those files into the "steam_settings" folder for each game.

## How to use as executable
1. Make sure the application is executable. chmod +x or prompts in your GUI for your DE/WM/Compositor.
2. Go to SteamDB and find your game.
3. Start the program and have the GUI open.
4. Save the page as an html file to the HTML folder inside the folder where SSG.py is located.
5. Follow any of the prompts that might appear.
6. Click the Directory Path link on the application or Navigate to the Games folder near SSG.py.
7. Done.

### Settings Menu
This is currently only in the application and not terminal based yet. Though if you want this to create you a "configs.user.ini" for your games. Click "Enable User Config" inside of the settings menu. Enter in all your desired information and it will create the "configs.user.ini" for you with the information you entered. Now each and every game will have this file placed inside the "steam_settings" folder.

### General Settings
You can mange general settings and choose to use a Steam API key or not. It's not required, but it is a extra addition if you choose to use it. Steam API Keys are stored encrypted in a file. If you open it the file will not show your actual key just a bunch of junk. Then once in the application it stars the key out so it's not directly visible. The application does decrypt the key for using the Steam API as Steam will not accept the encrypted key. However, once it is done using it, it instantly encrypts the data again.

<img width="780" height="386" alt="SSG_GC" src="https://github.com/user-attachments/assets/c0a0af37-1bb1-4ed5-a415-030c940b09c3" />

### Steam Settings Users Config File
<img width="797" height="483" alt="image" src="https://github.com/user-attachments/assets/21b85cb4-a08a-4256-8599-67cefcc98ae3" />

### Download Manager
You can now manage emulators versions and potential other things if there is anything else to be added down the road. Steam-Settings-Generator now has the ability for you to decide what you want. No more auto update to the latest and backup of the old. If there is an issue in one version, you can skip it. Just click the download and anything installed will be checked off. Click the "X" and it will delete the installed version, returning it to the previous state. This download manager is also cached so you are not timing out do to constant looking at the releases. 

***Steam Settings Generator downloads these for you, but we do not directly supply support for third-party software. Any issues about the downloads that aren't strictly a problem with downloading said files will be closed. Support for third-party software should be found with the given software developers of said downloaded third-party software.***

<img width="786" height="381" alt="SSG_DLM" src="https://github.com/user-attachments/assets/334038dd-6c2f-457e-9e20-e4e2145b7e84" />


## Existing Files at 0% with the GUI
1. Press the "Attention Button" on the 0% game entry.
2. Follow the prompts.
3. Done
<img width="761" height="90" alt="image" src="https://github.com/user-attachments/assets/777bd42e-346d-4d6f-879e-acab74095a56" />

## Redo Existing Files at 100% with the GUI
1. Press the "Attention Button" on the complete game with 100% on the game entry.
2. Press "Reprocess HTML" in the menu.
3. Follow any prompts.
4. Done
<img width="152" height="75" alt="image" src="https://github.com/user-attachments/assets/d5bb934b-d19e-4304-8fac-22e47475255b" />

## Delete Existing Files with GUI
1. Press the trashcan ison next to the finished game entry.
2. Done.
<img width="752" height="86" alt="image" src="https://github.com/user-attachments/assets/66cbdc9f-32ed-4e98-ae31-9d486179306c" />

## Mass Delete Existing Files with GUI
1. Press the Mass Delete ison at the top left above the list.
2. Follow promts.
3. Done.
<img width="77" height="52" alt="image" src="https://github.com/user-attachments/assets/1e508758-b620-43e5-888e-9b142345f586" />

## Process Game
<img width="626" height="51" alt="image" src="https://github.com/user-attachments/assets/c6ae7e7a-6ca5-4c42-8b1c-67efa0a2bcc2" />

You need "tkdnd" for drag and drop, but there is a fallback. If this doesn't work CTRL+C of the executable then focus the application and press CTRL+V and it pastes the executable into the application.
<img width="762" height="339" alt="image" src="https://github.com/user-attachments/assets/cb86bc2f-b57a-4d6c-b323-bad1236bd934" />
<img width="729" height="331" alt="image" src="https://github.com/user-attachments/assets/8b15b9f2-73ee-46d4-9661-641ce8df01e2" />

File Exploring is also an option.

<img width="604" height="321" alt="image" src="https://github.com/user-attachments/assets/32c7ccd5-cded-420e-98dd-aead83e8f074" />

## How to use in terminal
1. Go to SteamDB and find your game.
2. Save the page as an HTML file anywhere on your PC.
4. Open a terminal write "python SSG.py /path-to-html/*.html".
5. Let the script run and follow the prompts if any appear.
6. Go to the "Games" folder beside SSG.py. The files will be in this folder.
7. Done

If you use this and want a means to track your achievement process.
- https://github.com/Elite-Alien/Achievement-Viewer

## Disclaimer Statement
Steam Settings Generator is an independent, open‑source application. It is not affiliated with, endorsed by, or sponsored by Valve Corporation, Steam, or any of Valve’s subsidiaries or related entities. The developers of Steam Settings Generator make no claim to ownership, trademark, or any other intellectual‑property rights in Valve’s products, services, or brand assets.

### No Association or Endorsement
- The software does not use any proprietary Valve code, APIs, or assets beyond publicly available information.
- Any references to “Steam” are solely descriptive of the service the application interacts with for user convenience.
- Valve, Steam, and their logos remain the exclusive property of Valve Corporation.

### Ownership and Rights
- All rights to Steam Settings Generator reside with its open‑source contributors.
- The application does not claim any ownership, license, or other rights to Valve’s software, trademarks, or patents.

### Intended Use
- The tool is provided as‑is to facilitate configuration tasks for users of Steam‑based games that the user legally purchased.
- It is intended for personal, non‑commercial use and may be integrated with other open‑source projects at the user’s discretion.
- Steam Settings Generator does not condone, support, or facilitate piracy of any software, games, or digital content. It is solely a utility for managing legally purchased games.
