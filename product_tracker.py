import time
import threading
from typing import Dict, List, Callable, Optional
import logging
from msedge.selenium_tools import Edge, EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from datetime import datetime, timedelta
import re
from retry import retry
from cachetools import TTLCache
from queue import Queue
from database import Database
from prometheus_client import Counter, Gauge, start_http_server
from store_scrapers import ScraperFactory
import matplotlib.pyplot as plt
import io
import os
import winreg
import requests
import psutil

# Metrics
SCRAPE_COUNTER = Counter('product_scrapes_total', 'Total number of product scrapes')
SCRAPE_ERROR_COUNTER = Counter('scrape_errors_total', 'Total number of scraping errors')
ACTIVE_DRIVERS = Gauge('chrome_drivers_active', 'Number of active Chrome drivers')
PRICE_DROPS = Counter('price_drops_total', 'Total number of price drops detected')

logger = logging.getLogger(__name__)

class DriverPool:
    def __init__(self, max_drivers: int = 3):
        self.max_drivers = max_drivers
        self.drivers: Queue[Edge] = Queue()
        self.active_drivers = 0

    def get_driver(self) -> Edge:
        try:
            if not self.drivers.empty():
                return self.drivers.get()
            
            if self.active_drivers < self.max_drivers:
                driver = self._create_driver()
                self.active_drivers += 1
                return driver
            
            return self.drivers.get()
        except Exception as e:
            logger.error(f"Error getting driver: {e}")
            raise

    def return_driver(self, driver: Edge):
        self.drivers.put(driver)

    def _create_driver(self) -> Edge:
        try:
            options = EdgeOptions()
            options.use_chromium = True
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-extensions')
            options.add_argument('--disable-infobars')
            
            # Add error handling for driver creation
            try:
                driver = Edge(options=options)
                logger.info("Successfully created new Edge driver")
                return driver
            except Exception as e:
                logger.error(f"Failed to create Edge driver: {e}")
                raise
                
        except Exception as e:
            logger.error(f"Error in _create_driver: {e}")
            raise

    def cleanup(self):
        while not self.drivers.empty():
            driver = self.drivers.get()
            driver.quit()
            self.active_drivers -= 1
            ACTIVE_DRIVERS.dec()

