import time
import os
import pyotp
from urllib.parse import parse_qs, urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import config
from managers import config_manager

def perform_auto_login(kite_instance):
    # Fetch dynamic credentials from DB/Settings
    auth = config_manager.get_auth_config()
    
    user_id = auth.get("ZERODHA_USER_ID")
    password = auth.get("ZERODHA_PASSWORD")
    totp_secret = auth.get("TOTP_SECRET")

    if not user_id or not totp_secret:
        return None, "Dynamic Credentials Missing"
        
def perform_auto_login(kite_instance):
    print("🔄 Starting Auto-Login Sequence...")
    
    # --- CONFIGURE CHROME OPTIONS FOR STABILITY ---
    chrome_options = Options()
    # Use 'new' headless mode for better compatibility with modern websites
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--ignore-certificate-errors")
    
    # ANTI-BOT DETECTION FLAGS
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    driver = None
    try:
        # Install/Update Driver automatically
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Mask WebDriver property to avoid bot detection scripts
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })
        
        login_url = kite_instance.login_url()
        print(f"➡️ Navigating to Login URL...")
        driver.get(login_url)
        wait = WebDriverWait(driver, 30) # Increased wait time for slow network

        # --- STEP 1: USER ID ---
        print("➡️ Step 1: Entering User ID...")
        try:
            # Wait for User ID input to be interactive
            user_id_field = wait.until(EC.element_to_be_clickable((By.ID, "userid")))
            user_id_field.clear()
            user_id_field.send_keys(config.ZERODHA_USER_ID)
            user_id_field.send_keys(Keys.ENTER)
            time.sleep(1.5) # Allow DOM transition
        except Exception as e:
            return None, f"Failed at User ID Step: {str(e)}"

        # --- STEP 2: PASSWORD ---
        print("➡️ Step 2: Entering Password...")
        try:
            # Wait for Password input
            password_field = wait.until(EC.element_to_be_clickable((By.ID, "password")))
            password_field.clear()
            password_field.send_keys(config.ZERODHA_PASSWORD)
            password_field.send_keys(Keys.ENTER)
            time.sleep(2) # Allow network request
        except Exception as e:
            return None, f"Failed at Password Step: {str(e)}"

        # --- STEP 3: TOTP ---
        print("➡️ Step 3: Handling TOTP...")
        try:
            # Wait for EITHER the TOTP input OR an error message
            try:
                # Check for explicit error message first (e.g., 'Invalid password')
                error_msg = driver.find_elements(By.CSS_SELECTOR, ".su-message.error, .error-message")
                if error_msg and error_msg[0].is_displayed():
                    return None, f"Login Error: {error_msg[0].text}"
            except: pass

            # Look for the TOTP input field (Type can vary, usually 'text' or 'number' inside the 2FA form)
            totp_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='text'], input[type='number'], input[placeholder='TOTP']")))
            
            if not config.TOTP_SECRET:
                return None, "TOTP_SECRET is missing in config."
                
            totp_now = pyotp.TOTP(config.TOTP_SECRET).now()
            print(f"   🔑 Generated TOTP: {totp_now}")
            
            # Click first to ensure focus, then type
            totp_input.click()
            totp_input.clear()
            totp_input.send_keys(totp_now)
            totp_input.send_keys(Keys.ENTER)
            time.sleep(2)
            
        except Exception as e:
            if "App Code" in driver.page_source:
                return None, "Error: Zerodha is asking for Mobile App Code, but System is configured for TOTP."
            return None, f"Failed at TOTP Step: {str(e)}"

        # --- STEP 4: VERIFY SUCCESS ---
        print("⏳ Waiting for Redirect/Dashboard...")
        
        start_time = time.time()
        while time.time() - start_time < 20: # Wait up to 20 seconds
            current_url = driver.current_url
            
            # Check 1: Request Token in URL (Successful Redirect)
            if "request_token=" in current_url:
                parsed = urlparse(current_url)
                request_token = parse_qs(parsed.query).get('request_token', [None])[0]
                if request_token:
                    print(f"✅ Success! Token Captured: {request_token[:6]}...")
                    return request_token, None
            
            # Removed Check 2 (Dashboard Skip) to force token capture
                
            # Check 3: Error on screen
            # FIX: Use driver.page_source instead of just page_source
            if "Incorrect password" in driver.page_source or "Invalid TOTP" in driver.page_source:
                return None, "Login Failed: Invalid Credentials detected."

            time.sleep(0.1)

        return None, "Login Timed Out. Could not detect Success."

    except Exception as e:
        print(f"❌ Critical Selenium Error: {e}")
        return None, str(e)
        
    finally:
        if driver:
            try:
                driver.quit()
            except: pass
