from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import json
import logging

logger = logging.getLogger(__name__)

class StoreScraper(ABC):
    def __init__(self, driver, selectors: Dict):
        self.driver = driver
        self.selectors = json.loads(selectors)

    @abstractmethod
    def get_price(self) -> float:
        pass

    @abstractmethod
    def get_sizes(self) -> List[str]:
        pass

    @abstractmethod
    def is_in_stock(self) -> bool:
        pass

class TrendyolScraper(StoreScraper):
    def get_price(self) -> float:
        try:
            price_elem = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "prc-dsc"))
            )
            price_text = price_elem.text.strip()
            return float(price_text.replace('TL', '').replace('.', '').replace(',', '.'))
        except Exception as e:
            logger.error(f"Error getting Trendyol price: {e}")
            return 0.0

    def get_sizes(self) -> List[str]:
        try:
            size_elements = self.driver.find_elements(By.CSS_SELECTOR, "div.sp-itm:not(.so)")
            return [size.text.strip() for size in size_elements]
        except Exception as e:
            logger.error(f"Error getting Trendyol sizes: {e}")
            return []

    def is_in_stock(self) -> bool:
        return len(self.get_sizes()) > 0

class BershkaScraper(StoreScraper):
    def get_price(self) -> float:
        try:
            price_elem = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "current-price-elem"))
            )
            price_text = price_elem.text.strip()
            return float(price_text.replace('TL', '').replace('.', '').replace(',', '.'))
        except Exception as e:
            logger.error(f"Error getting Bershka price: {e}")
            return 0.0

    def get_sizes(self) -> List[str]:
        try:
            size_elements = self.driver.find_elements(By.CSS_SELECTOR, ".size-selector-option:not(.disabled)")
            return [size.text.strip() for size in size_elements]
        except Exception as e:
            logger.error(f"Error getting Bershka sizes: {e}")
            return []

    def is_in_stock(self) -> bool:
        return len(self.get_sizes()) > 0

class ZaraScraper(StoreScraper):
    # Similar implementation for Zara
    pass

class ScraperFactory:
    @staticmethod
    def get_scraper(store_name: str, driver, selectors: str) -> Optional[StoreScraper]:
        scrapers = {
            'Trendyol': TrendyolScraper,
            'Bershka': BershkaScraper,
            'Zara': ZaraScraper
        }
        scraper_class = scrapers.get(store_name)
        return scraper_class(driver, selectors) if scraper_class else None 