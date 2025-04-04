import os
import re
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import yt_dlp
from flask import Flask, request
import gc
import tempfile

# تنظیمات لاگینگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# توکن ربات
TOKEN = "7274292176:AAEoX0csJq2neu1Hl0aeuYFXDW_kork2b5w"
# آیدی گروه
GROUP_ID = -1002260229635

# ایجاد برنامه Flask
app = Flask(__name__)

# لیست دامنه‌های مجاز
ALLOWED_DOMAINS = [
    'pornhub.com',
    'xvideos.com',
    'sexbebin.com',
    'xhamster.com'
]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # بررسی اینکه پیام در گروه مورد نظر است
    if update.message.chat_id != GROUP_ID:
        return

    # بررسی وجود لینک در پیام
    message_text = update.message.text
    if not message_text:
        return

    # بررسی دامنه‌های مجاز
    is_allowed = False
    for domain in ALLOWED_DOMAINS:
        if domain in message_text.lower():
            is_allowed = True
            break

    if not is_allowed:
        return

    # ایجاد یک فایل موقت با پسوند mp4
    temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
    temp_path = temp_file.name
    temp_file.close()

    try:
        # تنظیمات yt-dlp برای کمترین کیفیت
        ydl_opts = {
            'format': 'worst',  # کمترین کیفیت
            'outtmpl': temp_path,
            'quiet': True,
            'no_warnings': True,
            'max_filesize': 50 * 1024 * 1024,  # 50MB به بایت
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'format_sort': ['res:144', 'ext:mp4:m4a', 'size'],
            'format_sort_force': True,
        }

        # دانلود ویدیو
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([message_text])

        # ارسال ویدیو به گروه
        with open(temp_path, 'rb') as video:
            await context.bot.send_video(
                chat_id=GROUP_ID,
                video=video,
                caption=f"ویدیو از {message_text}",
                supports_streaming=True
            )

    except Exception as e:
        logging.error(f"خطا در پردازش ویدیو: {str(e)}")
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"خطا در دانلود ویدیو: {str(e)}"
        )
    finally:
        # پاک کردن فایل موقت
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        # پاکسازی حافظه
        gc.collect()

def main():
    # ایجاد برنامه
    application = Application.builder().token(TOKEN).build()

    # اضافه کردن هندلر پیام
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # تنظیم webhook
    port = int(os.environ.get('PORT', 8080))
    webhook_url = os.environ.get('https://mostahjan.onrender.com')
    
    if webhook_url:
        application.run_webhook(
            listen='0.0.0.0',
            port=port,
            url_path=TOKEN,
            webhook_url=f"{webhook_url}/{TOKEN}"
        )
    else:
        # اگر webhook_url تنظیم نشده باشد، از polling استفاده می‌کند
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 
