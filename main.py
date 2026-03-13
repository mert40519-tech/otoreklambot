"""
TikTok Çekim Bot - Telegram Email Scraper
==========================================
Gereksinimler:
    pip install python-telegram-bot==20.7 apify-client

Kullanım:
    1. @BotFather'dan bot token al → BOT_TOKEN
    2. https://console.apify.com/account/integrations → APIFY_TOKEN
    3. python tiktok_bot.py
"""

import re
import json
import uuid
import asyncio
import logging
import datetime
from io import BytesIO
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from apify_client import ApifyClient

# ─────────────────────────────────────────────
# YAPILANDIRMA  ← Sadece bu 3 satırı doldur
# ─────────────────────────────────────────────
BOT_TOKEN   = "BURAYA_BOT_TOKEN_YAZ"     # @BotFather'dan al
APIFY_TOKEN = "BURAYA_APIFY_TOKEN_YAZ"   # apify.com → console → integrations
ADMIN_IDS   = [123456789]                # Kendi Telegram ID'n (@userinfobot ile bak)

# Kaç profil taransın (Apify kredi harcar)
DEFAULT_PROFILES_PER_HASHTAG = 100

# ─────────────────────────────────────────────
# VERİ KATMANI  (JSON dosya tabanlı)
# ─────────────────────────────────────────────
DATA_DIR       = Path("data");  DATA_DIR.mkdir(exist_ok=True)
LICENSES_FILE  = DATA_DIR / "licenses.json"
USERS_FILE     = DATA_DIR / "users.json"
EMAILS_FILE    = DATA_DIR / "emails.json"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"

def _load(path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default

def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

def get_licenses():    return _load(LICENSES_FILE, {})
def get_users():       return _load(USERS_FILE, {})
def get_emails():      return _load(EMAILS_FILE, [])
def get_blacklist():   return _load(BLACKLIST_FILE, [])
def save_licenses(d):  _save(LICENSES_FILE, d)
def save_users(d):     _save(USERS_FILE, d)
def save_emails(d):    _save(EMAILS_FILE, d)
def save_blacklist(d): _save(BLACKLIST_FILE, d)

def get_user(uid: int) -> dict:
    users = get_users()
    key   = str(uid)
    if key not in users:
        users[key] = {
            "id": uid, "licensed": False, "license_key": None,
            "parallel": 6, "delay": 0.5, "scraping": False,
            "scraped_count": 0, "joined": datetime.datetime.now().isoformat(),
        }
        save_users(users)
    return users[key]

def update_user(uid: int, **kw):
    users = get_users(); users[str(uid)].update(kw); save_users(users)

# ─────────────────────────────────────────────
# LİSANS SİSTEMİ
# ─────────────────────────────────────────────
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

def generate_license(parallel=12, delay=0.03) -> str:
    key  = str(uuid.uuid4()).upper()
    lics = get_licenses()
    lics[key] = {"parallel": parallel, "delay": delay, "used_by": None,
                 "created": datetime.datetime.now().isoformat()}
    save_licenses(lics)
    return key

def activate_license(uid: int, key: str):
    lics = get_licenses(); key = key.strip().upper()
    if key not in lics:
        return False, "❌ Geçersiz lisans anahtarı!"
    lic = lics[key]
    if lic["used_by"] and lic["used_by"] != str(uid):
        return False, "❌ Bu lisans başka bir kullanıcıya ait!"
    lic["used_by"] = str(uid); save_licenses(lics)
    update_user(uid, licensed=True, license_key=key,
                parallel=lic["parallel"], delay=lic["delay"])
    return True, f"✅ Lisans aktif! {lic['parallel']} paralel işlem, {lic['delay']}s gecikme"

# ─────────────────────────────────────────────
# GERÇEK APİFY SCRAPER
# ─────────────────────────────────────────────

async def scrape_tiktok_emails(
    targets: list,
    user: dict,
    on_progress,
) -> list:
    """
    Apify resmi TikTok Scraper aktörünü kullanarak
    profil bio'larından email adresi toplar.

    Aktör: apify/tiktok-scraper
    Docs : https://apify.com/apify/tiktok-scraper
    """
    client    = ApifyClient(APIFY_TOKEN)
    blacklist = get_blacklist()
    found     = []

    hashtags = [t.lstrip("#") for t in targets if t.startswith("#")]
    profiles = [t.lstrip("@") for t in targets if t.startswith("@")]

    run_input = {
        "hashtags":                      hashtags,
        "profiles":                      profiles,
        "resultsPerPage":                DEFAULT_PROFILES_PER_HASHTAG,
        "shouldDownloadVideos":          False,
        "shouldDownloadCovers":          False,
        "shouldDownloadSubtitles":       False,
        "shouldDownloadSlideshowImages": False,
    }

    # Apify senkron çağrısını thread pool'da çalıştır
    loop = asyncio.get_event_loop()

    def run_apify():
        run   = client.actor("apify/tiktok-scraper").call(run_input=run_input)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        return items

    items = await loop.run_in_executor(None, run_apify)

    total = len(items)
    for i, item in enumerate(items):
        # Bio alanı
        bio = (
            item.get("authorMeta", {}).get("signature", "")
            or item.get("description", "")
            or item.get("text", "")
        )
        for em in EMAIL_RE.findall(bio):
            em_lower = em.lower()
            if em_lower not in blacklist and em_lower not in found:
                found.append(em_lower)

        await on_progress(i + 1, total, len(found))

    return found

# ─────────────────────────────────────────────
# KLAVYELER
# ─────────────────────────────────────────────

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Durum ve İstatistikler", callback_data="status")],
        [
            InlineKeyboardButton("▶️ Scraper'ı Başlat", callback_data="start_scraper"),
            InlineKeyboardButton("⏹ Scraper'ı Durdur",  callback_data="stop_scraper"),
        ],
        [InlineKeyboardButton("🗑 Blacklist Yönetimi",   callback_data="blacklist")],
        [InlineKeyboardButton("📩 Mailleri Al",          callback_data="get_emails")],
        [InlineKeyboardButton("⚙️ Ayarlar",              callback_data="settings")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Ana Menü", callback_data="main_menu")
    ]])

