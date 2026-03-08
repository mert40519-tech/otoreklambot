import logging
import re
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes,
)

# ─── YAPILANDIRMA ────────────────────────────────────────────────────────────
BOT_TOKEN "8610004085:AAFWTrYkgdMlP2hlwR9rKX5bUSoBrGR809c"
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# t.me/xxx veya @xxx linklerini yakalar
LINK_PATTERN = re.compile(
    r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})|@([a-zA-Z][a-zA-Z0-9_]{4,})",
    re.IGNORECASE,
)

async def is_group_or_channel(username: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Kullanıcı adının bir grup veya kanal olup olmadığını kontrol eder."""
    try:
        chat = await context.bot.get_chat(f"@{username}")
        return chat.type in ("group", "supergroup", "channel")
    except Exception:
        return False

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    chat_id = message.chat_id
    user = message.from_user
    if not user:
        return

    # Adminleri atla
    if await is_admin(chat_id, user.id, context):
        return

    # Link var mı?
    matches = LINK_PATTERN.findall(message.text)
    if not matches:
        return

    # Grup/kanal mı kontrol et
    for m in matches:
        username = m[0] or m[1]
        if await is_group_or_channel(username, context):
            try:
                await message.delete()
            except Exception as e:
                logger.warning(f"Mesaj silinemedi: {e}")
            return  # ilk grup/kanal linki bulunca sil ve çık

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.GROUPS, check_message)
    )

    logger.info("Bot başlatıldı...")
    app.run_polling(allowed_updates=["message"])

if __name__ == "__main__":
    main()