class ProductTracker:
    def __init__(self, notification_callback: Callable = None, db_path: str = "product_tracker.db"):
        print("\n🚀 Initializing Product Tracker...")
        
        if not self.check_system_requirements():
            raise SystemExit("System requirements not met. Please fix the issues above.")
        
        self.notification_callback = notification_callback
        self.db = Database(db_path)
        
        # Test driver setup
        try:
            self.driver_pool = DriverPool()
            if not self.test_driver():
                logger.error("Edge WebDriver test failed")
                raise Exception("Failed to initialize Edge WebDriver")
            logger.info("Edge WebDriver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize driver pool: {e}")
            raise

        self.cache = TTLCache(maxsize=100, ttl=300)
        self.is_running = True
        self.thread = threading.Thread(target=self._tracking_loop, daemon=True)
        
        # Start metrics server
        try:
            start_http_server(8000)
        except OSError as e:
            logger.warning(f"Metrics server already running or port in use: {e}")
        
        self.thread.start()

    @retry(WebDriverException, tries=3, delay=2, backoff=2)
    def get_available_sizes(self, url: str) -> List[str]:
        driver = None
        try:
            driver = self.driver_pool.get_driver()
            driver.get(url)
            try:
                # Wait for size options to load
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.sp-itm, div.size-variant-wrapper"))
                )
            except TimeoutException:
                # If no size options found, return empty list
                return []
            
            # Handle dynamic loading
            self._scroll_page(driver)
            
            # Try different size selectors
            size_elements = []
            selectors = [
                "div.sp-itm:not(.so)",  # Regular size selector
                "div.size-variant-wrapper:not(.disabled)",  # Alternative size selector
                "div.variant-wrapper:not(.disabled)"  # Another variant
            ]
            
            for selector in selectors:
                size_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if size_elements:
                    break
            
            sizes = [size.text.strip() for size in size_elements]
            logger.info(f"Found sizes: {sizes}")  # Debug log
            
            SCRAPE_COUNTER.inc()
            return sizes
        except Exception as e:
            SCRAPE_ERROR_COUNTER.inc()
            logger.error(f"Error getting sizes from Trendyol: {e}")
            return []
        finally:
            if driver:
                self.driver_pool.return_driver(driver)

    def _scroll_page(self, driver):
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    @retry(WebDriverException, tries=3, delay=2, backoff=2)
    def get_product_details(self, url: str) -> Optional[Dict]:
        logger.info(f"Getting product details for URL: {url}")
        
        if not url:
            logger.error("Empty URL provided")
            return None

        driver = None
        try:
            driver = self.driver_pool.get_driver()
            if not driver:
                logger.error("Failed to get WebDriver")
                return None

            logger.info("Loading URL in WebDriver")
            driver.get(url)
            
            # Wait for page load
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # Try multiple selectors for product name
            product_name = None
            name_selectors = ["pr-new-br", "product-name", "title"]
            for selector in name_selectors:
                try:
                    element = driver.find_element(By.CLASS_NAME, selector)
                    product_name = element.text.strip()
                    if product_name:
                        break
                except:
                    continue

            if not product_name:
                logger.error("Could not find product name")
                return None

            # Try multiple selectors for price
            price = self._extract_price(driver)
            if price <= 0:
                logger.error("Could not find valid price")
                return None

            details = {
                'name': product_name,
                'price': price,
                'available_sizes': self.get_available_sizes(url),
                'last_checked': datetime.now()
            }
            
            logger.info(f"Successfully got product details: {details}")
            return details

        except Exception as e:
            logger.error(f"Error in get_product_details: {e}")
            return None
        finally:
            if driver:
                try:
                    self.driver_pool.return_driver(driver)
                except:
                    pass

    def _get_element_text(self, driver, by, value) -> str:
        try:
            element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((by, value))
            )
            return element.text.strip()
        except TimeoutException:
            return "N/A"

    def _extract_price(self, driver) -> float:
        try:
            # Try different price selectors
            price_selectors = [
                "prc-dsc",  # Normal price
                "prc-slg",  # Sale price
                "price-box"  # Alternative price class
            ]
            
            for selector in price_selectors:
                try:
                    price_element = driver.find_element(By.CLASS_NAME, selector)
                    price_text = price_element.text.strip()
                    price_text = price_text.replace('TL', '').replace('.', '').replace(',', '.').strip()
                    return float(price_text)
                except:
                    continue
                    
            raise ValueError("No valid price found")
            
        except Exception as e:
            logging.error(f"Error extracting price: {e}")
            return 0.0

    def add_tracking(self, user_id: int, url: str, size: str) -> Dict:
        if not self._is_valid_trendyol_url(url):
            raise ValueError("Geçersiz Trendyol URL'i")
            
        initial_details = self.get_product_details(url)
        if not initial_details:
            raise ValueError("Ürün bilgileri alınamadı")
            
        product_data = {
            'url': url,
            'size': size,
            'last_price': initial_details['price'],
            'product_name': initial_details['name']
        }
        
        self.db.add_tracked_product(user_id, product_data)
        return initial_details

    def _is_valid_trendyol_url(self, url: str) -> bool:
        try:
            pattern = r'trendyol\.com/.*?/.*?-p-[0-9]+'
            return bool(re.search(pattern, url.lower()))
        except:
            logger.error(f"Error validating URL: {url}")
            return False

    def _tracking_loop(self):
        while self.is_running:
            try:
                products = self.db.get_all_tracked_products()
                for product in products:
                    if not self.is_running:
                        break
                    self._check_product(product)
                time.sleep(900)  # 15 dakika
            except Exception as e:
                logger.error(f"Error in tracking loop: {e}")
                if not self.is_running:
                    break
                time.sleep(60)  # Wait before retrying

    def _check_product(self, product: Dict):
        details = self.get_product_details(product['url'])
        if not details:
            return

        current_price = details['price']
        last_price = product['last_price']
        
        # Check price thresholds
        thresholds = self.db.get_product_thresholds(product['id'])
        for threshold in thresholds:
            if current_price <= threshold['threshold_price']:
                self._notify_threshold_reached(
                    threshold['user_id'],
                    product['product_name'],
                    product['url'],
                    threshold['threshold_price'],
                    current_price
                )

        # Check stock status
        if details['is_available'] and not product.get('was_available', True):
            self._notify_stock_available(
                product['user_id'],
                product['product_name'],
                product['url']
            )

        if current_price < last_price:
            if (product['size'].lower() == 'hepsi' or 
                product['size'] in details['available_sizes']):
                
                PRICE_DROPS.inc()
                self._notify_price_drop(
                    product['user_id'],
                    product['product_name'],
                    product['url'],
                    last_price,
                    current_price,
                    details['available_sizes']
                )

        self.db.update_product_price(product['id'], current_price)

    def _notify_price_drop(self, user_id: int, product_name: str, url: str, 
                          old_price: float, new_price: float, available_sizes: List[str]):
        if self.notification_callback:
            message = (
                f"🔔 FİYAT DÜŞTÜ!\n\n"
                f"📦 Ürün: {product_name}\n"
                f"💰 Eski fiyat: {old_price:.2f} TL\n"
                f"🏷 Yeni fiyat: {new_price:.2f} TL\n"
                f"📉 İndirim: {((old_price - new_price) / old_price * 100):.1f}%\n"
                f"📏 Mevcut bedenler: {', '.join(available_sizes)}\n\n"
                f"🛍 Ürün linki: {url}"
            )
            self.notification_callback(user_id, message)

    def add_price_threshold(self, user_id: int, product_id: int, threshold_price: float):
        self.db.add_threshold(user_id, product_id, threshold_price)

    def get_price_history_chart(self, product_id: int) -> Optional[bytes]:
        history = self.db.get_price_history(product_id)
        if not history:
            return None

        dates = [h['checked_at'] for h in history]
        prices = [h['price'] for h in history]

        plt.figure(figsize=(10, 6))
        plt.plot(dates, prices)
        plt.title('Fiyat Geçmişi')
        plt.xlabel('Tarih')
        plt.ylabel('Fiyat (TL)')
        plt.xticks(rotation=45)
        plt.grid(True)

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        return buf.getvalue()

    def compare_prices(self, product_name: str) -> List[Dict]:
        stores = self.db.get_enabled_stores()
        results = []
        
        for store in stores:
            price_info = self.db.get_latest_price(product_name, store['id'])
            if price_info:
                results.append({
                    'store_name': store['name'],
                    'price': price_info['current_price'],
                    'in_stock': price_info['in_stock'],
                    'url': price_info['store_url']
                })
        
        return sorted(results, key=lambda x: x['price'])

    def cleanup(self):
        """Gracefully shutdown the tracker"""
        logger.info("Cleaning up ProductTracker...")
        self.is_running = False
        
        # Wait for tracking loop to finish current iteration
        if self.thread.is_alive():
            self.thread.join(timeout=30)
        
        # Clean up driver pool
        if hasattr(self, 'driver_pool'):
            self.driver_pool.cleanup()
        
        logger.info("ProductTracker cleanup completed.")

    def __del__(self):
        """Ensure cleanup on object destruction"""
        self.cleanup()

    def test_driver(self) -> bool:
        """Test if Edge WebDriver is working correctly"""
        driver = None
        try:
            driver = self.driver_pool.get_driver()
            driver.get("https://www.trendyol.com")
            return True
        except Exception as e:
            logger.error(f"Driver test failed: {e}")
            return False
        finally:
            if driver:
                self.driver_pool.return_driver(driver)

    def check_system_requirements(self):
        """Check all requirements and return detailed status"""
        status = {
            'edge_browser': False,
            'edge_driver': False,
            'driver_version_match': False,
            'permissions': False,
            'network': False,
            'memory': False
        }
        
        print("\n🔍 Checking system requirements...")
        
        # Check Edge browser
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Edge\BLBeacon") as key:
                edge_version = winreg.QueryValueEx(key, "version")[0]
                print("✅ Microsoft Edge found:", edge_version)
                status['edge_browser'] = True
        except Exception as e:
            print("❌ Microsoft Edge not found or version check failed")
            print(f"   Error: {str(e)}")

        # Check EdgeDriver
        try:
            driver = None
            driver = Edge(options=EdgeOptions())
            driver_version = driver.capabilities['browserVersion']
            print(f"✅ EdgeDriver found: {driver_version}")
            status['edge_driver'] = True
            
            # Check version match
            if edge_version.split('.')[0] == driver_version.split('.')[0]:
                print("✅ Edge and EdgeDriver versions match")
                status['driver_version_match'] = True
            else:
                print("❌ Version mismatch:")
                print(f"   Edge: {edge_version}")
                print(f"   Driver: {driver_version}")
        except Exception as e:
            print("❌ EdgeDriver check failed")
            print(f"   Error: {str(e)}")
        finally:
            if driver:
                driver.quit()

        # Check permissions
        try:
            test_file = "test_permissions.txt"
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            print("✅ File system permissions OK")
            status['permissions'] = True
        except Exception as e:
            print("❌ Permission check failed")
            print(f"   Error: {str(e)}")

        # Check network
        try:
            response = requests.get('https://www.trendyol.com', timeout=5)
            if response.status_code == 200:
                print("✅ Network connection to Trendyol OK")
                status['network'] = True
            else:
                print(f"❌ Network check failed: Status code {response.status_code}")
        except Exception as e:
            print("❌ Network check failed")
            print(f"   Error: {str(e)}")

        # Check available memory
        try:
            memory = psutil.virtual_memory()
            available_gb = memory.available / (1024 * 1024 * 1024)
            if available_gb > 1.0:
                print(f"✅ Available memory: {available_gb:.1f}GB")
                status['memory'] = True
            else:
                print(f"❌ Low memory: {available_gb:.1f}GB available")
        except Exception as e:
            print("❌ Memory check failed")
            print(f"   Error: {str(e)}")

        # Overall status
        print("\n📊 System Status Summary:")
        all_ok = all(status.values())
        if all_ok:
            print("✅ All checks passed! System ready.")
        else:
            print("❌ Some checks failed:")
            for check, passed in status.items():
                icon = "✅" if passed else "❌"
                print(f"{icon} {check.replace('_', ' ').title()}")
            
            print("\n🔧 Recommended fixes:")
            if not status['edge_browser']:
                print("• Install Microsoft Edge browser")
            if not status['edge_driver']:
                print("• Download and install EdgeDriver from:")
                print("  https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/")
            if not status['driver_version_match']:
                print("• Update EdgeDriver to match your Edge browser version")
            if not status['permissions']:
                print("• Run the application with appropriate permissions")
            if not status['network']:
                print("• Check your internet connection")
                print("• Verify if Trendyol is accessible")
            if not status['memory']:
                print("• Free up some system memory")
                print("• Close unnecessary applications")

        return all_ok 