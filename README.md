# Steam-Settings-Generator
This tool creates the achievements and DLC files needed for steam_settings, downloads achievement images, makes the steam_appid.txt, and copies over your defined files into a steam_settings folder.

If you want just the DLC or Achievement scraping by itself see my modular entries.
1. https://github.com/Elite-Alien/Steam-Achievement-Scrapper/tree/main
2. https://github.com/Elite-Alien/Steam-DLC-Scraper/tree/main

This script is basically those two combined but slightly altered in some areas. Along with added extra perks. I wrote this sloppy code on a whim because some of the other tools were broken and some could not even get data of delisted games. My adaptation of this will get delisted games and it tries to prevent you from timing yourself out on image downloads. As a SteamDB and Steam servers if scraping their images constantly you can get a timeout. So it logs the HTML file so you don't accedently do it twice and end up downloading them again. It does still grab the text information, as that is all located in the saved HTML file you have on your local storage.

### Extra Folder
If you have any files like the "configs.overlay.ini", "sounds" (folder), "fonts" (folder), "configs.user.ini", or other files this script does not make. Create a folder called ".extra" next to "SSG.py". (Yes "." before extra.) Place all desired files inside the folder. Every time you run SSG.py it will copy those files into the "steam_settings" folder for each game. The reasoning behind making the "extra" folder hidden is so you don't have a mistake happen when deleting other files. It gets out of the way and you will not misclick when doing a "shift + delete" or something. Nuking the extra folder while only wanting to delete a handful of your game folders that sit near it.

## How to use as executable
1. Make sure the application is executable. chmod +x or prompts in your GUI for your DE/WM/Compositor.
2. Go to SteamDB and find your game.
3. Save the page as an html file to the same folder SSG.py is in while running the application.
4. Follow any of the prompts that might appear.
5. Done.

The GUI is very basic. It's nothing more than a simple Zenith/Zenity style window that runs while you save files. Though wih this method you can keep it running and marathon style many games in a matter of minutes. Once your done, close it and it will stop checking for HTML files.

## How to use in terminal
1. Go to SteamDB and find your game.
2. Save the page as an HTML file anywhere on your PC.
4. Open a terminal write "python SSG.py /path-to-html/*.html".
5. Let the script run and follow the prompts if any appear.
6. Done

Warning
-------
Use this at your own risk. This shouldn't harm a system in any way, shape or form, but I will not be blamed for neglagence on your part. This is just a combination of my two original scripts, with modifications. I wrote these for myself to get Achievements, Achievement Images, and DLC files made and expanded it to this one overall tool. I am putting this up to let anyone who wishes to use it at their own risk. I will maybe supply a little support, but I will be very hands off with this. As it functions as intended if you have the correct python libraries installed on your Linux machine.

If you want to make changes, fork this and add them. Share your changes with others and let whomever that wishes to use it use those features. That is all. Happy gaming.