# ─────────────────────────────────────────────
# KOMUT HANDLERLARI
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = get_user(uid)
    if not user["licensed"]:
        await update.message.reply_text(
            "❌ *Yetkisiz erişim!*\n\nBu botu kullanmak için geçerli bir lisansa ihtiyacınız var.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔑 Lisans Aktifleştir", callback_data="activate_license")
            ]])
        )
        return
    await update.message.reply_text(
        "🤖 *Ana Menü*\n\nAşağıdaki butonlardan işleminizi seçin:",
        parse_mode="Markdown", reply_markup=main_menu_kb()
    )

async def cmd_activate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ctx.args:
        await update.message.reply_text("Kullanım: /activate <lisans_anahtarı>")
        return
    ok, msg = activate_license(uid, ctx.args[0])
    await update.message.reply_text(msg)
    if ok:
        await update.message.reply_text(
            "🤖 *Ana Menü*\n\nAşağıdaki butonlardan işleminizi seçin:",
            parse_mode="Markdown", reply_markup=main_menu_kb()
        )

async def cmd_genlic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Yetkisiz.")
        return
    parallel = int(ctx.args[0]) if ctx.args else 12
    delay    = float(ctx.args[1]) if len(ctx.args) > 1 else 0.03
    key = generate_license(parallel, delay)
    await update.message.reply_text(
        f"✅ Yeni lisans:\n\n`/activate {key}`\n\n⚡ {parallel} paralel | ⏱ {delay}s",
        parse_mode="Markdown"
    )

async def cmd_set_parallel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(ctx.args[0]); assert 1 <= val <= 20
    except Exception:
        await update.message.reply_text("Kullanım: /set_parallel <1-20>")
        return
    update_user(update.effective_user.id, parallel=val)
    await update.message.reply_text(f"✅ Paralel işlem: {val}")

