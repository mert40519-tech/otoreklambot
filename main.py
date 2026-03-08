import logging
import re
import json
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    CommandHandler,
)

# ─── YAPILANDIRMA ────────────────────────────────────────────────────────────
BOT_TOKEN = "8610004085:AAFItaxPIC65hkD6yElppxJ0v557-9hEc5M"
MAX_WARNINGS = 3          # kaç uyarıda mute
MUTE_DURATION = 30        # dakika
WARNINGS_FILE = "warnings.json"
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── UYARI VERİTABANI (JSON) ─────────────────────────────────────────────────
def load_warnings() -> dict:
    if os.path.exists(WARNINGS_FILE):
        with open(WARNINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_warnings(data: dict):
    with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}:{user_id}"

def get_warnings(chat_id: int, user_id: int) -> int:
    data = load_warnings()
    return data.get(get_key(chat_id, user_id), 0)

def add_warning(chat_id: int, user_id: int) -> int:
    data = load_warnings()
    key = get_key(chat_id, user_id)
    data[key] = data.get(key, 0) + 1
    save_warnings(data)
    return data[key]

def remove_warning(chat_id: int, user_id: int) -> int:
    data = load_warnings()
    key = get_key(chat_id, user_id)
    if data.get(key, 0) > 0:
        data[key] -= 1
    save_warnings(data)
    return data.get(key, 0)

def reset_warnings(chat_id: int, user_id: int):
    data = load_warnings()
    key = get_key(chat_id, user_id)
    data[key] = 0
    save_warnings(data)

# ─── REGEX: GRUP/KANAL LİNKİ AMA KULLANICI ADI DEĞİL ────────────────────────
# t.me/xxx  veya  @xxx  formatlarını yakalar.
# Kullanıcı adları genellikle 5-32 karakter, grup/kanal linkleri de aynı formatta.
# Telegram'da grup/kanal ile kullanıcı adını ayırt etmenin kesin yolu API çağrısıdır.
# Bu bot: mesajda t.me/ veya @ ile başlayan HERHANGİ bir linki yakalar,
# ardından Telegram API'si üzerinden bunun bir grup/kanal mı yoksa kullanıcı mı olduğunu kontrol eder.

LINK_PATTERN = re.compile(
    r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})|@([a-zA-Z][a-zA-Z0-9_]{4,})",
    re.IGNORECASE,
)

