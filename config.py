import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
    MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
