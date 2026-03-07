import json, os, asyncio, logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import nest_asyncio

nest_asyncio.apply()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ================= AYARLAR =================
ADMIN_ID = 8532799482  
BOT_TOKEN = "8618683721:AAE7oMXzzWhqzE_2iUCO8DFh_SreGVOwhmk" 
DATA_FILE = "katre_veritabani.json"

# State Tanımları
WAIT_LICENSE, WAIT_API, WAIT_PHONE, WAIT_CODE, WAIT_2FA, WAIT_MESSAGE, WAIT_DELAY, WAIT_BLACKLIST = range(8)

active_tasks = {}

# ================= VERİTABANI =================
def load_db():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {"lisanslar": {}, "users": {}}

def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(db, f, indent=4, ensure_ascii=False)

db = load_db()

def check_license(u_id):
    u_id = str(u_id)
    if u_id in db["users"] and db["users"][u_id].get("lisans_bitis"):
        bitis = datetime.strptime(db["users"][u_id]["lisans_bitis"], "%Y-%m-%d %H:%M")
        return bitis > datetime.now()
    return False

# ================= MENÜLER =================
def menu_main(u_id):
    u = db["users"].get(str(u_id), {})
    is_active = u.get("active", False)
    hesap_sayisi = len(u.get("hesaplar", []))
    limit = u.get("limit", 0)
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👤 Hesap Ekle ({hesap_sayisi}/{limit})", callback_data="btn_hesap_ekle")],
        [InlineKeyboardButton("⏸ Kampanya Durdur" if is_active else "🚀 Kampanya Başlat", callback_data="btn_toggle")],
        [InlineKeyboardButton("⚙️ Ayarlar", callback_data="btn_ayarlar"), InlineKeyboardButton("📊 Durum", callback_data="btn_durum")],
        [InlineKeyboardButton("🚫 Kara Liste", callback_data="btn_karaliste"), InlineKeyboardButton("📋 Loglar", callback_data="btn_loglar")],
        [InlineKeyboardButton("🔑 Lisans Bilgisi", callback_data="btn_lisans_bilgi")]
    ])

def menu_ayarlar():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Mesaj Metni Belirle", callback_data="btn_set_msg")],
        [InlineKeyboardButton("⏱ Bekleme Süresi Ayarla", callback_data="btn_set_time")],
        [InlineKeyboardButton("⬅️ Ana Menüye Dön", callback_data="btn_ana_menu")]
    ])

# ================= REKLAM DÖNGÜSÜ =================
async def reklam_motoru(u_id, hesap, chat_id, bot_app):
    u_id = str(u_id)
    phone = hesap["phone"]
    client = TelegramClient(f"sessions/{u_id}_{phone}", int(hesap["api_id"]), hesap["api_hash"])
    try:
        await client.connect()
        while db["users"][u_id]["active"]:
            u_data = db["users"][u_id]
            basarili, basarisiz = 0, 0
            async for dialog in client.iter_dialogs():
                if not db["users"][u_id]["active"]: break
                if dialog.is_group and str(dialog.id) not in u_data["kara_liste"] and str(dialog.name) not in u_data["kara_liste"]:
                    try:
                        await client.send_message(dialog.id, u_data["mesaj"])
                        basarili += 1
                        await asyncio.sleep(2)
                    except: basarisiz += 1
            
            zaman = datetime.now().strftime('%H:%M')
            log_text = f"<b>[{zaman}] {phone}:</b> {basarili} Başarılı, {basarisiz} Hata."
            u_data["loglar"].append(log_text)
            if len(u_data["loglar"]) > 10: u_data["loglar"].pop(0)
            save_db(db)
            await bot_app.bot.send_message(chat_id=chat_id, text=f"ℹ️ {log_text}", parse_mode="HTML")
            
            for _ in range(u_data["sure"] * 60):
                if not db["users"][u_id]["active"]: break
                await asyncio.sleep(1)
    finally: await client.disconnect()

# ================= ADMİN KOMUTLARI =================
async def cmd_keyuret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        sa, gu, ay, yi, paket_adi, cihaz_limiti = context.args
        paketler = {"bronz": 1, "elmas": 3, "vip": 5}
        p_name = paket_adi.lower()
        if p_name not in paketler: return
        
        new_key = f"KATRE-{os.urandom(3).hex().upper()}"
        bitis = datetime.now() + timedelta(days=int(gu)+(int(ay)*30)+(int(yi)*365), hours=int(sa))
        db["lisanslar"][new_key] = {"bitis": bitis.strftime("%Y-%m-%d %H:%M"), "paket": p_name.upper(), "h_limit": paketler[p_name], "c_limit": int(cihaz_limiti), "k_cihazlar": []}
        save_db(db)
        await update.message.reply_text(f"✅ <b>Key Üretildi:</b>\n<code>{new_key}</code>\n💎 <b>Paket:</b> {p_name.upper()}\n👥 <b>Cihaz:</b> {cihaz_limiti}", parse_mode="HTML")
    except:
        await update.message.reply_text("<b>Kullanım:</b> /keyuret saat gun ay yil paket cihazlimiti", parse_mode="HTML")

