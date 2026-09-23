import asyncio, logging, os
from html import escape
from dotenv import load_dotenv
from supabase import create_client
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
ADMINS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
DB = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
router = Router()

class Add(StatesGroup):
    source = State()
    code = State()
    title = State()
    description = State()
    year = State()
    genre = State()
    rating = State()

class EditChannel(StatesGroup):
    channel_id = State()
    username = State()
    link = State()

class Broadcast(StatesGroup):
    message = State()

def admin(uid): return uid in ADMINS

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def menu():
    return kb([
        [InlineKeyboardButton(text="🔎 Kino qidirish", callback_data="search")],
        [InlineKeyboardButton(text="🎬 Yangi kinolar", callback_data="new"),
         InlineKeyboardButton(text="🔥 Mashhur", callback_data="popular")],
        [InlineKeyboardButton(text="📚 Janrlar", callback_data="genres")],
        [InlineKeyboardButton(text="ℹ️ Yordam", callback_data="help")]
    ])

def admin_menu():
    return kb([
        [InlineKeyboardButton(text="➕ Kino qo‘shish", callback_data="add")],
        [InlineKeyboardButton(text="📢 Reklama", callback_data="broadcast")],
        [InlineKeyboardButton(text="⚙️ Kanalni almashtirish", callback_data="channel")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")]
    ])

async def setting(name, default=""):
    r = DB.table("settings").select("value").eq("name", name).limit(1).execute()
    return r.data[0]["value"] if r.data else default

async def set_setting(name, value):
    DB.table("settings").upsert({"name": name, "value": str(value)}, on_conflict="name").execute()

async def channel_data():
    return await setting("channel_id", os.getenv("CHANNEL_ID", "")), await setting("channel_username", os.getenv("CHANNEL_USERNAME", "")), await setting("channel_link", os.getenv("CHANNEL_INVITE_URL", ""))

async def subscribed(bot, uid):
    cid, _, _ = await channel_data()
    if not cid: return True
    try:
        m = await bot.get_chat_member(int(cid), uid)
        return m.status in ("creator", "administrator", "member")
    except Exception as e:
        logging.error("Subscription check: %s", e)
        return False

async def sub_message(bot, message):
    cid, username, link = await channel_data()
    url = link or (f"https://t.me/{username.lstrip('@')}" if username else "")
    rows = []
    if url: rows.append([InlineKeyboardButton(text="📢 Kanalga obuna bo‘lish", url=url)])
    rows.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    await message.answer("❌ Avval kanalga obuna bo‘ling.", reply_markup=kb(rows))

async def allowed(bot, message):
    if await subscribed(bot, message.from_user.id): return True
    await sub_message(bot, message)
    return False

async def save_user(message):
    DB.table("users").upsert({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username or "",
        "first_name": message.from_user.first_name or ""
    }, on_conflict="telegram_id").execute()

def card(m):
    return f"🎬 <b>{escape(str(m.get('title','')))}</b>\n\n" \
           f"🔢 Kod: <code>{escape(str(m.get('code','')))}</code>\n" \
           f"📅 Yil: {m.get('year') or '-'}\n" \
           f"🎭 Janr: {escape(str(m.get('genre','-')))}\n" \
           f"⭐ Reyting: {m.get('rating') or '-'}\n" \
           f"👁 Ko‘rishlar: {m.get('views',0)}\n\n" \
           f"{escape(str(m.get('description','')))}"

async def show_movie(bot, message, m):
    await bot.copy_message(message.chat.id, int(m["channel_id"]), int(m["message_id"]))
    DB.rpc("increment_movie_views", {"movie_id": m["id"]}).execute()
    await message.answer(card(m))

@router.message(CommandStart())
async def start(message: Message):
    await save_user(message)
    if not await allowed(bot, message): return
    await message.answer("🎬 Kino botiga xush kelibsiz.", reply_markup=menu())

@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if admin(message.from_user.id): await message.answer("Admin panel", reply_markup=admin_menu())

@router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery):
    if await subscribed(bot, call.from_user.id):
        await call.message.edit_text("✅ Obuna tasdiqlandi.", reply_markup=menu())
    else:
        await call.answer("Hali obuna bo‘lmagansiz.", show_alert=True)

@router.callback_query(F.data == "help")
async def help_cb(call: CallbackQuery):
    await call.message.answer("Kino kodini yuboring yoki qidirish tugmasidan foydalaning.")
    await call.answer()

@router.callback_query(F.data == "search")
async def search_cb(call: CallbackQuery):
    await call.message.answer("Kino nomi yoki kodini yuboring.")
    await call.answer()

