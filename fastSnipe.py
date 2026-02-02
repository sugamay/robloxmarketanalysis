#!/usr/bin/env python3
"""
roblox_fast_bot.py

Fast Rolimon's -> Roblox purchase bot with price verification.

Usage examples:
  python roblox_fast_bot.py                  # dry-run (default)
  python roblox_fast_bot.py --real           # perform real buys (dangerous)
  python roblox_fast_bot.py --poll 5 --real  # poll every 5s and perform buys

Requirements:
  pip install -r requirements.txt
  OR
  pip install selenium webdriver-manager python-dotenv beautifulsoup4
"""

import time
import re
import os
import logging
import argparse
from dotenv import load_dotenv
from selenium.webdriver import ActionChains


from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup  # kept as optional fallback parsing

# --- Configuration & helpers -------------------------------------------------
load_dotenv()
ROBLOX_SECURITY_COOKIE = os.getenv("ROBLOX_SECURITY_COOKIE")  # must be set in .env
maxPrice = 316
minDealPercentage = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def _to_int_from_text(text):
    """Extract digits safely from a text like '1,234' or '96 Robux'."""
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0






# --- Diagnostics helper (NO BEHAVIOR CHANGES) -----------------------------
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException

def diagnose_click_element(driver, css_selector, name_hint="diag", save_screenshot=False):

    """
    Non-invasive diagnostics: returns a dict with bounding rect, computed style,
    what element is at the center (elementFromPoint), and optionally saves a screenshot.
    Call right before/after clicks to prove what element would receive the click.
    """
    out = {"selector": css_selector, "time": time.time()}
    try:
        el = driver.find_element(By.CSS_SELECTOR, css_selector)
    except NoSuchElementException:
        out["error"] = "element_not_found"
        logging.info("[DIAG-%s] element not found for selector: %s", name_hint, css_selector)
        return out

    try:
        # bounding rect
        rect = driver.execute_script("return arguments[0].getBoundingClientRect().toJSON();", el)
        out["rect"] = rect

        # computed style snapshot
        style = driver.execute_script("""
            const s = window.getComputedStyle(arguments[0]);
            return {
              display: s.display,
              visibility: s.visibility,
              opacity: s.opacity,
              pointerEvents: s.pointerEvents,
              zIndex: s.zIndex
            };
        """, el)
        out["computed_style"] = style

        # center coordinates (viewport coords)
        cx = rect["left"] + rect["width"]/2
        cy = rect["top"] + rect["height"]/2
        out["center"] = {"x": cx, "y": cy}

        # element actually at that viewport point (outerHTML trimmed)
        top_outer = driver.execute_script(
            "const e = document.elementFromPoint(arguments[0], arguments[1]); return e ? e.outerHTML : null;",
            cx, cy
        )
        out["elementFromPoint_outerHTML_trim"] = (top_outer[:2000] + "...") if top_outer else None

        # top element meta
        top_info = driver.execute_script("""
            const e = document.elementFromPoint(arguments[0], arguments[1]);
            if (!e) return null;
            const s = window.getComputedStyle(e);
            return {tag: e.tagName, id: e.id || null, classes: e.className || null, text: (e.innerText||'').trim().slice(0,200), pointerEvents: s.pointerEvents, zIndex: s.zIndex, display: s.display, visibility: s.visibility};
        """, cx, cy)
        out["top_element_info"] = top_info

        out["selenium_displayed"] = el.is_displayed()
        try:
            out["selenium_enabled"] = el.is_enabled()
        except Exception:
            out["selenium_enabled"] = None

    except StaleElementReferenceException:
        out["error"] = "stale_element_reference"
    except Exception as e:
        out["error"] = f"exception_{type(e).__name__}"
        out["exception_msg"] = str(e)

    # save screenshot for offline inspection
    """ if save_screenshot:
        try:
            fname = f"diag_{name_hint}_{int(time.time())}.png"
            driver.save_screenshot(fname)
            out["screenshot"] = fname
            logging.info("[DIAG-%s] screenshot saved: %s", name_hint, fname)
        except Exception as e:
            out["screenshot_error"] = str(e) """

    # log summary (not too verbose)
    logging.info("[DIAG-%s] selector=%s rect=%s top=%s pointerEvents=%s z=%s displayed=%s enabled=%s",
                 name_hint,
                 css_selector,
                 ("{x:%.0f y:%.0f w:%.0f h:%.0f}" % (out["rect"]["left"], out["rect"]["top"], out["rect"]["width"], out["rect"]["height"])) if "rect" in out else "no-rect",
                 (out["top_element_info"]["tag"] + (("."+out["top_element_info"]["classes"].split()[0]) if out["top_element_info"] and out["top_element_info"].get("classes") else "")) if out.get("top_element_info") else "none",
                 out.get("top_element_info",{}).get("pointerEvents"),
                 out.get("top_element_info",{}).get("zIndex"),
                 out.get("selenium_displayed"),
                 out.get("selenium_enabled"))
    return out