async def cmd_keysil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        key = context.args[0]
        if key in db["lisanslar"]:
            del db["lisanslar"][key]; save_db(db)
            await update.message.reply_text("✅ Key silindi.")
    except: pass

# ================= ANA CALLBACK HANDLER =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u_id = str(query.from_user.id)
    data = query.data
    await query.answer()

    if data == "btn_ana_menu":
        await query.edit_message_text("🤖 <b>KATRE Ana Menü</b>", reply_markup=menu_main(u_id), parse_mode="HTML")
        return ConversationHandler.END

    elif data == "btn_lisans_bilgi":
        u = db["users"].get(u_id, {})
        txt = (f"🔑 <b>LİSANS BİLGİSİ</b>\n\n<b>Key:</b> <code>{u.get('aktif_key','Yok')}</code>\n"
               f"<b>Paket:</b> {u.get('paket','Yok')}\n<b>Hesap Limiti:</b> {u.get('limit',0)}\n"
               f"<b>Bitiş:</b> {u.get('lisans_bitis','Yok')}")
        await query.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Geri", callback_data="btn_ana_menu")]]))
        return ConversationHandler.END

    elif data == "btn_loglar":
        logs = "\n".join(db["users"][u_id]["loglar"]) if db["users"][u_id]["loglar"] else "<i>Log yok.</i>"
        await query.message.reply_text(f"📋 <b>LOGLAR</b>\n\n{logs}", parse_mode="HTML")

    elif data == "btn_karaliste":
        kl = "\n".join(db["users"][u_id]["kara_liste"]) if db["users"][u_id]["kara_liste"] else "<i>Liste boş.</i>"
        kb = [[InlineKeyboardButton("➕ Ekle", callback_data="btn_kl_ekle"), InlineKeyboardButton("🗑 Temizle", callback_data="btn_kl_temizle")], [InlineKeyboardButton("⬅️ Geri", callback_data="btn_ana_menu")]]
        await query.edit_message_text(f"🚫 <b>KARA LİSTE</b>\n\n{kl}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data == "btn_kl_ekle":
        await query.message.reply_text("🚫 Engellenecek grup ID veya ismini yazın:")
        return WAIT_BLACKLIST

    elif data == "btn_kl_temizle":
        db["users"][u_id]["kara_liste"] = []; save_db(db)
        await query.message.reply_text("✅ Temizlendi.")

    elif data == "btn_ayarlar":
        await query.edit_message_text("⚙️ <b>AYARLAR</b>", reply_markup=menu_ayarlar(), parse_mode="HTML")

    elif data == "btn_toggle":
        u = db["users"][u_id]
        if not u["active"]:
            if not u["hesaplar"] or u["mesaj"] == "Belirlenmedi":
                await query.message.reply_text("❌ Önce hesap ekleyin ve mesaj ayarlayın!")
                return
            u["active"] = True; save_db(db)
            active_tasks[u_id] = [asyncio.create_task(reklam_motoru(u_id, h, query.message.chat_id, context.application)) for h in u["hesaplar"]]
            await query.edit_message_text("🚀 <b>Kampanya Başladı!</b>", reply_markup=menu_main(u_id), parse_mode="HTML")
        else:
            u["active"] = False; save_db(db)
            if u_id in active_tasks:
                for t in active_tasks[u_id]: t.cancel()
            await query.edit_message_text("⏸ <b>Kampanya Durduruldu.</b>", reply_markup=menu_main(u_id), parse_mode="HTML")

    elif data == "btn_hesap_ekle":
        if len(db["users"][u_id]["hesaplar"]) >= db["users"][u_id]["limit"]:
            await query.message.reply_text("⚠️ Paket limitiniz doldu!")
            return ConversationHandler.END
        await query.message.reply_text("ℹ️ <b>API_ID API_HASH</b> gönderin:", parse_mode="HTML")
        return WAIT_API

    elif data == "btn_set_msg":
        await query.message.reply_text("💬 Reklam mesajınızı yazın:")
        return WAIT_MESSAGE

    elif data == "btn_set_time":
        await query.message.reply_text("⏱ Kaç dakika beklensin? (Sadece sayı):")
        return WAIT_DELAY

    elif data == "btn_lisans_gir":
        await query.message.reply_text("🔑 Lisans anahtarınızı gönderin:")
        return WAIT_LICENSE

# ================= GİRİŞ FONKSİYONLARI =================
async def input_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    u_id = str(update.effective_user.id)
    if key in db["lisanslar"]:
        l = db["lisanslar"][key]
        if u_id not in l["k_cihazlar"]:
            if len(l["k_cihazlar"]) >= l["c_limit"]:
                await update.message.reply_text("❌ Bu keyin cihaz limiti dolmuş!")
                return ConversationHandler.END
            l["k_cihazlar"].append(u_id)
        db["users"][u_id].update({"lisans_bitis": l["bitis"], "paket": l["paket"], "limit": l["h_limit"], "aktif_key": key})
        save_db(db); await update.message.reply_text("✅ Lisans Aktif!"); return ConversationHandler.END
    await update.message.reply_text("❌ Geçersiz Key!"); return WAIT_LICENSE

async def input_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    u_id = str(update.effective_user.id)
    context.user_data['temp_phone'] = phone
    client = TelegramClient(f"sessions/{u_id}_{phone}", context.user_data['temp_api_id'], context.user_data['temp_api_hash'])
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        context.user_data['phone_code_hash'] = sent.phone_code_hash
        await update.message.reply_text("📩 Kodu <b>1-2-3-4-5</b> şeklinde girin:", parse_mode="HTML")
        return WAIT_CODE
    except Exception as e: await update.message.reply_text(f"Hata: {e}"); return ConversationHandler.END
    finally: await client.disconnect()

async def input_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clean_code = update.message.text.replace("-", "").replace(" ", "")
    u_id = str(update.effective_user.id)
    phone = context.user_data['temp_phone']
    client = TelegramClient(f"sessions/{u_id}_{phone}", context.user_data['temp_api_id'], context.user_data['temp_api_hash'])
    await client.connect()
    try:
        await client.sign_in(phone, clean_code, phone_code_hash=context.user_data['phone_code_hash'])
        db["users"][u_id]["hesaplar"].append({"phone": phone, "api_id": context.user_data['temp_api_id'], "api_hash": context.user_data['temp_api_hash']})
        save_db(db); await update.message.reply_text("✅ Hesap Eklendi!", reply_markup=menu_main(u_id)); return ConversationHandler.END
    except SessionPasswordNeededError:
        await update.message.reply_text("🔐 2FA Şifresi girin:"); return WAIT_2FA
    except Exception as e: await update.message.reply_text(f"Hata: {e}"); return ConversationHandler.END
    finally: await client.disconnect()

# ================= DİĞER INPUTLAR =================
async def input_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        aid, ahash = update.message.text.split()
        context.user_data['temp_api_id'], context.user_data['temp_api_hash'] = int(aid), ahash
        await update.message.reply_text("📞 Telefon No (+90...):"); return WAIT_PHONE
    except: return WAIT_API

async def input_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db["users"][str(update.effective_user.id)]["mesaj"] = update.message.text
    save_db(db); await update.message.reply_text("✅ Mesaj Kaydedildi."); return ConversationHandler.END

async def input_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db["users"][str(update.effective_user.id)]["sure"] = int(update.message.text)
        save_db(db); await update.message.reply_text("✅ Süre Kaydedildi."); return ConversationHandler.END
    except: return WAIT_DELAY

async def input_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db["users"][str(update.effective_user.id)]["kara_liste"].append(update.message.text)
    save_db(db); await update.message.reply_text("✅ Kara listeye eklendi."); return ConversationHandler.END

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = str(update.effective_user.id)
    if u_id not in db["users"]:
        db["users"][u_id] = {"hesaplar": [], "mesaj": "Belirlenmedi", "sure": 5, "active": False, "lisans_bitis": None, "paket": "Yok", "limit": 0, "loglar": [], "kara_liste": [], "aktif_key": "Yok"}
        save_db(db)
    
    u = db["users"][u_id]
    if not check_license(u_id):
        await update.message.reply_text("⛔️ <b>KATRE Reklam Botu</b>\nLisansınız aktif değil.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Lisans Gir", callback_data="btn_lisans_gir")]]), parse_mode="HTML")
        return ConversationHandler.END

    txt = (f"🚀 <b>KATRE PRO REKLAM BOTU</b>\n\n"
           f"💎 <b>Paket:</b> {u['paket']}\n"
           f"👤 <b>Hesap:</b> {len(u['hesaplar'])}/{u['limit']}\n"
           f"📅 <b>Bitiş:</b> <code>{u['lisans_bitis']}</code>")
    await update.message.reply_text(txt, reply_markup=menu_main(u_id), parse_mode="HTML")
    return ConversationHandler.END

def main():
    if not os.path.exists("sessions"): os.makedirs("sessions")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start), CallbackQueryHandler(callback_handler)],
        states={
            WAIT_LICENSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_license)],
            WAIT_API: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_api)],
            WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_phone)],
            WAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_code)],
            WAIT_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_message)],
            WAIT_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_delay)],
            WAIT_BLACKLIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_blacklist)]
        },
        fallbacks=[CommandHandler("start", cmd_start), CallbackQueryHandler(callback_handler)]
    )
    app.add_handler(CommandHandler("keyuret", cmd_keyuret))
    app.add_handler(CommandHandler("keysil", cmd_keysil))
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__": main()
