from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
import time
import os
import urllib.request
import urllib.error

URL = "http://127.0.0.1:8001/health"

def main():
    # ensure screenshots directory exists
    os.makedirs('screenshots', exist_ok=True)
    # Quick-path: if URL is a health endpoint, do a simple HTTP GET and print result
    if URL.rstrip('/').endswith('/health'):
        try:
            with urllib.request.urlopen(URL, timeout=5) as r:
                body = r.read().decode('utf-8')
                print('HEALTH_OK')
                print(body)
        except urllib.error.URLError as e:
            print('HEALTH_FAIL')
            print(e)
        return

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1200,800")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    driver.get(URL)
    try:
        # wait up to 20s for the page to set window._uptimeChart (set by snapshots.js)
        WebDriverWait(driver, 20).until(lambda d: d.execute_script("return (typeof Chart !== 'undefined') && (window._uptimeChart !== undefined && window._uptimeChart !== null);"))
        print("CHART_RENDERED: OK")
        driver.save_screenshot('screenshots/chart.png')
    except TimeoutException:
        print("CHART_RENDERED: TIMEOUT")
        # save page for debugging
        with open('screenshots/page.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
    finally:
        driver.quit()

if __name__ == '__main__':
    main()
