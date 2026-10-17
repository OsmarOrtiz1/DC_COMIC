import os
import time
import pandas as pd

from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from selenium.common.exceptions import TimeoutException

from webdriver_manager.chrome import ChromeDriverManager


#=====================================================
# SETTINGS
#=====================================================

BASE_URL = "https://dc.fandom.com"

START_URL = "https://dc.fandom.com/wiki/Category:Characters"

OUTPUT_FILE = "data/dc_characters.csv"


#=====================================================
# BROWSER SETUP
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
});
""")

print("Opening character category...")

driver.get(START_URL)


#=====================================================
# ACCEPT COOKIES
#=====================================================

try:

    accept_button = WebDriverWait(driver, 5).until(
        EC.element_to_be_clickable(
            (By.ID, "onetrust-accept-btn-handler")
        )
    )

    accept_button.click()

    print("Cookies accepted.")

except Exception:

    print("Cookie banner not found.")


#=====================================================
# INITIAL HUMAN VERIFICATION (this can be annoying but I didnt know what else to do to deal with the cloudflare)
#=====================================================

input(
    "\n"
    "If Cloudflare verification appears, complete it now.\n"
    "Once the character category is visible, press ENTER to continue..."
)


#=====================================================
# STORAGE
#=====================================================

characters = []

page = 1


#=====================================================
# MAIN LOOP
#=====================================================

while True:

    print("\n" + "=" * 70)
    print(f"PAGE {page}")
    print("=" * 70)

    try:

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "a.category-page__member-link"
                )
            )
        )

    except TimeoutException:

        print(" Timed out waiting for character links.")

        with open(
            "character_error_page.html",
            "w",
            encoding="utf-8"
        ) as f:

            f.write(driver.page_source)

        print("Saved character_error_page.html")

        break


    
    # PARSE CURRENT PAGE
    

    soup = BeautifulSoup(
        driver.page_source,
        "html.parser"
    )


    links = soup.find_all(
        "a",
        class_="category-page__member-link"
    )


    print(f"Found {len(links)} characters")


    # EXTRACT CHARACTERS

    for link in links:

        name = link.get_text(
            " ",
            strip=True
        )

        href = link.get("href")


        if not name or not href:
            continue


        if href.startswith("/"):

            url = BASE_URL + href

        else:

            url = href


        characters.append({

            "character": name,

            "character_url": url

        })


    # FIND NEXT BUTTON

    try:

        next_button = WebDriverWait(driver, 10).until(

            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "a.category-page__pagination-next"
                )
            )

        )

        print("Next button found.")


        
        # SCROLL NEXT BUTTON INTO VIEW (this helped me to deal with the human verification)

        driver.execute_script(
            """
            arguments[0].scrollIntoView({
                block: 'center'
            });
            """,
            next_button
        )


        time.sleep(0.5)

        
        # CLICK NEXT

        driver.execute_script(
            "arguments[0].click();",
            next_button
        )

        print("Clicked Next.")


        # GIVE CLOUDFLARE TIME TO APPEAR

        print(
            "Waiting 2 seconds for possible verification..."
        )

        time.sleep(2)


        # MANUAL VERIFICATION

        input(
            "\n"
            "If a human verification appeared, complete it now.\n"
            "When the next page is ready, press ENTER to continue..."
        )


        # WAIT FOR NEXT PAGE

        print(
            "Waiting for character links..."
        )

        WebDriverWait(driver, 20).until(

            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "a.category-page__member-link"
                )
            )

        )


        print("Next page ready.")

        page += 1


    except TimeoutException:

        print("\nNo more pages.")

        break


    except Exception as e:

        print("\n========== ERROR ==========")

        print(type(e).__name__)
        print(e)

        with open(
            "character_error_page.html",
            "w",
            encoding="utf-8"
        ) as f:

            f.write(driver.page_source)

        print("Saved character_error_page.html")

        break

# CLOSE BROWSER

driver.quit()

#=====================================================
# CREATE DATAFRAME
#=====================================================

df = pd.DataFrame(characters)


#=====================================================
# REMOVE DUPLICATES AND SORT
#=====================================================

print("\nRemoving duplicates...")

if len(df) > 0:

    df = df.drop_duplicates(
        subset=["character_url"]
    )

if len(df) > 0:

    df = df.sort_values(
        "character"
    ).reset_index(
        drop=True
    )


#=====================================================
# CREATE OUTPUT FOLDER AND SAVE
#=====================================================

os.makedirs(
    "data",
    exist_ok=True
)

df.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8"
)


#=====================================================
# SUMMARY
#=====================================================

print("\n")
print("=" * 70)
print("FINISHED")
print("=" * 70)

print(
    f"Unique characters: {len(df):,}"
)

print(
    f"Saved to: {OUTPUT_FILE}"
)


print("\nFirst 20:")

if len(df) > 0:

    print(
        df.head(20).to_string(index=False)
    )