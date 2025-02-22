
# Hoschel Product Price Tracking Telegram Bot

A robust Telegram bot implementation for tracking product prices on Trendyol (Turkish e-commerce platform), built with Python using the `python-telegram-bot` library.

## Core Features

-   Real-time price tracking for Trendyol products
-   Size-specific product monitoring
-   Rate limiting implementation (100 requests per minute)
-   Interactive button interface
-   Price history tracking and notifications
-   User-specific product lists
-   Automated price change notifications

## Technical Implementation

### Key Components

-   **Rate Limiting Decorator**: Implements request throttling using a decorator pattern
-   **Conversation Handler**: Multi-step dialog for product tracking setup
-   **Signal Handling**: Graceful shutdown mechanism
-   **Error Handling**: Comprehensive error catching and logging
-   **Database Integration**: Persistent storage for user preferences and product data

### Main Commands

-   `/start` - Initialize bot and display main menu
-   `/help` - Show usage instructions
-   `/list` - Display tracked products
-   `/status` - Show bot status and tracking statistics

### Interactive Features

-   **Custom Keyboard Markup**:
    -   Product Tracking
    -   Tracking List
    -   Help
    -   Status
-   **Inline Keyboards**: Used for product management (e.g., stopping tracking)

### Product Tracking Flow

1.  User initiates tracking
2.  URL validation
3.  Size selection (if applicable)
4.  Price monitoring setup
5.  Automated notifications for price changes

## Error Handling & Logging

-   Comprehensive logging system using Python's `logging` module
-   Validation for:
    -   URL format
    -   Trendyol-specific URLs
    -   Product availability
    -   Size availability

## Environment Configuration

-   Uses `.env` for configuration
-   Requires `TELEGRAM_BOT_TOKEN` environment variable

## Security Features

-   Rate limiting per user
-   Input validation
-   Error handling for invalid tokens
-   Graceful shutdown handling

## Dependencies

-   python-telegram-bot
-   python-dotenv
-   logging
-   datetime
-   functools

This bot provides a complete solution for automated price tracking with user-friendly interfaces and robust error handling, suitable for production deployment.
