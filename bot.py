import os
import re
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import yt_dlp
from flask import Flask, request
import gc
import tempfile
import shutil
import time
import signal

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

# کلاس برای مدیریت تایمر دانلود
class DownloadTimeout(Exception):
    pass

def timeout_handler(signum, frame):
    raise DownloadTimeout("زمان دانلود به پایان رسید")

# تابع برای تشخیص لینک در متن
def contains_url(text):
    url_pattern = re.compile(
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    )
    return bool(url_pattern.search(text))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # بررسی اینکه پیام در گروه مورد نظر است
    if update.message.chat_id != GROUP_ID:
        return

    # بررسی وجود لینک در پیام
    message_text = update.message.text
    if not message_text or not contains_url(message_text):
        return

    # ایجاد یک دایرکتوری موقت
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, 'video.mp4')

    try:
        # تنظیم تایمر برای دانلود (3 دقیقه)
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(180)  # 3 دقیقه

        # تنظیمات yt-dlp برای کمترین کیفیت
        ydl_opts = {
            'format': 'worst',  # کمترین کیفیت موجود
            'outtmpl': temp_path,
            'quiet': False,  # نمایش لاگ‌ها برای عیب‌یابی
            'no_warnings': False,  # نمایش هشدارها
            'verbose': True,  # نمایش جزئیات بیشتر
            'max_filesize': 100 * 1024 * 1024,  # 100MB به بایت
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'merge_output_format': 'mp4',
            'retries': 3,  # تعداد تلاش‌های مجدد
            'socket_timeout': 30,  # زمان انتظار برای اتصال
            'progress_hooks': [lambda d: logging.info(f"پیشرفت دانلود: {d.get('_percent_str', '0%')}")],
            'extract_flat': True,  # استخراج تمام ویدیوهای موجود در صفحه
        }

        # دانلود ویدیو
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # ابتدا اطلاعات ویدیو را بدون دانلود دریافت می‌کنیم
                info = ydl.extract_info(message_text, download=False)
                logging.info(f"اطلاعات فایل: {info}")
                
                # بررسی مدت زمان ویدیو
                duration = info.get('duration', 0)
                if duration < 120:  # کمتر از 2 دقیقه (120 ثانیه)
                    await context.bot.send_message(
                        chat_id=GROUP_ID,
                        text=f"ویدیو کوتاه‌تر از 2 دقیقه است (مدت زمان: {duration} ثانیه). دانلود نمی‌شود."
                    )
                    return
                
                # دانلود ویدیو
                info = ydl.extract_info(message_text, download=True)
                
                # اگر فایل با پسوند دیگری ذخیره شده، آن را به mp4 تغییر نام می‌دهیم
                downloaded_file = ydl.prepare_filename(info)
                logging.info(f"مسیر فایل دانلود شده: {downloaded_file}")
                
                if downloaded_file != temp_path and os.path.exists(downloaded_file):
                    shutil.move(downloaded_file, temp_path)
                    logging.info(f"فایل به {temp_path} منتقل شد")

                # بررسی سایز فایل
                if os.path.exists(temp_path):
                    file_size = os.path.getsize(temp_path)
                    logging.info(f"سایز فایل: {file_size} bytes")
                    if file_size == 0:
                        raise Exception("فایل دانلود شده خالی است")
                    elif file_size > 100 * 1024 * 1024:
                        raise Exception(f"سایز فایل ({file_size/1024/1024:.2f}MB) بیشتر از حد مجاز (100MB) است")

            except Exception as e:
                logging.error(f"خطا در دانلود: {str(e)}")
                raise e

        # غیرفعال کردن تایمر
        signal.alarm(0)

        # ارسال ویدیو به گروه
        with open(temp_path, 'rb') as video:
            await context.bot.send_video(
                chat_id=GROUP_ID,
                video=video,
                caption=f"ویدیو از {message_text} (مدت زمان: {duration} ثانیه)",
                supports_streaming=True
            )

    except DownloadTimeout:
        logging.error("زمان دانلود به پایان رسید")
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text="زمان دانلود به پایان رسید. لطفاً دوباره تلاش کنید."
        )
    except Exception as e:
        logging.error(f"خطا در پردازش ویدیو: {str(e)}")
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"خطا در دانلود ویدیو: {str(e)}"
        )
    finally:
        # غیرفعال کردن تایمر در هر صورت
        signal.alarm(0)
        
        # پاک کردن دایرکتوری موقت و محتویات آن
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        
        # پاکسازی حافظه
        gc.collect()

def main():
    # ایجاد برنامه
    application = Application.builder().token(TOKEN).build()

    # اضافه کردن هندلر پیام
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # تنظیم webhook
    port = int(os.environ.get('PORT', 8080))
    webhook_url = os.environ.get('WEBHOOK_URL')
    
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