@router.message()
async def search_message(message: Message):
    if message.from_user.id in ADMINS and message.text and message.text.startswith("/"): return
    if not await allowed(bot, message): return
    q = (message.text or "").strip()
    if not q: return
    r = DB.table("movies").select("*").or_(f"code.eq.{q},title.ilike.%{q}%").limit(10).execute()
    if not r.data:
        await message.answer("❌ Kino topilmadi.")
        return
    for m in r.data: await message.answer(card(m), reply_markup=kb([[InlineKeyboardButton(text="▶️ Ko‘rish", callback_data=f"watch:{m['id']}")]]))

@router.callback_query(F.data.startswith("watch:"))
async def watch(call: CallbackQuery):
    if not await subscribed(bot, call.from_user.id):
        await sub_message(bot, call.message); await call.answer(); return
    mid = int(call.data.split(":")[1])
    r = DB.table("movies").select("*").eq("id", mid).single().execute()
    await show_movie(bot, call.message, r.data)
    await call.answer()

@router.callback_query(F.data == "add")
async def add_start(call: CallbackQuery, state: FSMContext):
    if not admin(call.from_user.id): return
    await state.set_state(Add.source)
    await call.message.answer("Kanal postining message_id raqamini yuboring:")
    await call.answer()

@router.message(Add.source)
async def add_source(message: Message, state: FSMContext):
    try: int(message.text)
    except: return await message.answer("Faqat raqam yuboring.")
    await state.update_data(message_id=int(message.text), channel_id=int((await channel_data())[0]))
    await state.set_state(Add.code); await message.answer("Kino kodi:")

@router.message(Add.code)
async def add_code(message: Message, state: FSMContext):
    await state.update_data(code=message.text.strip()); await state.set_state(Add.title); await message.answer("Kino nomi:")

@router.message(Add.title)
async def add_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text); await state.set_state(Add.description); await message.answer("Tavsif:")

@router.message(Add.description)
async def add_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text); await state.set_state(Add.year); await message.answer("Yil:")

@router.message(Add.year)
async def add_year(message: Message, state: FSMContext):
    try: y=int(message.text)
    except: return await message.answer("Yil raqam bo‘lishi kerak.")
    await state.update_data(year=y); await state.set_state(Add.genre); await message.answer("Janr:")

@router.message(Add.genre)
async def add_genre(message: Message, state: FSMContext):
    await state.update_data(genre=message.text); await state.set_state(Add.rating); await message.answer("Reyting:")

@router.message(Add.rating)
async def add_rating(message: Message, state: FSMContext):
    try: rating=float(message.text)
    except: return await message.answer("Reyting raqam bo‘lishi kerak.")
    d=await state.get_data(); d["rating"]=rating; d["views"]=0
    try:
        DB.table("movies").insert(d).execute()
        await message.answer("✅ Kino qo‘shildi.")
    except Exception as e: await message.answer(f"❌ Xato: {e}")
    await state.clear()

@router.callback_query(F.data == "channel")
async def channel_start(call: CallbackQuery, state: FSMContext):
    if not admin(call.from_user.id): return
    await state.set_state(EditChannel.channel_id); await call.message.answer("Yangi kanal ID:")
    await call.answer()

@router.message(EditChannel.channel_id)
async def channel_id(message: Message, state: FSMContext):
    await state.update_data(channel_id=message.text); await state.set_state(EditChannel.username); await message.answer("@username:")

@router.message(EditChannel.username)
async def channel_username(message: Message, state: FSMContext):
    await state.update_data(username=message.text); await state.set_state(EditChannel.link); await message.answer("Kanal havolasi:")

@router.message(EditChannel.link)
async def channel_link(message: Message, state: FSMContext):
    d=await state.get_data(); d["link"]=message.text
    for k,v in d.items(): await set_setting(k,v)
    await state.clear(); await message.answer("✅ Kanal almashtirildi.")

@router.callback_query(F.data == "broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    if not admin(call.from_user.id): return
    await state.set_state(Broadcast.message); await call.message.answer("Reklama xabarini yuboring."); await call.answer()

@router.message(Broadcast.message)
async def broadcast_send(message: Message, state: FSMContext):
    users=DB.table("users").select("telegram_id").eq("is_blocked",False).execute().data
    ok=0
    for u in users:
        try:
            await bot.copy_message(int(u["telegram_id"]), message.chat.id, message.message_id)
            ok+=1; await asyncio.sleep(.1)
        except Exception: pass
    await state.clear(); await message.answer(f"✅ Yuborildi: {ok}")

@router.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if not admin(call.from_user.id): return
    m=DB.table("movies").select("id",count="exact").execute()
    u=DB.table("users").select("id",count="exact").execute()
    await call.message.answer(f"🎬 Kinolar: {m.count or 0}\n👤 Foydalanuvchilar: {u.count or 0}")
    await call.answer()

async def main():
    global bot
    logging.basicConfig(level=logging.INFO)
    bot=Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher(storage=MemoryStorage()); dp.include_router(router)
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
