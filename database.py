import sqlite3
from datetime import datetime
from typing import List, Dict, Optional
import json
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = "product_tracker.db"):
        self.db_path = db_path
        self.initialize_db()

    def initialize_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracked_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    url TEXT,
                    size TEXT,
                    last_price REAL,
                    product_name TEXT,
                    last_check TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    price REAL,
                    checked_at TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES tracked_products (id)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id INTEGER PRIMARY KEY,
                    request_count INTEGER DEFAULT 0,
                    last_request TIMESTAMP,
                    preferences TEXT
                )
            """)

            # Add price threshold table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_thresholds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    user_id INTEGER,
                    threshold_price REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES tracked_products (id)
                )
            """)
            
            # Add supported stores table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS supported_stores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    base_url TEXT,
                    selectors JSON,
                    enabled BOOLEAN DEFAULT TRUE
                )
            """)
            
            # Add store product links table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS store_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    store_id INTEGER,
                    store_url TEXT,
                    current_price REAL,
                    in_stock BOOLEAN,
                    last_check TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES tracked_products (id),
                    FOREIGN KEY (store_id) REFERENCES supported_stores (id)
                )
            """)

            # Initialize supported stores
            stores = [
                ('Trendyol', 'trendyol.com', '{"price": ".prc-dsc", "size": "div.sp-itm"}'),
                ('Bershka', 'bershka.com', '{"price": ".current-price-elem", "size": ".size-selector-option"}'),
                ('Zara', 'zara.com', '{"price": ".price-elem", "size": ".size-pill"}')
            ]
            
            conn.executemany("""
                INSERT OR IGNORE INTO supported_stores (name, base_url, selectors)
                VALUES (?, ?, ?)
            """, stores)

    def add_tracked_product(self, user_id: int, product_data: Dict) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tracked_products 
                (user_id, url, size, last_price, product_name, last_check)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                product_data['url'],
                product_data['size'],
                product_data['last_price'],
                product_data['product_name'],
                datetime.now()
            ))
            return cursor.lastrowid

    def get_user_products(self, user_id: int) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tracked_products 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            """, (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_product(self, user_id: int, product_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM tracked_products 
                WHERE id = ? AND user_id = ?
            """, (product_id, user_id))
            return cursor.rowcount > 0

    def update_product_price(self, product_id: int, new_price: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE tracked_products 
                SET last_price = ?, last_check = ? 
                WHERE id = ?
            """, (new_price, datetime.now(), product_id))
            
            conn.execute("""
                INSERT INTO price_history (product_id, price, checked_at)
                VALUES (?, ?, ?)
            """, (product_id, new_price, datetime.now()))

    def get_all_tracked_products(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tracked_products")
            return [dict(row) for row in cursor.fetchall()]

    def update_user_stats(self, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # First try to update existing record
            cursor.execute("""
                UPDATE user_stats 
                SET request_count = CASE 
                    WHEN last_request < datetime('now', '-1 minute') THEN 1 
                    ELSE request_count + 1 
                END,
                last_request = datetime('now')
                WHERE user_id = ?
            """, (user_id,))
            
            # If no record exists, create new one
            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO user_stats (user_id, request_count, last_request)
                    VALUES (?, 1, datetime('now'))
                """, (user_id,))

    def get_user_request_count(self, user_id: int, timeframe_minutes: int = 60) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT request_count FROM user_stats 
                WHERE user_id = ? AND 
                last_request > datetime('now', ?)
            """, (user_id, f'-{timeframe_minutes} minutes'))
            result = cursor.fetchone()
            return result[0] if result else 0 