import os
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ConversationHandler, ContextTypes, CallbackQueryHandler
)
from dotenv import load_dotenv
from product_tracker import ProductTracker
import logging
from datetime import datetime, timedelta
from functools import wraps
import telegram
import signal
import sys

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Conversation states
URL, SIZE = range(2)

# Rate limiting settings
RATE_LIMIT = 100  # requests per minute
RATE_WINDOW = 60  # seconds

def rate_limit(func):
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        # Check rate limit
        request_count = self.db.get_user_request_count(user_id, RATE_WINDOW)
        if request_count >= RATE_LIMIT:
            await update.message.reply_text(
                "⚠️ Çok fazla istek gönderdiniz. Lütfen 1 dakika bekleyin."
            )
            return
        
        # Update request count
        try:
            return await func(self, update, context, *args, **kwargs)
        finally:
            self.db.update_user_stats(user_id)

    return wrapper

class ProductBot:
    def __init__(self):
        self.product_tracker = ProductTracker(notification_callback=self.send_notification)
        self.application = None
        self.db = self.product_tracker.db
        self.is_running = False

    async def send_notification(self, user_id: int, message: str):
        if self.application:
            try:
                keyboard = [
                    ['🛍 Ürün Takibi Başlat', '📋 Takip Listesi'],
                    ['ℹ️ Yardım', '📊 Durum']
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                
                await self.application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Error sending notification to user {user_id}: {e}")

    @rate_limit
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            ['🛍 Ürün Takibi Başlat', '📋 Takip Listesi'],
            ['ℹ️ Yardım', '📊 Durum']
        ]
        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            is_persistent=True,
            one_time_keyboard=False
        )
        
        welcome_message = (
            "HoschelAI ile Ürün Takip Sistemine Hoş Geldiniz! 👋\n\n"
            "🛍️ Trendyol ürünlerinin fiyatlarını takip edebilir,\n"
            "📉 Fiyat düşüşlerinden anında haberdar olabilirsiniz.\n\n"
            "Aşağıdaki butonları kullanarak işlem yapabilirsiniz."
        )
        await update.message.reply_text(welcome_message, reply_markup=reply_markup)

    @rate_limit
    async def handle_buttons(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        
        if text == '🛍 Ürün Takibi Başlat':
            return await self.track(update, context)
        elif text == '📋 Takip Listesi':
            return await self.list_products(update, context)
        elif text == 'ℹ️ Yardım':
            return await self.help(update, context)
        elif text == '📊 Durum':
            return await self.status(update, context)

    @rate_limit
    async def track(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🔗 Lütfen Trendyol ürün linkini yapıştırın:"
        )
        return URL

    async def url_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info("Received URL input")
        url = update.message.text
        if not url.startswith(('http://', 'https://')):
            await update.message.reply_text(
                "❌ Geçersiz URL. Lütfen geçerli bir Trendyol ürün linki girin."
            )
            return URL

        # Check if URL is valid Trendyol URL
        if not self.product_tracker._is_valid_trendyol_url(url):
            logger.info(f"Invalid Trendyol URL: {url}")
            await update.message.reply_text(
                "❌ Geçersiz Trendyol URL'i. Lütfen doğru bir ürün linki girin.\n"
                "Örnek: https://www.trendyol.com/marka/urun-p-1234567"
            )
            return URL

        # Try to get product details first
        product_details = self.product_tracker.get_product_details(url)
        if not product_details:
            logger.error(f"Failed to get product details for URL: {url}")
            await update.message.reply_text(
                "❌ Ürün bilgileri alınamadı. Lütfen geçerli bir Trendyol ürün linki girdiğinizden emin olun."
            )
            return URL

        context.user_data['url'] = url
        context.user_data['product_details'] = product_details
        
        try:
            sizes = self.product_tracker.get_available_sizes(url)
            logger.info(f"Found sizes for {url}: {sizes}")

            if sizes:
                size_keyboard = [[size] for size in sizes]
                size_keyboard.append(['Tüm Bedenler'])
                reply_markup = ReplyKeyboardMarkup(
                    size_keyboard,
                    one_time_keyboard=True,
                    resize_keyboard=True
                )
                await update.message.reply_text(
                    f"📦 Ürün: {product_details['name']}\n"
                    f"💰 Fiyat: {product_details['price']:.2f} TL\n\n"
                    "📏 Lütfen takip etmek istediğiniz bedeni seçin:",
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    f"📦 Ürün: {product_details['name']}\n"
                    f"💰 Fiyat: {product_details['price']:.2f} TL\n\n"
                    "❗ Bu ürün için beden seçeneği bulunamadı.\n"
                    "Devam etmek için 'Tüm Bedenler' yazın:"
                )
            return SIZE

        except Exception as e:
            logger.error(f"Error in url_input: {e}")
            await update.message.reply_text(
                "❌ Bir hata oluştu. Lütfen tekrar deneyin."
            )
            return URL

    async def size_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info("Size input received")  # Debug log
        try:
            url = context.user_data['url']
            size = update.message.text
            logger.info(f"Processing size: {size} for URL: {url}")  # Debug log
            
            try:
                product_details = self.product_tracker.add_tracking(
                    update.effective_user.id,
                    url,
                    size
                )
                
                logger.info(f"Product tracking added: {product_details}")  # Debug log
                
                # Return to main menu
                keyboard = [
                    ['🛍 Ürün Takibi Başlat', '📋 Takip Listesi'],
                    ['ℹ️ Yardım', '📊 Durum']
                ]
                reply_markup = ReplyKeyboardMarkup(
                    keyboard,
                    resize_keyboard=True,
                    is_persistent=True,
                    one_time_keyboard=False
                )
                
                await update.message.reply_text(
                    f"✅ Takip başlatıldı!\n\n"
                    f"📦 Ürün: {product_details['name']}\n"
                    f"📏 Beden: {size}\n"
                    f"💰 Mevcut fiyat: {product_details['price']:.2f}TL\n\n"
                    "🔔 Fiyat düştüğünde size haber vereceğim!",
                    reply_markup=reply_markup
                )
                
            except ValueError as ve:
                logger.error(f"ValueError in size_input: {ve}")  # Debug log
                await update.message.reply_text(f"❌ Hata: {str(ve)}")
                return SIZE
                
        except Exception as e:
            logger.error(f"Error in size_input: {e}")  # Debug log
            await update.message.reply_text(
                "❌ Bir hata oluştu. Lütfen tekrar deneyin."
            )
            logger.error(f"Error in size input: {e}")
            return SIZE
            
        return ConversationHandler.END

    @rate_limit
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📌 Nasıl Kullanılır?\n\n"
            "1. '🛍 Ürün Takibi Başlat' butonuna tıklayın\n"
            "2. Trendyol ürün linkini yapıştırın\n"
            "3. İstediğiniz bedeni seçin\n"
            "🔍 Diğer Komutlar:\n"
            "📋 Takip Listesi - Takip ettiğiniz ürünleri görün\n"
            "📊 Durum - Bot durumunu kontrol edin"
        )
        keyboard = [
            ['🛍 Ürün Takibi Başlat', '📋 Takip Listesi'],
            ['ℹ️ Yardım', '📊 Durum']
        ]
        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            is_persistent=True,
            one_time_keyboard=False
        )
        await update.message.reply_text(help_text, reply_markup=reply_markup)

    @rate_limit
    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        products = self.db.get_user_products(user_id)
        
        if not products:
            keyboard = [
                ['🛍 Ürün Takibi Başlat', '📋 Takip Listesi'],
                ['ℹ️ Yardım', '📊 Durum']
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                "📝 Henüz takip ettiğiniz bir ürün bulunmuyor.\n"
                "Yeni bir ürün takibi başlatmak için '🛍 Ürün Takibi Başlat' butonunu kullanın.",
                reply_markup=reply_markup
            )
            return

        for product in products:
            keyboard = [[
                InlineKeyboardButton(
                    "🗑 Takibi Durdur",
                    callback_data=f"delete_{product['id']}"
                )
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = (
                f"📦 Ürün: {product['product_name']}\n"
                f"📏 Beden: {product['size']}\n"
                f"💳 Güncel fiyat: {product['last_price']:.2f}TL\n"
                f"🕒 Son kontrol: {product['last_check']}\n"
                f"🔗 Link: {product['url']}"
            )
            
            await update.message.reply_text(message, reply_markup=reply_markup)

    async def delete_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        product_id = int(query.data.split('_')[1])
        if self.db.delete_product(query.from_user.id, product_id):
            await query.message.edit_text(
                f"{query.message.text}\n\n❌ Takip durduruldu."
            )
        else:
            await query.message.reply_text("❌ Ürün takibi durdurulamadı.")

    @rate_limit
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        products = self.db.get_user_products(user_id)
        
        status_message = (
            "📊 Bot Durumu\n\n"
            f"👤 Takip ettiğiniz ürün sayısı: {len(products)}\n"
            f"⚡ Bot durumu: Aktif\n"
            f"🕒 Kontrol sıklığı: 15 dakika"
        )
        
        await update.message.reply_text(status_message)

    @rate_limit
    async def set_threshold(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            product_id = int(context.args[0])
            threshold_price = float(context.args[1])
            
            self.product_tracker.add_price_threshold(
                update.effective_user.id,
                product_id,
                threshold_price
            )
            
            await update.message.reply_text(
                f"✅ Fiyat alarmı {threshold_price:.2f}TL olarak ayarlandı."
            )
        except (ValueError, IndexError):
            await update.message.reply_text(
                "❌ Hatalı komut. Örnek kullanım: /threshold product_id price"
            )

    @rate_limit
    async def show_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            product_id = int(context.args[0])
            chart_data = self.product_tracker.get_price_history_chart(product_id)
            
            if chart_data:
                await update.message.reply_photo(
                    photo=chart_data,
                    caption="📊 Ürün fiyat geçmişi"
                )
            else:
                await update.message.reply_text(
                    "❌ Fiyat geçmişi bulunamadı."
                )
        except (ValueError, IndexError):
            await update.message.reply_text(
                "❌ Hatalı komut. Örnek kullanım: /history product_id"
            )

    @rate_limit
    async def compare_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "❌ Lütfen ürün adı girin. Örnek: /compare ürün adı"
            )
            return

        product_name = ' '.join(context.args)
        results = self.product_tracker.compare_prices(product_name)
        
        if not results:
            await update.message.reply_text(
                "❌ Ürün fiyat karşılaştırması bulunamadı."
            )
            return

        message = "💰 Fiyat Karşılaştırması:\n\n"
        for result in results:
            stock_status = "✅ Stokta" if result['in_stock'] else "❌ Stokta Değil"
            message += (
                f"🏪 {result['store_name']}\n"
                f"💵 {result['price']:.2f}TL\n"
                f"📦 {stock_status}\n"
                f"🔗 {result['url']}\n\n"
            )

        await update.message.reply_text(message)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info("Shutdown signal received. Cleaning up...")
        self.is_running = False
        if self.application:
            self.application.stop()
        self.product_tracker.cleanup()
        sys.exit(0)

    def run(self):
        try:
            # Set up signal handlers
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)

            self.is_running = True
            self.application = Application.builder().token(TOKEN).build()

            # Add handlers in correct order
            self.application.add_handler(CommandHandler("start", self.start))
            self.application.add_handler(CommandHandler("help", self.help))
            self.application.add_handler(CommandHandler("list", self.list_products))
            self.application.add_handler(CommandHandler("status", self.status))
            
            # Add button handler before conversation handler
            self.application.add_handler(MessageHandler(
                filters.Regex('^(🛍 Ürün Takibi Başlat|📋 Takip Listesi|ℹ️ Yardım|📊 Durum)$'),
                self.handle_buttons
            ))

            # Add conversation handler last
            conv_handler = ConversationHandler(
                entry_points=[
                    MessageHandler(filters.Regex('^🛍 Ürün Takibi Başlat$'), self.track),
                    CommandHandler('track', self.track)
                ],
                states={
                    URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.url_input)],
                    SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.size_input)],
                },
                fallbacks=[
                    CommandHandler('cancel', self.start),
                    MessageHandler(filters.Regex('^(📋 Takip Listesi|ℹ️ Yardım|📊 Durum)$'), self.handle_buttons)
                ]
            )
            self.application.add_handler(conv_handler)
            
            # Add callback query handler
            self.application.add_handler(CallbackQueryHandler(self.delete_callback, pattern=r'^delete_'))

            logger.info("Starting bot...")
            self.application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                stop_signals=[],
                close_loop=False
            )
            
        except telegram.error.InvalidToken:
            logger.error("Invalid bot token! Please check your .env file and update the token from BotFather.")
            raise SystemExit(1)
        except Exception as e:
            logger.error(f"Failed to start bot: {str(e)}")
            raise SystemExit(1)
        finally:
            if self.is_running:
                self.product_tracker.cleanup()
                logger.info("Bot stopped.")

    def __del__(self):
        """Ensure cleanup on object destruction"""
        if hasattr(self, 'product_tracker'):
            self.product_tracker.cleanup()

if __name__ == '__main__':
    bot = ProductBot()
    bot.run() 