# --- Selenium driver init ---------------------------------------------------
def init_driver(headless=True, disable_images=True):
    chrome_options = Options()
    if headless:
        # "new" headless mode in recent Chrome; falls back otherwise
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")

    if disable_images:
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_setting_values.media_stream": 2
        }
        chrome_options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver

# --- Authentication / warm-up ------------------------------------------------
def set_roblox_cookie(driver, cookie_value, wait_seconds=5):
    if not cookie_value:
        logging.error("ROBLOX_SECURITY_COOKIE not set. Put it in a .env file as ROBLOX_SECURITY_COOKIE=...")
        raise RuntimeError("Missing ROBLOX_SECURITY_COOKIE")
    driver.get("https://www.roblox.com")
    # Ensure page loaded
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    # Add cookie for domain
    driver.add_cookie({"name": ".ROBLOSECURITY", "value": cookie_value, "domain": ".roblox.com", "path": "/"})
    driver.refresh()
    logging.info("Set ROBLOX cookie and refreshed Roblox site. Waiting briefly...")
    time.sleep(wait_seconds)

def warm_dns_cache(driver):
    """Optional warm-up to reduce first navigation DNS/TLS overhead."""
    try:
        driver.execute_script("window.open('https://www.roblox.com', '_blank');")
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
            # wait briefly then close
            time.sleep(0.5)
            driver.close()
            driver.switch_to.window(handles[0])
        logging.info("Performed DNS/TLS warm-up for roblox.com.")
    except Exception:
        logging.debug("Warm-up failed or skipped.")

# --- Rolimon's page & filters ------------------------------------------------
def open_deals_page(driver, wait_timeout=12):
    driver.get("https://www.rolimons.com/deals")
    WebDriverWait(driver, wait_timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    # short pause to let JS render initial content
    time.sleep(0.7)
    logging.info("Loaded Rolimon's deals page.")

def set_filter_20_percent(driver, short_wait=6):
    """Click filter to select 20% or a similar filter. Best-effort; will not crash if it fails."""
    wait = WebDriverWait(driver, short_wait)
    try:
        dropdown = wait.until(EC.element_to_be_clickable((By.ID, "filter-category-dropdown")))
        dropdown.click()
        # The site's exact option label may change; we try a couple of fallbacks.
        try:
            option = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//a[@data-category='filter_below_20_percent' and contains(text(),'20%')]")))
            option.click()
        except Exception:
            # fallback: click the 20% link by textual contain
            option2 = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(),'20%')]")))
            option2.click()
        logging.info("Attempted to set 20% filter.")
    except Exception as e:
        logging.debug("Could not set filter (non-fatal): %s", e)

# --- Deal extraction (fast Selenium DOM path, fallback BeautifulSoup) ------
VALID_GRADIENTS = [
    "deal_bg_gradient_uncommon",
    "deal_bg_gradient_rare",
    "deal_bg_gradient_epic",
    "deal_bg_gradient_legendary"
]