async def cmd_set_delay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(ctx.args[0]); assert 0.01 <= val <= 5
    except Exception:
        await update.message.reply_text("Kullanım: /set_delay <0.01-5>")
        return
    update_user(update.effective_user.id, delay=val)
    await update.message.reply_text(f"✅ Gecikme: {val}s")

async def cmd_blacklist_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Kullanım: /blacklist_remove <email>")
        return
    em = ctx.args[0].lower(); bl = get_blacklist()
    if em in bl:
        bl.remove(em); save_blacklist(bl)
        await update.message.reply_text(f"✅ {em} kaldırıldı.")
    else:
        await update.message.reply_text(f"⚠️ {em} listede yok.")

# ─────────────────────────────────────────────
# CALLBACK QUERY ROUTER
# ─────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query; await q.answer()
    uid  = update.effective_user.id
    user = get_user(uid)
    data = q.data

    if data == "activate_license":
        await q.edit_message_text(
            "🔑 Lisans anahtarınızı gönderin:\n\n`/activate XXXX-XXXX-XXXX-XXXX`",
            parse_mode="Markdown"
        )
        return

    if not user["licensed"]:
        await q.edit_message_text("❌ Önce /activate ile lisansınızı aktifleştirin.")
        return

    if data == "main_menu":
        await q.edit_message_text(
            "🤖 *Ana Menü*\n\nAşağıdaki butonlardan işleminizi seçin:",
            parse_mode="Markdown", reply_markup=main_menu_kb()
        )

    elif data == "status":
        emails = get_emails(); bl = get_blacklist()
        status = "🟢 Aktif" if user["scraping"] else "🔴 Pasif"
        await q.edit_message_text(
            f"📊 *İstatistikler*\n\n"
            f"▸ Scraper  : {status}\n"
            f"▸ Email    : `{len(emails)}`\n"
            f"▸ Blacklist: `{len(bl)}`\n"
            f"▸ Paralel  : `{user['parallel']}`\n"
            f"▸ Gecikme  : `{user['delay']}s`\n"
            f"▸ Lisans   : `{user['license_key']}`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "start_scraper":
        update_user(uid, scraping=True)
        await q.edit_message_text(
            "▶️ *Hangi hashtag veya kullanıcı adı taransın?*\n\n"
            "• Hashtag : `#fitness`\n"
            "• Profil  : `@nike`\n"
            "• Çoklu   : `#fitness, #lifestyle, @nike`\n\n"
            "Şimdi gönderin:",
            parse_mode="Markdown", reply_markup=back_kb()
        )
        ctx.user_data["waiting"] = "hashtag"

    elif data == "stop_scraper":
        update_user(uid, scraping=False)
        ctx.user_data["waiting"] = None
        await q.edit_message_text("⏹ Scraper durduruldu.", reply_markup=back_kb())

    elif data == "get_emails":
        emails = get_emails()
        if not emails:
            await q.edit_message_text("📭 Henüz email toplanmadı.", reply_markup=back_kb())
            return
        buf = BytesIO("\n".join(emails).encode()); buf.name = "emails.txt"
        await q.message.reply_document(
            document=buf, filename="emails.txt",
            caption=f"📩 Toplam *{len(emails)}* email", parse_mode="Markdown"
        )
        await q.edit_message_text(
            f"✅ *{len(emails)}* email gönderildi.",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "blacklist":
        bl   = get_blacklist()
        text = "🗑 *Blacklist*\n\n"
        text += ("\n".join(f"• `{e}`" for e in bl[:20]) or "_Boş_")
        if len(bl) > 20: text += f"\n_...{len(bl)-20} daha_"
        text += "\n\nEklemek için email adresini yazın:"
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())
        ctx.user_data["waiting"] = "blacklist"

    elif data == "settings":
        await q.edit_message_text(
            f"⚙️ *Ayarlar*\n\n"
            f"▸ Paralel: `{user['parallel']}`  →  `/set_parallel <1-20>`\n"
            f"▸ Gecikme: `{user['delay']}s`  →  `/set_delay <0.01-5>`\n\n"
            f"_Not: Daha fazla paralel = daha fazla Apify kredisi_",
            parse_mode="Markdown", reply_markup=back_kb()
        )

# ─────────────────────────────────────────────
# METİN MESAJI HANDLER
# ─────────────────────────────────────────────

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    user    = get_user(uid)
    waiting = ctx.user_data.get("waiting")
    text    = update.message.text.strip()

    if not user["licensed"]:
        await update.message.reply_text("❌ Önce /activate ile lisansınızı aktifleştirin.")
        return

    # ── Hashtag / profil girişi ──────────────
    if waiting == "hashtag":
        ctx.user_data["waiting"] = None
        targets = [t.strip() for t in text.split(",") if t.strip()]
        targets = [t if t.startswith(("#", "@")) else f"#{t}" for t in targets]

        msg = await update.message.reply_text(
            f"⚙️ *Apify ile scraping başlatıldı...*\n"
            f"🔍 Hedef: `{'`, `'.join(targets)}`\n\n"
            f"⏳ Aktör başlatılıyor...",
            parse_mode="Markdown"
        )

        async def on_progress(done, total, found_count):
            if total == 0 or done % 5 != 0 and done != total:
                return
            pct = done * 10 // total
            bar = "█" * pct + "░" * (10 - pct)
            try:
                await msg.edit_text(
                    f"⚙️ *Scraping devam ediyor...*\n"
                    f"🔍 Hedef: `{'`, `'.join(targets)}`\n\n"
                    f"`[{bar}]` {done}/{total} profil\n"
                    f"📧 Bulunan email: *{found_count}*",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        try:
            emails = await scrape_tiktok_emails(targets, user, on_progress)
        except Exception as e:
            await msg.edit_text(
                f"❌ *Scraping hatası:*\n`{e}`\n\n"
                f"Apify token ve internet bağlantısını kontrol et.",
                parse_mode="Markdown", reply_markup=main_menu_kb()
            )
            update_user(uid, scraping=False)
            return

        existing  = get_emails()
        new_count = 0
        for em in emails:
            if em not in existing:
                existing.append(em); new_count += 1
        save_emails(existing)
        update_user(uid, scraped_count=user["scraped_count"] + new_count, scraping=False)

        await msg.edit_text(
            f"✅ *Scraping tamamlandı!*\n\n"
            f"📧 Yeni email  : *{new_count}*\n"
            f"📦 Toplam email: *{len(existing)}*\n\n"
            f"_Mailleri almak için → Mailleri Al_",
            parse_mode="Markdown", reply_markup=main_menu_kb()
        )

    # ── Blacklist ekleme ─────────────────────
    elif waiting == "blacklist":
        ctx.user_data["waiting"] = None
        new_emails = [e.lower() for e in EMAIL_RE.findall(text)]
        if not new_emails:
            await update.message.reply_text("⚠️ Geçerli email bulunamadı.")
            return
        bl = get_blacklist()
        added = 0
        for e in new_emails:
            if e not in bl:
                bl.append(e); added += 1
        save_blacklist(bl)
        await update.message.reply_text(
            f"✅ {added} email blacklist'e eklendi.", reply_markup=main_menu_kb()
        )

# ─────────────────────────────────────────────
# BAŞLAT
# ─────────────────────────────────────────────

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",            cmd_start))
    app.add_handler(CommandHandler("activate",         cmd_activate))
    app.add_handler(CommandHandler("genlic",           cmd_genlic))
    app.add_handler(CommandHandler("set_parallel",     cmd_set_parallel))
    app.add_handler(CommandHandler("set_delay",        cmd_set_delay))
    app.add_handler(CommandHandler("blacklist_remove", cmd_blacklist_remove))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    print("🤖 TikTok Çekim Bot başlatıldı...")
    app.run_polling()

if __name__ == "__main__":
    main()
