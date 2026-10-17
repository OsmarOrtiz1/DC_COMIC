import os
import re
import time

import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

from webdriver_manager.chrome import ChromeDriverManager

# ----------------------------------------------------
# Configure Chrome
# ----------------------------------------------------
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

driver.execute_script("""
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
})
""")

# ----------------------------------------------------
# Create output folder
# ----------------------------------------------------
os.makedirs("data/comic_pages", exist_ok=True)

# ----------------------------------------------------
# Read comic list
# ----------------------------------------------------
comics = pd.read_csv(r"C:\Users\26472724\OneDrive - University of Limerick\Desktop\PhD UL\dc_comics.csv")

print(f"Found {len(comics)} comics to download.")

# ----------------------------------------------------
# Files to redownload
# ----------------------------------------------------

FILES_TO_REDOWNLOAD = {
    # Security-check pages
    "Absolute Superman Vol 1 13.html",
    "Action Comics Vol 1 1022.html",
    "Action Comics Vol 1 1023.html",
    "Action Comics Vol 1 227.html",
    "Action Comics Vol 1 352.html",
    "Action Comics Vol 1 353.html",
    "Action Comics Vol 1 480.html",
    "Action Comics Vol 1 481.html",
    "Action Comics Vol 1 609.html",
    "Action Comics Vol 1 732.html",
    "Action Comics Vol 1 850.html",
    "Action Comics Vol 1 91.html",
    "Action Comics Vol 1 92.html",
    "Action Comics Vol 2 39.html",
    "Adventure Comics Vol 1 185.html",
    "Adventure Comics Vol 1 311.html",
    "Adventures of Bob Hope Vol 1 90.html",
    "Adventures of Bob Hope Vol 1 91.html",
    "Adventures of Bob Hope Vol 1 92.html",
    "Adventures of Jerry Lewis Vol 1 102.html",
    "Adventures of Superman Vol 1 474.html",
    "Adventures of Superman Vol 1 475.html",
    "All Top Comics Vol 1 10.html",
    "All-American Men of War Vol 1 23.html",
    "All-Flash Vol 1 15.html",
    "All-Star Comics Vol 1 55.html",
    "All-Star Comics Vol 1 56.html",
    "All-Star Comics Vol 1 57.html",
    "All-Star Comics Vol 1 58.html",
    "All-Star Comics Vol 1 59.html",
    "All-Star Western Vol 1 98.html",
    "American Century Vol 1 2.html",
    "American Century Vol 1 3.html",
    "Animal Man Vol 1 18.html",
    "Arion Lord of Atlantis Vol 1 29.html",
    "Arion Lord of Atlantis Vol 1 30.html",
    "Batgirl Vol 5 12.html",
    "Batgirl Vol 5 13.html",
    "Batman Confidential Vol 1 9.html",
    "Batman Gallery Vol 1 1.html",
    "Batman Legends of the Dark Knight Vol 1 190.html",
    "Batman Legends of the Dark Knight Vol 1 45.html",
    "Batman Legends of the Dark Knight Vol 1 46.html",
    "Batman Scottish Connection.html",
    "Batman Vol 1 13.html",
    "Batman Vol 1 153.html",
    "Batman Vol 1 289.html",
    "Batman Vol 1 438.html",
    "Batman Vol 1 577.html",
    "Batman and Robin Vol 2 40.html",
    "Batman and Robin Vol 3 1.html",
    "Batman and the Outsiders Vol 2 40.html",
    "Batman vs. Ra's al Ghul Vol 1 4.html",
    "Beware the Batman Vol 1 5.html",
    "Beware the Batman Vol 1 6.html",
    "Beware the Creeper Vol 1 1.html",
    "Birds of Prey Vol 1 113.html",
    "Black Adam The Dark Age Vol 1 1.html",
    "Black Magic Vol 1 9.html",
    "Blackhawk Vol 3 16.html",
    "Blue Beetle Vol 1 13.html",
    "Blue Beetle Vol 8 9.html",
    "Books of Magick Life During Wartime Vol 1 11.html",
    "Brightest Day Vol 1 10.html",
    "Captain Marvel Adventures Vol 1 70.html",
    "Captain Marvel, Jr. Vol 1 56.html",
    "Cartoon Network Action Pack Vol 1 38.html",
    "Cartoon Network Action Pack Vol 1 39.html",
    "Catwoman Vol 2 75.html",
    "Catwoman Vol 2 76.html",
    "Challengers of the Unknown Vol 1 80.html",
    "Challengers of the Unknown Vol 1 81.html",
    "Codename Knockout Vol 1 8.html",
    "Comic Cavalcade Vol 1 56.html",
    "Convergence Harley Quinn Vol 1 2.html",
    "Cruel and Unusual Vol 1 2.html",
    "DC Nation Vol 2 3.html",
    "DC's Hawk-Girl Summer Vol 1 1.html",
    "Daphne Byrne Vol 1 4.html",
    "Daphne Byrne Vol 1 5.html",
    "Deathstroke Vol 4 40.html",
    "Detective Comics Vol 1 343.html",
    "Detective Comics Vol 1 344.html",
    "Detective Comics Vol 1 678.html",
    "Detective Comics Vol 1 982.html",
    "Detective Comics Vol 1 983.html",
    "Detective Comics Vol 1 984.html",
    "Detective Comics Vol 1 985.html",
    "Detective Comics Vol 1 99.html",
    "Doom Patrol Vol 2 60.html",
    "Doorway to Nightmare Vol 1 5.html",
    "Double Action Comics Vol 1 2.html",
    "Empire Vol 1 3.html",
    "Empire Vol 1 4.html",
    "FCBD 2017 Wonder Woman Special Edition Vol 1 1.html",
    "Fables Vol 1 17.html",
    "Falling in Love Vol 1 24.html",
    "Falling in Love Vol 1 25.html",
    "Fate Vol 1 1.html",
    "Finals Vol 1 1.html",
    "Finals Vol 1 2.html",
    "Finals Vol 1 3.html",
    "Freedom Fighters Vol 1 15.html",
    "Fury of Firestorm The Nuclear Men Vol 1 13.html",
    "G.I. Combat Vol 1 188.html",
    "G.I. Combat Vol 1 189.html",
    "G.I. Combat Vol 1 39.html",
    "Gotham Underground Vol 1 2.html",
    "Green Lantern Legacy Vol 1 1.html",
    "Green Lantern Vol 2 18.html",
    "Green Lantern Vol 3 86.html",
    "Gunfire Vol 1 4.html",
    "Harley's Little Black Book Vol 1 4.html",
    "Hawkworld Vol 2 5.html",
    "Hellblazer Vol 1 130.html",
    "I Am Batman Vol 1 7.html",
    "Infinite Crisis Special Day of Vengeance Vol 1 1.html",
    "JLA Heaven's Ladder.html",
    "JLA Island of Dr. Moreau.html",
    "Justice League United Annual Vol 1 1.html",
    "Knight Terrors Vol 1 2.html",
    "L.E.G.I.O.N. Vol 1 27.html",
    "Leading Comics Vol 1 1.html",
    "Legends of the DC Universe Vol 1 24.html",
    "Legion of Super-Heroes Vol 2 261.html",
    "Legion of Super-Heroes Vol 2 262.html",
    "Legion of Super-Heroes Vol 2 263.html",
    "Legion of Super-Heroes Vol 2 264.html",
    "Legion of Super-Heroes Vol 2 265.html",
    "Legionnaires 3 Vol 1 4.html",
    "Legionnaires Annual Vol 1 1.html",
    "Legionnaires Annual Vol 1 2.html",
    "Looney Tunes Vol 1 40.html",
    "Mighty Crusaders Vol 1 6.html",
    "Minute Man Vol 1 2.html",
    "Mystery in Space Vol 1 40.html",
    "New Gods Vol 1 16.html",
    "Night Force Vol 2 3.html",
    "Nightwing Vol 2 118.html",
    "Nightwing Vol 4 74.html",
    "Northlanders Vol 1 10.html",
    "Our Fighting Forces Vol 1 11.html",
    "Outsiders Vol 3 18.html",
    "Ravagers Vol 1 11.html",
    "Real Screen Comics Vol 1 62.html",
    "Real Screen Comics Vol 1 63.html",
    "Real Screen Comics Vol 1 64.html",
    "Real Screen Comics Vol 1 65.html",
    "Real Screen Comics Vol 1 66.html",
    "Real Screen Comics Vol 1 67.html",
    "Red Hood and the Outlaws Vol 2 22.html",
    "Resurrection Man Vol 1 23.html",
    "Robin Vol 2 169.html",
    "Robin Vol 2 19.html",
    "Robin Vol 2 20.html",
    "Sandman Vol 2 4.html",
    "Scalped Vol 1 10.html",
    "Secret Hearts Vol 1 76.html",
    "Secret Origins Vol 3 9.html",
    "Sgt. Rock Vol 1 305.html",
    "Star Trek The Next Generation Vol 2 1.html",
    "Star-Spangled Comics Vol 1 126.html",
    "Strange Adventures Vol 1 221.html",
    "Sugar and Spike Vol 1 56.html",
    "Super-Team Family Vol 1 11.html",
    "Supergirl Vol 4 25.html",
    "Supergirl Vol 8 4.html",
    "Supergirl Vol 8 5.html",
    "Superman & Bugs Bunny Vol 1 4.html",
    "Superman Family Vol 1 222.html",
    "Superman Vol 1 355.html",
    "Superman Vol 1 356.html",
    "Superman Vol 1 40.html",
    "Superman Vol 1 686.html",
    "Superman Vol 1 687.html",
    "Superman vs. Lobo Vol 1 1.html",
    "Tales of the Unexpected Vol 1 71.html",
    "Team Titans Vol 1 23.html",
    "Teen Titans Lost Annual Vol 1 1.html",
    "The Atom Vol 1 36.html",
    "The Atom Vol 1 37.html",
    "The Demon Vol 1 2.html",
    "The Final Night Preview.html",
    "The Flash Vol 1 254.html",
    "The Invisibles Vol 2 6.html",
    "The Lost Boys Vol 1 5.html",
    "The Unexpected Vol 1 219.html",
    "The Unexpected Vol 1 220.html",
    "The Unexpected Vol 1 221.html",
    "The Unexpected Vol 1 222.html",
    "The Young Magician Vol 1 2.html",
    "Totino's Presents Batman.html",
    "Trinity Vol 1 10.html",
    "Trinity Vol 1 11.html",
    "Trinity Vol 1 12.html",
    "Vigilante Southland Vol 1 1.html",
    "Whiz Comics Vol 1 50.html",
    "Who's Who in the DC Universe Vol 1 8.html",
    "Wonder Woman Vol 5 54.html",
    "World of Warcraft Curse of the Worgen Vol 1 2.html",
    "World's Finest Vol 1 210.html",
    "World's Finest Vol 1 314.html",
    "World's Finest Vol 1 315.html",
    "World's Finest Vol 1 97.html",
    "Young Heroes in Love Vol 1 3.html",
    "Young Justice Vol 3 17.html",
    "Young Romance Vol 1 186.html",

    # Server-error pages
    "Batman Vol 3 29.html",
    "SupermanBatman Vol 1 64.html",

    # Empty files
    "House of Mystery Vol 1 260.html",
    "New Super-Man and the Justice League of China Vol 1 24.html",
}