def get_deals_via_selenium(driver):
    selector = ", ".join(f"div.{c}" for c in VALID_GRADIENTS)
    containers = driver.find_elements(By.CSS_SELECTOR, selector)
    deals = []
    for c in containers:
        try:
            # parent <a> with item URL
            url = None
            try:
                parent_a = c.find_element(By.XPATH, "./ancestor::a[1]")
                url = parent_a.get_attribute("href")
            except Exception:
                url = None

            title = "Unknown"
            try:
                title_el = c.find_element(By.CSS_SELECTOR, ".deal-title")
                title = title_el.get_attribute("title") or title_el.text.strip() or title
            except Exception:
                pass

            data = {}
            try:
                rows = c.find_elements(By.CSS_SELECTOR, ".mt-1.rounded-bottom .d-flex.justify-content-between")
                for row in rows:
                    try:
                        header = row.find_element(By.CSS_SELECTOR, ".stat-header").text.strip()
                        value = row.find_element(By.CSS_SELECTOR, ".stat-data").text.strip()
                        data[header] = value
                    except Exception:
                        continue
            except Exception:
                pass

            price = _to_int_from_text(data.get("Price", "0"))
            deal_percent = _to_int_from_text(data.get("Deal", "0%"))
            deals.append({
                "title": title,
                "price": price,
                "deal_percent": deal_percent,
                "raw_data": data,
                "url": url
            })
        except Exception:
            continue
    # If nothing found via DOM (rare), fallback to parsing page_source with BeautifulSoup
    if not deals:
        logging.debug("No deals via Selenium DOM; falling back to BeautifulSoup parsing.")
        deals = parse_deals_bs4(driver.page_source)
    return deals

def parse_deals_bs4(html):
    soup = BeautifulSoup(html, "html.parser")
    deals = []
    containers = soup.find_all("div", class_=lambda x: x and any(g in x for g in VALID_GRADIENTS))
    for container in containers:
        parent_a = container.find_parent("a")
        url = parent_a["href"] if parent_a and "href" in parent_a.attrs else None
        title_div = container.find("div", class_="deal-title")
        title = title_div.get("title") if title_div else "Unknown"
        info_div = container.find("div", class_="mt-1 rounded-bottom")
        if not info_div:
            continue
        rows = info_div.find_all("div", class_="d-flex justify-content-between")
        data = {}
        for row in rows:
            header_div = row.find("div", class_="stat-header")
            value_div = row.find("div", class_="stat-data")
            if header_div and value_div:
                header = header_div.get_text(strip=True)
                value = value_div.get_text(strip=True)
                data[header] = value
        price = _to_int_from_text(data.get("Price", "0"))
        deal_percent = _to_int_from_text(data.get("Deal", "0%"))
        deals.append({"title": title, "price": price, "deal_percent": deal_percent, "raw_data": data, "url": url})
    return deals

