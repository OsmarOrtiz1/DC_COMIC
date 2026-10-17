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

CATEGORY_NAME = "Race"

CATEGORY_URL = "https://dc.fandom.com/wiki/Category:Races?from=Kangas"

OUTPUT_FILE = "data/keywords_associations.csv"


#=====================================================
# CONFIGURE CHROME
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


driver.get(CATEGORY_URL)

# ACCEPT COOKIES

try:

    accept_button = WebDriverWait(driver, 10).until(

        EC.element_to_be_clickable(
            (By.ID, "onetrust-accept-btn-handler")
        )

    )

    accept_button.click()

    print(" Cookies accepted.")

except Exception:

    print(" Cookie banner not found.")


page = 1

data = []

#=====================================================
#HUMAN VERIFICATION
#=====================================================

input(
    "\n"
    "If Cloudflare verification appears, complete it now.\n"
    "Once the character category is visible, press ENTER to continue..."
)

#=====================================================
# SCRAPE ALL PAGES
#=====================================================

while True:

    print("\n" + "=" * 70)
    print(f"PAGE {page}")
    print("=" * 70)

    try:

        WebDriverWait(driver, 20).until(

            EC.presence_of_element_located(
                (By.CSS_SELECTOR,
                 "a.category-page__member-link")
            )

        )

    except TimeoutException:

        print("Timed out waiting for page.")

        break


    soup = BeautifulSoup(
        driver.page_source,
        "html.parser"
    )

    titles = soup.find_all(

        "a",

        class_="category-page__member-link"

    )

    print(f"Found {len(titles)} entries")


    for item in titles:

        data.append({

            "keyword": item.text.strip(),

            "url":
                "https://dc.fandom.com"
                + item["href"],

            "category": CATEGORY_NAME

        })


    # NEXT PAGE

    try:

        next_button = WebDriverWait(driver, 10).until(

            EC.element_to_be_clickable(

                (
                    By.CSS_SELECTOR,
                    "a.category-page__pagination-next"
                )

            )

        )

        driver.execute_script(

            "arguments[0].scrollIntoView({block:'center'});",

            next_button

        )

        time.sleep(1)

        next_button.click()

        print("Next page")

        time.sleep(3)

        page += 1

    except Exception:

        print("No more pages.")

        break


driver.quit()


#=====================================================
# CREATE DATAFRAME
#=====================================================

new_df = pd.DataFrame(data)

print("\n" + "=" * 70)
print("NEW DATA")
print("=" * 70)

print(new_df.head())

print(f"\nNew records: {len(new_df):,}")


#=====================================================
# LOAD EXISTING FILE
#=====================================================

try:

    existing_df = pd.read_csv(OUTPUT_FILE)

    print(f"Existing records: {len(existing_df):,}")

except FileNotFoundError:

    print("Creating new association file.")

    existing_df = pd.DataFrame(

        columns=[

            "keyword",

            "url",

            "category"

        ]

    )


#=====================================================
# APPEND
#=====================================================

combined_df = pd.concat(

    [

        existing_df,

        new_df

    ],

    ignore_index=True

)


#=====================================================
# REMOVE DUPLICATES
#=====================================================

before = len(combined_df)

combined_df = combined_df.drop_duplicates(

    subset=[

        "keyword",

        "url"

    ]

)

after = len(combined_df)

print(f"\nDuplicates removed: {before-after:,}")

print(f"Total records: {after:,}")


#=====================================================
# SUMMARY AND SAVE
#=====================================================

print("\n" + "=" * 70)
print("CATEGORY SUMMARY")
print("=" * 70)

print(

    combined_df["category"]

    .value_counts()

)

print("\nUnique keywords:", combined_df["keyword"].nunique())

print("Unique URLs:", combined_df["url"].nunique())


combined_df.to_csv(

    OUTPUT_FILE,

    index=False,

    encoding="utf-8"

)

print("\n File updated!")

print(OUTPUT_FILE)
