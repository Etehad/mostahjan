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
import ffmpeg

# تنظیمات لاگینگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# توکن ربات
TOKEN = "7274292176:AAEoX0csJq2neu1Hl0aeuYFXDW_kork2b5w"
# آیدی گروه
GROUP_ID = -1002260229635
# حداکثر سایز فایل برای هر بخش (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 مگابایت به بایت

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

# تابع برای تقسیم ویدیو به بخش‌های کوچکتر
def split_video(input_path, output_dir, max_size_bytes):
    try:
        # دریافت اطلاعات ویدیو
        probe = ffmpeg.probe(input_path)
        duration = float(probe['format']['duration'])
        file_size = os.path.getsize(input_path)
        
        # محاسبه تعداد بخش‌ها
        num_parts = int(file_size / max_size_bytes) + (1 if file_size % max_size_bytes else 0)
        part_duration = duration / num_parts
        
        output_files = []
        for i in range(num_parts):
            output_path = os.path.join(output_dir, f'part_{i+1}.mp4')
            stream = ffmpeg.input(input_path, ss=i*part_duration, t=part_duration)
            stream = ffmpeg.output(stream, output_path, c='copy', f='mp4', loglevel='error')
            ffmpeg.run(stream)
            output_files.append(output_path)
        
        return output_files
    except Exception as e:
        logging.error(f"خطا در تقسیم ویدیو: {str(e)}")
        raise

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

    # ارسال پیام موقت اولیه
    progress_message = await context.bot.send_message(
        chat_id=GROUP_ID,
        text="در حال پردازش لینک..."
    )

    # متغیر برای ردیابی آخرین درصد گزارش‌شده
    last_reported_percent = -10  # برای اطمینان از گزارش 0% در ابتدا

    # تابع برای به‌روزرسانی پیام پیشرفت
    async def update_progress(status):
        nonlocal last_reported_percent
        if status['status'] == 'downloading':
            percent_str = status.get('_percent_str', '0%').replace('%', '').strip()
            try:
                percent = float(percent_str)
                if percent >= last_reported_percent + 10:  # به‌روزرسانی هر 10 درصد
                    last_reported_percent = int(percent // 10) * 10
                    await context.bot.edit_message_text(
                        chat_id=GROUP_ID,
                        message_id=progress_message.message_id,
                        text=f"{last_reported_percent}% دانلود شده"
                    )
            except ValueError:
                pass  # در صورت خطا در تبدیل درصد، نادیده بگیر
        elif status['status'] == 'finished':
            await context.bot.edit_message_text(
                chat_id=GROUP_ID,
                message_id=progress_message.message_id,
                text="دانلود کامل شد، در حال پردازش ویدیو..."
            )

    try:
        # تنظیم تایمر برای دانلود (3 دقیقه)
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(180)  # 3 دقیقه

        # تنظیمات yt-dlp برای کمترین کیفیت
        ydl_opts = {
            'format': 'worst',  # کمترین کیفیت موجود
            'outtmpl': temp_path,
            'quiet': False,
            'no_warnings': False,
            'verbose': True,
            'max_filesize': 100 * 1024 * 1024,  # 100MB به بایت
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'merge_output_format': 'mp4',
            'retries': 3,
            'socket_timeout': 30,
            'progress_hooks': [update_progress],  # استفاده از تابع به‌روزرسانی پیشرفت
            'extract_flat': True,
        }

        # دانلود ویدیو
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # دریافت اطلاعات ویدیو
                info = ydl.extract_info(message_text, download=False)
                logging.info(f"اطلاعات فایل: {info}")
                
                # بررسی مدت زمان ویدیو
                duration = info.get('duration', 0)
                if duration < 120:  # کمتر از 2 دقیقه
                    await context.bot.edit_message_text(
                        chat_id=GROUP_ID,
                        message_id=progress_message.message_id,
                        text=f"ویدیو کوتاه‌تر از 2 دقیقه است (مدت زمان: {duration} ثانیه). دانلود نمی‌شود."
                    )
                    return
                
                # دانلود ویدیو
                info = ydl.extract_info(message_text, download=True)
                
                # مدیریت فایل دانلود شده
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

        # بررسی سایز فایل و ارسال ویدیو
        file_size = os.path.getsize(temp_path)
        if file_size <= MAX_FILE_SIZE:
            # ارسال مستقیم فایل اگر کمتر یا برابر 50MB باشد
            await context.bot.edit_message_text(
                chat_id=GROUP_ID,
                message_id=progress_message.message_id,
                text="در حال ارسال ویدیو..."
            )
            with open(temp_path, 'rb') as video:
                await context.bot.send_video(
                    chat_id=GROUP_ID,
                    video=video,
                    caption=f"ویدیو از {message_text} (مدت زمان: {duration} ثانیه)",
                    supports_streaming=True
                )
        else:
            # تقسیم ویدیو به بخش‌های کوچکتر
            await context.bot.edit_message_text(
                chat_id=GROUP_ID,
                message_id=progress_message.message_id,
                text="فایل بزرگ است، در حال تقسیم و ارسال ویدیو..."
            )
            video_parts = split_video(temp_path, temp_dir, MAX_FILE_SIZE)
            
            # ارسال هر بخش به گروه
            for i, part_path in enumerate(video_parts, 1):
                with open(part_path, 'rb') as video:
                    await context.bot.send_video(
                        chat_id=GROUP_ID,
                        video=video,
                        caption=f"بخش {i} از ویدیوی {message_text} (مدت زمان کل: {duration} ثانیه)",
                        supports_streaming=True
                    )

        # حذف پیام پیشرفت پس از اتمام
        await context.bot.delete_message(
            chat_id=GROUP_ID,
            message_id=progress_message.message_id
        )

    except DownloadTimeout:
        logging.error("زمان دانلود به پایان رسید")
        await context.bot.edit_message_text(
            chat_id=GROUP_ID,
            message_id=progress_message.message_id,
            text="زمان دانلود به پایان رسید. لطفاً دوباره تلاش کنید."
        )
    except Exception as e:
        logging.error(f"خطا در پردازش ویدیو: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=GROUP_ID,
            message_id=progress_message.message_id,
            text=f"خطا در پردازش ویدیو: {str(e)}"
        )
    finally:
        # غیرفعال کردن تایمر
        signal.alarm(0)
        
        # پاک کردن دایرکتوری موقت
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
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