# --- Fast & safe purchase flow ----------------------------------------------
def buy_item_fast(driver, roblox_url, expected_price, dry_run=True, page_timeout=5, modal_timeout=4, click_delay=0.06):
    original_handle = driver.current_window_handle
    # open new tab
    driver.execute_script("window.open('about:blank', '_blank');")
    new_tab = [h for h in driver.window_handles if h != original_handle][-1]
    driver.switch_to.window(new_tab)

    # navigate quickly
    try:
        driver.get(roblox_url)
    except Exception as e:
        logging.warning("Navigation error to item page: %s", e)
        driver.close()
        driver.switch_to.window(original_handle)
        return False

    wait = WebDriverWait(driver, page_timeout)
    # Fast JS price read on item page
    page_price_text = None
    try:
        page_price_text = driver.execute_script(
            "const el = document.querySelector('div.item-price-value span.text-robux-lg'); return el ? el.innerText : null;"
        )
        page_price = _to_int_from_text(page_price_text)
    except Exception:
        page_price = 0

    if page_price == 0:
        # fallback to short explicit wait
        try:
            el = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "div.item-price-value span.text-robux-lg")))
            page_price = _to_int_from_text(el.text)
        except Exception:
            logging.info("Price not found quickly on item page; aborting buy.")
            driver.close()
            driver.switch_to.window(original_handle)
            return False

    logging.debug("Item page price: %s (raw: %s)", page_price, page_price_text)
    if page_price != expected_price:
        logging.info("Price mismatch on page (expected %s vs page %s). Aborting buy.", expected_price, page_price)
        driver.close()
        driver.switch_to.window(original_handle)
        return False

    # Click buy button quickly (JS click preferred) — with diagnostics (no behavior changes)
    try:
        # DIAG: before click
        try:
            diagnose_click_element(driver, "button.shopping-cart-buy-button.PurchaseButton", name_hint="before_item_click")
        except Exception as e:
            logging.debug("Diag-before click failed: %s", e)

        buy_btn = driver.execute_script("return document.querySelector('button.shopping-cart-buy-button.PurchaseButton');")
        if buy_btn:
            driver.execute_script("arguments[0].click();", buy_btn)
        else:
            buy_el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.shopping-cart-buy-button.PurchaseButton")))
            driver.execute_script("arguments[0].click();", buy_el)
        time.sleep(click_delay)
        logging.info("Clicked buy button (fast).")

        # DIAG: immediately after click — sample elementFromPoint and screenshot
        try:
            post_click_info = driver.execute_script("""
                try {
                  const sel = 'button.shopping-cart-buy-button.PurchaseButton';
                  const el = document.querySelector(sel);
                  const rect = el ? el.getBoundingClientRect() : {left:0,top:0,width:0,height:0};
                  const cx = rect.left + rect.width/2;
                  const cy = rect.top + rect.height/2;
                  const top = document.elementFromPoint(cx, cy);
                  return {center: {x:cx,y:cy}, top_tag: top ? top.tagName : null, top_classes: top ? top.className : null, top_outer: top ? (top.outerHTML ? top.outerHTML.slice(0,1500) : null) : null};
                } catch(e) { return {error: String(e)}; }
            """)
            logging.info("[DIAG-after_item_click] %s", str(post_click_info)[:2000])
            try:
                """ ss = f"diag_after_item_click_{int(time.time())}.png"
                driver.save_screenshot(ss)
                logging.info("[DIAG-after_item_click] screenshot saved: %s", ss) """
            except Exception:
                pass
        except Exception as e:
            logging.debug("Diag-after click failed: %s", e)

    except Exception as e:
        logging.warning("Could not click buy button: %s", e)
        driver.close()
        driver.switch_to.window(original_handle)
        return False

    # --- Robust wait for modal and verify modal price (replacement) ---
    try:
        modal_price_text = None
        modal_html_for_debug = None
        end_time = time.time() + modal_timeout

        # small extra pause sometimes helps on very fast runs
        time.sleep(0.04)

        while time.time() < end_time:
            res = driver.execute_script("""
                try {
                    function findNumberInText(txt) {
                        if (!txt) return null;
                        const m = txt.match(/(\\d{1,6})/);
                        return m ? m[1] : null;
                    }

                    const scopedSelectors = [
                        'div.modal-content',
                        'div.in.modal .modal-content',
                        'div.modal-window .modal-content',
                        '.modal-body',
                        '.modal-message',
                        '#confirm-btn'
                    ];

                    // 1) Try scoped selectors (look inside each)
                    for (const s of scopedSelectors) {
                        const el = document.querySelector(s);
                        if (!el) continue;

                        const spanSel = el.querySelector('span.text-robux, span.text-robux-lg, span.text-robux-md, span.text-robux-sm, span.icon-robux + span');
                        if (spanSel) {
                            const num = findNumberInText(spanSel.textContent || spanSel.innerText);
                            if (num) return {found:true, price: num, html: el.innerHTML};
                        }

                        const mm = el.querySelector('.modal-message') || el;
                        const n2 = findNumberInText(mm.innerText || mm.textContent);
                        if (n2) return {found:true, price: n2, html: el.innerHTML};
                    }

                    // 2) Global span fallback
                    const globalSpan = document.querySelector('span.text-robux, span.text-robux-lg, span.text-robux-md, span.text-robux-sm');
                    if (globalSpan) {
                        const n3 = findNumberInText(globalSpan.innerText || globalSpan.textContent);
                        if (n3) {
                            const modalAncestor = globalSpan.closest('.modal-content, .modal-window, .in.modal, .modal-body');
                            return {found:true, price: n3, html: modalAncestor ? modalAncestor.innerHTML : document.documentElement.innerHTML.slice(0,2000)};
                        }
                    }

                    // 3) elementFromPoint fallback (center / footer area of modal)
                    const modalBox = document.querySelector('div.modal-content, div.modal-window, div.in.modal');
                    if (modalBox) {
                        const rect = modalBox.getBoundingClientRect();
                        const cx = rect.left + rect.width/2;
                        const cy = Math.max(rect.top + 20, rect.top + rect.height - 30);
                        const topEl = document.elementFromPoint(cx, cy);
                        if (topEl) {
                            const num = findNumberInText((topEl.innerText || topEl.textContent));
                            if (num) return {found:true, price: num, html: modalBox.innerHTML};
                            const anc = topEl.closest('button, a, span, div');
                            if (anc) {
                                const n4 = findNumberInText(anc.innerText || anc.textContent);
                                if (n4) return {found:true, price: n4, html: modalBox.innerHTML};
                            }
                        }
                    }

                    // 4) TreeWalker fallback limited steps (robust but bounded)
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                    let node;
                    let steps = 0;
                    while ((node = walker.nextNode()) && steps < 400) {
                        steps++;
                        const txt = (node.nodeValue || '').trim();
                        const m = txt.match(/(\\d{1,6})/);
                        if (m) {
                            const anc = node.parentElement ? node.parentElement.closest('.modal-content, .modal-window, .in.modal, .modal-body') : null;
                            if (anc) return {found:true, price: m[1], html: anc.innerHTML};
                        }
                    }

                    return {found:false};
                } catch(e) { return {error: String(e)}; }
            """)
            if isinstance(res, dict):
                if res.get("error"):
                    logging.debug("Modal JS error: %s", res["error"])
                if res.get("found"):
                    modal_html_for_debug = res.get("html")
                    if res.get("price"):
                        modal_price_text = res.get("price")
                        break
            time.sleep(0.06)

        # XPath fallback (last resort)
        if not modal_price_text:
            try:
                xp_res = driver.execute_script("""
                    try {
                        const xp = "/html/body/div[20]/div[2]/div/div/div[2]/div[1]/span[2]";
                        const node = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (node && (node.innerText||'').trim()) return {price: node.innerText.trim(), html: node.parentElement ? node.parentElement.innerHTML : null};
                        return null;
                    } catch(e) { return {error: String(e)}; }
                """)
                if isinstance(xp_res, dict) and xp_res.get("error"):
                    logging.debug("XPath JS error: %s", xp_res["error"])
                elif isinstance(xp_res, dict) and xp_res.get("price"):
                    modal_price_text = xp_res.get("price")
                    if not modal_html_for_debug:
                        modal_html_for_debug = xp_res.get("html")
            except Exception:
                pass

        if not modal_price_text:
            """ if modal_html_for_debug:
                try:
                    fname = f"modal_debug_{int(time.time())}.html"
                    with open(fname, "w", encoding="utf-8") as fh:
                        fh.write(modal_html_for_debug)
                    logging.info("Saved modal HTML for debugging: %s", fname)
                except Exception as e:
                    logging.debug("Failed to save modal HTML: %s", e) """
            logging.info("Modal didn't present a readable price (JS poll timed out). Aborting.")
            driver.close()
            driver.switch_to.window(original_handle)
            return False

        modal_price = _to_int_from_text(modal_price_text)
    except Exception as e:
        logging.info("Error while waiting for modal: %s. Aborting.", e)
        driver.close()
        driver.switch_to.window(original_handle)
        return False

    logging.debug("Modal price: %s (raw: %s)", modal_price, modal_price_text)
    if modal_price != expected_price:
        logging.info("Modal price mismatch (expected %s vs modal %s). Aborting.", expected_price, modal_price)
        driver.close()
        driver.switch_to.window(original_handle)
        return False

    if dry_run:
        logging.info("[DRY-RUN] Would have clicked 'Buy Now' for %s Robux. Closing tab.", expected_price)
        driver.close()
        driver.switch_to.window(original_handle)
        return True

    # --- Final Buy Now click — robust attempts with diagnostics (includes buy-button XPath fallback) ---
    try:
        # DIAG before final click
        try:
            diagnose_click_element(driver, "button.modal-button.btn-primary-md.btn-min-width", name_hint="before_buynow_click")
        except Exception as e:
            logging.debug("Diag-before BuyNow failed: %s", e)

        clicked = driver.execute_script("""
            try {
                // 1) exact-class selector
                let sel = "div.modal-content button.modal-button.btn-primary-md.btn-min-width";
                let b = document.querySelector(sel);
                if (b && !b.disabled) { b.click(); return 'clicked-by-class'; }

                // 2) find button inside modal with text matching 'buy' (case-insensitive)
                const buttons = Array.from(document.querySelectorAll('div.modal-content button, button.modal-button'));
                b = buttons.find(x => /buy\\s*now|buy/i.test((x.innerText||'').trim()));
                if (b && !b.disabled) { b.click(); return 'clicked-by-text'; }

                // 3) try the buy-button XPath fallback
                const xp = "/html/body/div[20]/div[2]/div/div/div[3]/div[2]/button[1]";
                const node = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (node && (node.tagName === 'BUTTON' || node.closest && node.closest('button'))) {
                    const btn = node.tagName === 'BUTTON' ? node : node.closest('button');
                    if (btn && !btn.disabled) { btn.click(); return 'clicked-by-xpath'; }
                }

                // 4) fallback to elementFromPoint near modal footer
                const modal = document.querySelector('div.in.modal, div.modal-window, div.modal-content');
                if (modal) {
                    const rect = modal.getBoundingClientRect();
                    const cx = rect.left + rect.width/2;
                    const cy = rect.top + rect.height - 20;
                    const top = document.elementFromPoint(cx, cy);
                    if (top && (top.tagName === 'BUTTON' || (top.closest && top.closest('button')))) {
                        const btn = top.tagName === 'BUTTON' ? top : top.closest('button');
                        if (btn && !btn.disabled) { btn.click(); return 'clicked-by-elementFromPoint'; }
                    }
                }
                return null;
            } catch(e) { return 'error:' + String(e); }
        """)
        logging.info("BuyNow click attempt result: %s", clicked)

        # DIAG after final click
        try:
            post_final = driver.execute_script("""
                try {
                  const sel = 'button.modal-button.btn-primary-md.btn-min-width';
                  const el = document.querySelector(sel) || document.querySelector('div.modal-content button');
                  const rect = el ? el.getBoundingClientRect() : {left:0,top:0,width:0,height:0};
                  const cx = rect.left + rect.width/2;
                  const cy = rect.top + rect.height/2;
                  const top = document.elementFromPoint(cx, cy);
                  return {center: {x:cx,y:cy}, top_tag: top ? top.tagName : null, top_classes: top ? top.className : null, top_outer: top ? (top.outerHTML ? top.outerHTML.slice(0,1500) : null) : null};
                } catch(e) { return {error: String(e)}; }
            """)
            logging.info("[DIAG-after_buynow_click] %s", str(post_final)[:2000])
            try:
                """ ss2 = f"diag_after_buynow_click_{int(time.time())}.png"
                driver.save_screenshot(ss2)
                logging.info("[DIAG-after_buynow_click] screenshot saved: %s", ss2) """
            except Exception:
                pass
        except Exception as e:
            logging.debug("Diag-after BuyNow failed: %s", e)

    except Exception as e:
        logging.exception("Failed to click Buy Now: %s", e)
        driver.close()
        driver.switch_to.window(original_handle)
        return False

    # short pause to let purchase complete then close
    # --- WAIT AFTER BUY NOW ------------------------------------------------
    # If we clicked the BuyNow button (clicked is a string like 'clicked-by-class'),
    # wait 15 seconds so the site can finish the request and show confirmation.
    try:
        was_clicked = bool(clicked) and not (isinstance(clicked, str) and clicked.startswith("error"))
    except NameError:
        was_clicked = False

    if was_clicked:
        logging.info("BuyNow was clicked (%s). Waiting 15s for confirmation/round-trip...", clicked)
        # give the site up to 15s to finish (spinner -> success, balance change, etc.)
        time.sleep(15.0)
    else:
        logging.info("BuyNow was not clicked successfully (%s). Short wait then close.", clicked)

    # close tab and return
    try:
        driver.close()
    except Exception as e:
        logging.debug("Error closing tab: %s", e)
    try:
        driver.switch_to.window(original_handle)
    except Exception as e:
        logging.debug("Error switching back to original handle: %s", e)
    return True