print(f"Files targeted: {len(FILES_TO_REDOWNLOAD)}")  # 211

# ----------------------------------------------------
# Safe filename
# ----------------------------------------------------
def safe_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

# ----------------------------------------------------
# Download pages
# ----------------------------------------------------
for index, row in comics.iterrows():

    title = row["title"]
    url = row["url"]

    filename = safe_filename(title) + ".html"
    filepath = os.path.join("data/comic_pages", filename)

    # ognore everything except the files we want to redownload
    if filename not in FILES_TO_REDOWNLOAD:
        continue

    print(f"\n[{index+1}/{len(comics)}]")
    print(title)
    print(url)

    try:

        driver.get(url)

        input(
            'check that the actual comic article has loaded in chrome. '
            'complete any security checks if necessary, then press enter to continue...'
        )

        html= driver.page_source
        page_title = driver.title.casefold()

        if any(text in page_title for text in (
            'just a moment',
            'attention required',
            'access denied',
            'connection time out',
            'internal server error',
        )):
            print(f" Not Saved: still blocked or showing an error - {title}")
            continue

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
            print(f"✅ Saved: {title}")

    except Exception as e:

        print(f"❌ Failed: {title}")
        print(type(e).__name__)
        print(e)

print("\n==========================")
print("Finished downloading all comics!")

driver.quit()