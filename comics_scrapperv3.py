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
# Start page 
#=====================================================
driver.get("https://dc.fandom.com/wiki/Category:Comics")


#=====================================================
# Accept cookies
#=====================================================
try:
    accept_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable(
            (By.ID, "onetrust-accept-btn-handler")
        )
    )

    accept_button.click()

    print("Cookies accepted.")

except Exception:
    print("Cookie banner not found.")


page = 1
data = []


#=====================================================
# Main loop
#=====================================================
while True:

    print(f"\n========== PAGE {page} ==========")

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a.category-page__member-link")
            )
        )
    except TimeoutException:
        print("Timed out waiting for comic links.")
        break

    print("Page title:", driver.title)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    titles = soup.find_all(
        "a",
        class_="category-page__member-link"
    )

    print(f"Found {len(titles)} comics")

    if len(titles) == 0:

        with open(f"page_{page}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        print(f"Saved page_{page}.html")

        break

    for comic in titles:

        data.append({
            "title": comic.text.strip(),
            "url": "https://dc.fandom.com" + comic["href"]
        })

    #=====================================================
    # Find and click Next
    #=====================================================
    try:

        next_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "a.category-page__pagination-next")
            )
        )

        print("Next button found!")

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            next_button
        )

        time.sleep(1)

        next_button.click()

        print("Clicked Next!")

        # Wait until the next page has loaded

        time.sleep(3)

        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a.category-page__member-link")
            )
        )

        page += 1

    except Exception as e:

        print("\n========== ERROR ==========")
        print(type(e).__name__)
        print(e)

        with open("error_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        print("Saved error_page.html")

        break


#=====================================================
# Finish
#=====================================================
driver.quit()

df = pd.DataFrame(data)

print("\n===========================")
print(df.head())

print(f"\nTotal comics scraped: {len(df)}")

df.to_csv("dc_comics.csv", index=False)

print("CSV saved!")
