# Steam Settings Generator
This tool creates the achievements.json, DLC files, downloads achievement images, makes the steam_appid.txt, and various other files it can make and obtain to place within the steam_settings folder or for use with the "steam_settings".

If you want just the DLC or Achievement scraping by itself see my modular entries.
1. https://github.com/Elite-Alien/Steam-Achievement-Scrapper/tree/main
2. https://github.com/Elite-Alien/Steam-DLC-Scraper/tree/main

This script is basically those two combined but slightly altered in some areas. Along with added extra perks. I wrote this sloppy code on a whim because some of the other tools were broken and some could not even get data of delisted games. My adaptation of this will get delisted games and it tries to prevent you from timing yourself out on image downloads. As a SteamDB and Steam servers if scraping their images constantly you can get a timeout. So it logs the HTML file so you don't accedently do it twice and end up downloading them again. It does still grab the text information, as that is all located in the saved HTML file you have on your local storage.

### Extra Folder
If you have any files like the "configs.overlay.ini", "sounds" (folder), "fonts" (folder), or other files this script does not make those or download sounds for you. It does make a "configs.user.ini" but that is based on your entries in the application currently via settings. Place all desired files inside the Extra folder located in the folder where SSG.py is at. Every time you run SSG.py it will copy those files into the "steam_settings" folder for each game.

### Settings Menu
This is currently only in the application and not terminal based yet. Though if you want this to create you a "configs.user.ini" for your games. Click "Enable User Config" inside of the settings menu. Enter in all your desired information and it will create the "configs.user.ini" for you with the information you entered. Now each and every game will have this file placed inside the "steam_settings" folder.

<img width="797" height="483" alt="image" src="https://github.com/user-attachments/assets/21b85cb4-a08a-4256-8599-67cefcc98ae3" />

## How to use as executable
1. Make sure the application is executable. chmod +x or prompts in your GUI for your DE/WM/Compositor.
2. Go to SteamDB and find your game.
3. Start the program and have the GUI open.
4. Save the page as an html file to the HTML folder inside the folder where SSG.py is located.
5. Follow any of the prompts that might appear.
6. Click the Directory Path link on the application or Navigate to the Games folder near SSG.py.
7. Done.

## Existing Files at 0% with the GUI
1. Press the "Attention Button" on the 0% game entry.
2. Follow the prompts.
3. Done
<img width="761" height="90" alt="image" src="https://github.com/user-attachments/assets/777bd42e-346d-4d6f-879e-acab74095a56" />

## Redo Existing Files at 100% with the GUI
1. Press the "Attention Button" on the 0% game entry.
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

The GUI is very basic. It's nothing more than a simple window that runs while you save files. Though wih this method you can keep it running and marathon style many games in a matter of minutes. Once your done, close it and it will stop checking for HTML files. Currently the second button, the one left of the trashcan is a place holder. I planned to make it something in the future update, but not sure which to do yet. It may just end up being removed. Because it might not be worth it. So for now it does nothing.

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
