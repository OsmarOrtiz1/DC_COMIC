import os
import re
import time

import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

from webdriver_manager.chrome import ChromeDriverManager

#=====================================================
# Configure Chrome
#=====================================================
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

#=====================================================
# Create output folder
#=====================================================
os.makedirs("data/comic_pages", exist_ok=True)

#=====================================================
# Read comic list and save
#=====================================================
comics = pd.read_csv("data/dc_comics.csv")

print(f"Found {len(comics)} comics to download.")

def safe_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

#=====================================================
# Download pages
#=====================================================
for index, row in comics.iterrows():

    title = row["title"]
    url = row["url"]

    filename = safe_filename(title) + ".html"
    filepath = os.path.join("data/comic_pages", filename)

    if os.path.exists(filepath):
        print(f"[{index+1}/{len(comics)}] Skipping: {title}")
        continue

    print(f"\n[{index+1}/{len(comics)}]")
    print(title)
    print(url)

    try:

        driver.get(url)

        time.sleep(3)

        html = driver.page_source

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        print(" Saved")

    except Exception as e:

        print(f"Failed: {title}")
        print(type(e).__name__)
        print(e)

print("Download complete.")

driver.quit()