import os
import logging
import asyncio
import tempfile
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import yt_dlp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CHOOSING_PLATFORM, WAITING_FOR_LINK = range(2)

PLATFORMS = {
    "tiktok": {"name": "TikTok", "emoji": "🎵", "domains": ["tiktok.com", "vm.tiktok.com"]},
    "instagram": {"name": "Instagram", "emoji": "📸", "domains": ["instagram.com", "instagr.am"]},
    "youtube": {"name": "YouTube", "emoji": "▶️", "domains": ["youtube.com", "youtu.be"]},
    "twitter": {"name": "Twitter / X", "emoji": "🐦", "domains": ["twitter.com", "x.com"]},
    "facebook": {"name": "Facebook", "emoji": "👥", "domains": ["facebook.com", "fb.com", "fb.watch"]},
    "vk": {"name": "ВКонтакте", "emoji": "💙", "domains": ["vk.com"]},
    "other": {"name": "Другая соцсеть", "emoji": "🌐", "domains": []},
}


def build_main_menu():
    keyboard = []
    row = []
    for key, info in PLATFORMS.items():
        btn = InlineKeyboardButton(f"{info['emoji']} {info['name']}", callback_data=f"platform:{key}")
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Привет! Я бот для скачивания видео из соцсетей.\n\n"
        "Выбери платформу, с которой хочешь скачать видео:",
        reply_markup=build_main_menu(),
    )
    return CHOOSING_PLATFORM


async def platform_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    platform_key = query.data.replace("platform:", "")
    platform = PLATFORMS.get(platform_key)
    if not platform:
        await query.edit_message_text("❌ Неизвестная платформа. Попробуй /start.")
        return ConversationHandler.END
    context.user_data["platform"] = platform_key
    examples = {
        "tiktok": "https://www.tiktok.com/@user/video/...",
        "instagram": "https://www.instagram.com/reel/...",
        "youtube": "https://youtu.be/...",
        "twitter": "https://x.com/user/status/...",
        "facebook": "https://www.facebook.com/watch/?v=...",
        "vk": "https://vk.com/video...",
        "other": "https://...",
    }
    example_url = examples.get(platform_key, "https://...")
    await query.edit_message_text(
        f"{platform['emoji']} *{platform['name']}* выбран!\n\n"
        f"📎 Отправь ссылку на видео.\n\n"
        f"Пример:\n`{example_url}`\n\n"
        f"Или нажми /cancel для отмены.",
        parse_mode="Markdown",
    )
    return WAITING_FOR_LINK


async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    platform_key = context.user_data.get("platform", "other")
    platform = PLATFORMS.get(platform_key, PLATFORMS["other"])
    if not url.startswith("http://") and not url.startswith("https://"):
        await update.message.reply_text("❌ Это не похоже на ссылку. Отправь корректный URL.")
        return WAITING_FOR_LINK
    status_msg = await update.message.reply_text(
        f"⏳ Скачиваю видео с {platform['emoji']} {platform['name']}...\nЭто может занять несколько секунд."
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_template = os.path.join(tmp_dir, "%(title).50s.%(ext)s")
        ydl_opts = {
            "outtmpl": output_template,
            "format": "bestvideo[ext=mp4][filesize<49M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<49M]/best[filesize<49M]/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 3,
            "noplaylist": True,
        }
        try:
            loop = asyncio.get_event_loop()
            def _download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)
            info = await loop.run_in_executor(None, _download)
            video_files = list(Path(tmp_dir).glob("*"))
            if not video_files:
                await status_msg.edit_text("❌ Не удалось скачать видео. Ссылка недействительна или видео недоступно.")
                return await _ask_again(update, context)
            video_path = video_files[0]
            if video_path.stat().st_size > 50 * 1024 * 1024:
                await status_msg.edit_text("❌ Видео слишком большое (больше 50 МБ).")
                return await _ask_again(update, context)
            title = info.get("title", "Видео") if info else "Видео"
            duration = info.get("duration") if info else None
            duration_str = f" • {int(duration // 60)}:{int(duration % 60):02d}" if duration else ""
            await status_msg.edit_text("📤 Отправляю видео...")
            with open(video_path, "rb") as vf:
                await update.message.reply_video(
                    video=vf,
                    caption=f"{platform['emoji']} *{title[:200]}*{duration_str}\n\nСкачано с {platform['name']}",
                    parse_mode="Markdown",
                    read_timeout=120,
                    write_timeout=120,
                )
            await status_msg.delete()
        except yt_dlp.utils.DownloadError as e:
            err_str = str(e).lower()
            if "private" in err_str or "login" in err_str:
                msg = "🔒 Приватное видео или требуется авторизация."
            elif "not found" in err_str or "404" in err_str:
                msg = "❌ Видео не найдено. Проверь ссылку."
            elif "geo" in err_str or "country" in err_str:
                msg = "🌍 Видео недоступно в твоём регионе."
            else:
                msg = "❌ Ошибка скачивания. Попробуй другую ссылку."
            await status_msg.edit_text(msg)
            return await _ask_again(update, context)
        except Exception as e:
            logger.error(f"Error: {e}")
            await status_msg.edit_text("❌ Произошла ошибка. Попробуй ещё раз.")
            return await _ask_again(update, context)
    return await _ask_again(update, context)


async def _ask_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[
        InlineKeyboardButton("🔄 Скачать ещё", callback_data="again"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu"),
    ]]
    await update.message.reply_text("Что делаем дальше?", reply_markup=InlineKeyboardMarkup(keyboard))
    return WAITING_FOR_LINK


async def again_or_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "menu":
        context.user_data.clear()
        await query.edit_message_text("Выбери платформу:", reply_markup=build_main_menu())
        return CHOOSING_PLATFORM
    else:
        platform_key = context.user_data.get("platform", "other")
        platform = PLATFORMS.get(platform_key, PLATFORMS["other"])
        await query.edit_message_text(f"{platform['emoji']} Отправь ещё одну ссылку с {platform['name']}:")
        return WAITING_FOR_LINK


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("✅ Отменено. Напиши /start чтобы начать заново.")
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception: {context.error}")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан!")
    app = Application.builder().token(token).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_PLATFORM: [CallbackQueryHandler(platform_chosen, pattern=r"^platform:")],
            WAITING_FOR_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, download_video),
                CallbackQueryHandler(again_or_menu, pattern=r"^(again|menu)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_error_handler(error_handler)
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()