async def is_group_or_channel(username: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Verilen kullanıcı adının bir grup veya kanal olup olmadığını kontrol eder."""
    try:
        chat = await context.bot.get_chat(f"@{username}")
        return chat.type in ("group", "supergroup", "channel")
    except Exception:
        # Bulunamazsa ya da hata olursa güvenli tarafta kal → uyarma
        return False

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

# ─── ANA MESAJ HANDLER ───────────────────────────────────────────────────────
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

    # Linki ara
    matches = LINK_PATTERN.findall(message.text)
    if not matches:
        return

    # Her eşleşme için grup/kanal mı kontrol et
    found_illegal = False
    for m in matches:
        username = m[0] or m[1]  # t.me/xxx → m[0], @xxx → m[1]
        if await is_group_or_channel(username, context):
            found_illegal = True
            break

    if not found_illegal:
        return

    # Mesajı sil
    try:
        await message.delete()
    except Exception as e:
        logger.warning(f"Mesaj silinemedi: {e}")
        return

    # Uyarı ekle
    warn_count = add_warning(chat_id, user.id)
    remaining = MAX_WARNINGS - warn_count

    user_mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

    if warn_count >= MAX_WARNINGS:
        # 30 dakika mute
        until = datetime.now() + timedelta(minutes=MUTE_DURATION)
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
        except Exception as e:
            logger.warning(f"Mute uygulanamadı: {e}")

        reset_warnings(chat_id, user.id)

        text = (
            f"🚫 {user_mention} grupta/kanalda link paylaştığı için "
            f"<b>{MUTE_DURATION} dakika susturuldu!</b>\n\n"
            f"⚠️ Grup veya kanal linkleri paylaşmak yasaktır."
        )
        await context.bot.send_message(chat_id, text, parse_mode="HTML")
        return

    # Normal uyarı mesajı + admin butonu
    text = (
        f"⚠️ {user_mention}, grup veya kanal linki paylaştığın için mesajın silindi!\n\n"
        f"📊 Uyarı: <b>{warn_count}/{MAX_WARNINGS}</b>\n"
        f"{'🔴' * warn_count}{'⚪' * remaining}\n\n"
        f"{'🔔 Son uyarın! Bir daha link atarsan susturulacaksın!' if warn_count == MAX_WARNINGS - 1 else ''}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"✅ Uyarıyı Kaldır ({warn_count} → {warn_count - 1})",
                callback_data=f"remove_warn:{chat_id}:{user.id}:{user.first_name}",
            )
        ]
    ])

    await context.bot.send_message(
        chat_id, text, parse_mode="HTML", reply_markup=keyboard
    )

# ─── CALLBACK: UYARI KALDIR ──────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("remove_warn:"):
        return

    # Sadece adminler kullanabilir
    admin_user = query.from_user
    _, chat_id_str, target_id_str, target_name = data.split(":", 3)
    chat_id = int(chat_id_str)
    target_id = int(target_id_str)

    if not await is_admin(chat_id, admin_user.id, context):
        await query.answer("❌ Bu butonu sadece adminler kullanabilir!", show_alert=True)
        return

    new_count = remove_warning(chat_id, target_id)
    admin_mention = f'<a href="tg://user?id={admin_user.id}">{admin_user.first_name}</a>'
    target_mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'

    new_text = (
        f"✅ {admin_mention}, {target_mention} adlı kullanıcının bir uyarısını kaldırdı.\n\n"
        f"📊 Güncel uyarı: <b>{new_count}/{MAX_WARNINGS}</b>"
    )

    await query.edit_message_text(new_text, parse_mode="HTML")

# ─── /uyarilar KOMUTU ────────────────────────────────────────────────────────
async def cmd_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    requester = message.from_user

    if not await is_admin(chat_id, requester.id, context):
        await message.reply_text("❌ Bu komutu sadece adminler kullanabilir.")
        return

    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
    elif context.args:
        try:
            uid = int(context.args[0])
            member = await context.bot.get_chat_member(chat_id, uid)
            target = member.user
        except Exception:
            await message.reply_text("❌ Kullanıcı bulunamadı.")
            return

    if not target:
        await message.reply_text("ℹ️ Kullanım: /uyarilar (birine yanıt verin veya kullanıcı ID yazın)")
        return

    count = get_warnings(chat_id, target.id)
    remaining = MAX_WARNINGS - count
    mention = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'

    text = (
        f"📊 {mention} uyarı durumu:\n\n"
        f"{'🔴' * count}{'⚪' * remaining} — <b>{count}/{MAX_WARNINGS}</b>\n\n"
        f"{'⚠️ Bir sonraki uyarıda susturulacak!' if count == MAX_WARNINGS - 1 else ''}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Uyarıyı Kaldır",
                callback_data=f"remove_warn:{chat_id}:{target.id}:{target.first_name}",
            )
        ]
    ])

    await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

# ─── /resetuyari KOMUTU ──────────────────────────────────────────────────────
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    requester = message.from_user

    if not await is_admin(chat_id, requester.id, context):
        await message.reply_text("❌ Bu komutu sadece adminler kullanabilir.")
        return

    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user

    if not target:
        await message.reply_text("ℹ️ Kullanım: /resetuyari (birine yanıt verin)")
        return

    reset_warnings(chat_id, target.id)
    mention = f'<a href="tg://user?id={target.id}">{target.first_name}</a>'
    await message.reply_text(
        f"🔄 {mention} adlı kullanıcının tüm uyarıları sıfırlandı.",
        parse_mode="HTML",
    )

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("uyarilar", cmd_warnings))
    app.add_handler(CommandHandler("resetuyari", cmd_reset))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.GROUPS, check_message)
    )

    logger.info("Bot başlatıldı...")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