# --- Main loop ---------------------------------------------------------------
def main_loop(headless=True, disable_images=True, poll_interval=6, refresh_interval=300, dry_run=True):
    driver = init_driver(headless=headless, disable_images=disable_images)
    try:
        set_roblox_cookie(driver, ROBLOX_SECURITY_COOKIE)
        warm_dns_cache(driver)
        open_deals_page(driver)
        set_filter_20_percent(driver)

        last_refresh = time.time()

        logging.info("Starting monitoring loop (poll_interval=%ss, dry_run=%s).", poll_interval, dry_run)
        while True:
            # periodic refresh to avoid session idles/popups
            if time.time() - last_refresh >= refresh_interval:
                logging.info("Refreshing Rolimon's deals page.")
                driver.refresh()
                time.sleep(0.8)
                last_refresh = time.time()
                set_filter_20_percent(driver)

            deals = get_deals_via_selenium(driver)
            logging.debug("Found %d deals.", len(deals))
            for d in deals:
                # your matching criteria: >=28% and price < 96 // test
                if d.get("deal_percent", 0) >= minDealPercentage and d.get("price", 999999) < maxPrice and d.get("url"):
                    logging.info("Qualifying deal: %s (%s Robux, %s%%) -> %s", d["title"], d["price"], d["deal_percent"], d["url"])
                    success = buy_item_fast(driver, d["url"], d["price"], dry_run=dry_run,
                                            page_timeout=4, modal_timeout=8, click_delay=0.06)
                    logging.info("Buy attempt result: %s", success)
                    # if you want to stop after a successful buy (real), uncomment:
                    # if success and not dry_run:
                    #     logging.info("Bought an item; stopping monitoring.")
                    #     return
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logging.info("User requested stop (KeyboardInterrupt).")
    finally:
        driver.quit()
        logging.info("Driver quit; exiting.")

# --- CLI & run ---------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fast Rolimon's -> Roblox buy bot")
    parser.add_argument("--real", action="store_true", help="Perform real buys (default is dry-run).")
    parser.add_argument("--poll", type=float, default=6.0, help="Polling interval in seconds (default 6).")
    parser.add_argument("--headless", dest="headless", action="store_true", help="Run headless (default).")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Run with visible browser window.")
    parser.add_argument("--disable-images", dest="disable_images", action="store_true", help="Disable images/media (default).")
    parser.add_argument("--enable-images", dest="disable_images", action="store_false", help="Enable images/media.")
    parser.set_defaults(headless=True, disable_images=True)

    args = parser.parse_args()
    print(f"Max price: {maxPrice}  Minimum: {minDealPercentage}%")
    try:
        main_loop(headless=args.headless, disable_images=args.disable_images, poll_interval=args.poll, dry_run=not args.real)
    except Exception as e:
        logging.exception("Fatal error in main: %s", e)
        raise
