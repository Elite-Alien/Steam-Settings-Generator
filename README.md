# Steam-Settings-Generator
This tool creates the achievements and DLC files needed for steam_settings, downloads achievement images, makes the steam_appid.txt, and copies over your defined files into a steam_settings folder.

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

## Delete Existing Files with GUI
1. Press the trashcan ison next to the finished game entry.
2. Done.
<img width="752" height="86" alt="image" src="https://github.com/user-attachments/assets/66cbdc9f-32ed-4e98-ae31-9d486179306c" />

The GUI is very basic. It's nothing more than a simple window that runs while you save files. Though wih this method you can keep it running and marathon style many games in a matter of minutes. Once your done, close it and it will stop checking for HTML files. Currently the second button, the one left of the trashcan is a place holder. I planned to make it something in the future update, but not sure which to do yet. It may just end up being removed. Because it might not be worth it. So for now it does nothing.

## How to use in terminal
1. Go to SteamDB and find your game.
2. Save the page as an HTML file anywhere on your PC.
4. Open a terminal write "python SSG.py /path-to-html/*.html".
5. Let the script run and follow the prompts if any appear.
6. Go to the "Games" folder beside SSG.py. The files will be in this folder.
7. Done

Warning
-------
Use this at your own risk. This shouldn't harm a system in any way, shape or form, but I will not be blamed for neglagence on your part. This is just a combination of my two original scripts, with modifications. I wrote these for myself to get Achievements, Achievement Images, and DLC files made and expanded it to this one overall tool. I am putting this up to let anyone who wishes to use it at their own risk. I will maybe supply a little support, but I will be very hands off with this. As it functions as intended if you have the correct python libraries installed on your Linux machine.

If you want to make changes, fork this and add them. Share your changes with others and let whomever that wishes to use it use those features. That is all. Happy gaming.
