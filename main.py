import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from supabase import create_client, Client

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

CHANNEL_ID_TEXT = os.getenv("CHANNEL_ID", "").strip()

try:
    CHANNEL_ID = int(CHANNEL_ID_TEXT)
except ValueError:
    CHANNEL_ID = 0

CHANNEL_LINK = os.getenv("CHANNEL_LINK", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL topilmadi")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY topilmadi")

if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS topilmadi")

if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID topilmadi")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# SUPABASE
# =========================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher(storage=MemoryStorage())


# =========================================================
# STATES
# =========================================================

class AddMovie(StatesGroup):
    code = State()
    title = State()
    alternative_title = State()
    description = State()
    year = State()
    genre = State()
    rating = State()
    channel_id = State()
    message_id = State()


class FastAddMovie(StatesGroup):
    code = State()
    title = State()
    description = State()
    year = State()
    genre = State()
    rating = State()


class SearchMovie(StatesGroup):
    query = State()


class EditMovie(StatesGroup):
    code = State()
    field = State()
    value = State()


class Broadcast(StatesGroup):
    text = State()


# =========================================================
# DATABASE HELPERS
# =========================================================

async def db_call(func):
    """
    Supabase Python client synchronous.
    Telegram bot event loop bloklanmasligi uchun
    querylarni alohida thread'da bajaradi.
    """
    return await asyncio.to_thread(func)


async def db_get_movie_by_code(code: str):
    code = code.strip()

    def query():
        response = (
            supabase
            .table("movies")
            .select("*")
            .eq("code", code)
            .limit(1)
            .execute()
        )

        return response.data[0] if response.data else None

    return await db_call(query)


async def db_get_movie(movie_id: int):
    def query():
        response = (
            supabase
            .table("movies")
            .select("*")
            .eq("id", movie_id)
            .limit(1)
            .execute()
        )

        return response.data[0] if response.data else None

    return await db_call(query)


async def db_add_movie(data: dict):
    def query():
        return (
            supabase
            .table("movies")
            .insert(data)
            .execute()
        )

    return await db_call(query)


async def db_update_movie(movie_id: int, data: dict):
    def query():
        return (
            supabase
            .table("movies")
            .update(data)
            .eq("id", movie_id)
            .execute()
        )

    return await db_call(query)


async def db_delete_movie(movie_id: int):
    def query():
        return (
            supabase
            .table("movies")
            .delete()
            .eq("id", movie_id)
            .execute()
        )

    return await db_call(query)


async def db_search_movies(query_text: str):
    query_text = query_text.strip()

    if not query_text:
        return []

    # Code exact
    movie = await db_get_movie_by_code(query_text)

    if movie:
        return [movie]

    def query():
        title_result = (
            supabase
            .table("movies")
            .select("*")
            .ilike("title", f"%{query_text}%")
            .order("views", desc=True)
            .limit(20)
            .execute()
        )

        alt_result = (
            supabase
            .table("movies")
            .select("*")
            .ilike("alternative_title", f"%{query_text}%")
            .order("views", desc=True)
            .limit(20)
            .execute()
        )

        result = []

        seen = set()

        for item in title_result.data or []:
            if item["id"] not in seen:
                seen.add(item["id"])
                result.append(item)

        for item in alt_result.data or []:
            if item["id"] not in seen:
                seen.add(item["id"])
                result.append(item)

        result.sort(
            key=lambda x: int(x.get("views") or 0),
            reverse=True
        )

        return result[:20]

    return await db_call(query)


async def db_get_genres():
    def query():
        response = (
            supabase
            .table("genres")
            .select("*")
            .order("name")
            .execute()
        )

        return response.data or []

    return await db_call(query)


async def db_add_genre(name: str):
    name = name.strip()

    def query():
        return (
            supabase
            .table("genres")
            .upsert(
                {"name": name},
                on_conflict="name"
            )
            .execute()
        )

    return await db_call(query)


async def db_movies_by_genre(genre: str):
    def query():
        response = (
            supabase
            .table("movies")
            .select("*")
            .ilike("genre", genre)
            .order("views", desc=True)
            .limit(20)
            .execute()
        )

        return response.data or []

    return await db_call(query)


async def db_popular_movies():
    def query():
        response = (
            supabase
            .table("movies")
            .select("*")
            .order("views", desc=True)
            .limit(10)
            .execute()
        )

        return response.data or []

    return await db_call(query)


async def db_new_movies():
    def query():
        response = (
            supabase
            .table("movies")
            .select("*")
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )

        return response.data or []

    return await db_call(query)


async def db_increment_views(movie_id: int):
    def query():
        return (
            supabase
            .rpc(
                "increment_movie_views",
                {"p_movie_id": movie_id}
            )
            .execute()
        )

    try:
        await db_call(query)
    except Exception as e:
        logger.error("Views update error: %s", e)


async def db_register_user(
    telegram_id: int,
    username: str | None,
    first_name: str | None
):
    now = datetime.now(timezone.utc).isoformat()

    data = {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_activity": now
    }

    def query():
        return (
            supabase
            .table("users")
            .upsert(
                data,
                on_conflict="telegram_id"
            )
            .execute()
        )

    try:
        await db_call(query)
    except Exception as e:
        logger.error("User register error: %s", e)


async def db_get_user(telegram_id: int):
    def query():
        response = (
            supabase
            .table("users")
            .select("*")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
        )

        return response.data[0] if response.data else None

    return await db_call(query)


async def db_is_favorite(telegram_id: int, movie_id: int):
    def query():
        response = (
            supabase
            .table("favorites")
            .select("id")
            .eq("telegram_id", telegram_id)
            .eq("movie_id", movie_id)
            .limit(1)
            .execute()
        )

        return bool(response.data)

    return await db_call(query)


async def db_add_favorite(telegram_id: int, movie_id: int):
    def query():
        return (
            supabase
            .table("favorites")
            .upsert(
                {
                    "telegram_id": telegram_id,
                    "movie_id": movie_id
                },
                on_conflict="telegram_id,movie_id"
            )
            .execute()
        )

    return await db_call(query)


async def db_remove_favorite(telegram_id: int, movie_id: int):
    def query():
        return (
            supabase
            .table("favorites")
            .delete()
            .eq("telegram_id", telegram_id)
            .eq("movie_id", movie_id)
            .execute()
        )

    return await db_call(query)


async def db_get_favorites(telegram_id: int):
    def get_ids():
        response = (
            supabase
            .table("favorites")
            .select("movie_id")
            .eq("telegram_id", telegram_id)
            .execute()
        )

        return [
            int(x["movie_id"])
            for x in (response.data or [])
        ]

    ids = await db_call(get_ids)

    if not ids:
        return []

    def get_movies():
        response = (
            supabase
            .table("movies")
            .select("*")
            .in_("id", ids)
            .execute()
        )

        movies = response.data or []

        order = {
            movie_id: index
            for index, movie_id in enumerate(ids)
        }

        movies.sort(
            key=lambda x: order.get(int(x["id"]), 999999)
        )

        return movies

    return await db_call(get_movies)


async def db_delete_favorites_for_movie(movie_id: int):
    def query():
        return (
            supabase
            .table("favorites")
            .delete()
            .eq("movie_id", movie_id)
            .execute()
        )

    return await db_call(query)


async def db_get_all_movies(limit=50):
    def query():
        response = (
            supabase
            .table("movies")
            .select("*")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )

        return response.data or []

    return await db_call(query)


async def db_get_all_users_page(offset: int, limit: int = 1000):
    def query():
        response = (
            supabase
            .table("users")
            .select("telegram_id,is_blocked")
            .range(offset, offset + limit - 1)
            .execute()
        )

        return response.data or []

    return await db_call(query)


async def db_count_table(table: str):
    def query():
        response = (
            supabase
            .table(table)
            .select("id", count="exact")
            .execute()
        )

        return int(response.count or 0)

    return await db_call(query)


async def db_count_blocked_users():
    def query():
        response = (
            supabase
            .table("users")
            .select("id", count="exact")
            .eq("is_blocked", True)
            .execute()
        )

        return int(response.count or 0)

    return await db_call(query)


async def db_get_top_movie():
    def query():
        response = (
            supabase
            .table("movies")
            .select("title,code,views")
            .order("views", desc=True)
            .limit(1)
            .execute()
        )

        return response.data[0] if response.data else None

    return await db_call(query)


async def db_get_today_users():
    now = datetime.now(timezone.utc)

    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    end = start + timedelta(days=1)

    def query():
        response = (
            supabase
            .table("users")
            .select("id", count="exact")
            .gte("last_activity", start.isoformat())
            .lt("last_activity", end.isoformat())
            .execute()
        )

        return int(response.count or 0)

    return await db_call(query)


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🎬 Kino qidirish"),
                KeyboardButton(text="🔢 Kod orqali qidirish")
            ],
            [
                KeyboardButton(text="🎭 Janrlar"),
                KeyboardButton(text="🔥 Eng ko‘p ko‘rilgan")
            ],
            [
                KeyboardButton(text="🆕 Yangi kinolar"),
                KeyboardButton(text="❤️ Sevimlilar")
            ],
            [
                KeyboardButton(text="ℹ️ Yordam")
            ]
        ],
        resize_keyboard=True
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="➕ Kino qo‘shish"),
                KeyboardButton(text="⚡ Tez qo‘shish")
            ],
            [
                KeyboardButton(text="📋 Kinolar"),
                KeyboardButton(text="📊 Statistika")
            ],
            [
                KeyboardButton(text="👥 Userlar"),
                KeyboardButton(text="🗑 Kino o‘chirish")
            ],
            [
                KeyboardButton(text="✏️ Kino tahrirlash"),
                KeyboardButton(text="📢 Reklama")
            ],
            [
                KeyboardButton(text="🔐 Obuna"),
                KeyboardButton(text="⚙️ Sozlamalar")
            ],
            [
                KeyboardButton(text="⬅️ Asosiy menyu")
            ]
        ],
        resize_keyboard=True
    )


def movie_keyboard(movie_id: int, is_favorite: bool):
    favorite_text = (
        "💔 Sevimlilardan olib tashlash"
        if is_favorite
        else "❤️ Sevimlilarga qo‘shish"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Kinoni ko‘rish",
                    callback_data=f"watch:{movie_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=favorite_text,
                    callback_data=f"favorite:{movie_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="back_main"
                )
            ]
        ]
    )


def subscription_keyboard():
    url = CHANNEL_LINK

    if not url and CHANNEL_ID:
        channel_text = str(CHANNEL_ID)

        if channel_text.startswith("-100"):
            url = (
                "https://t.me/c/"
                + channel_text[4:]
            )

    buttons = []

    if url:
        buttons.append([
            InlineKeyboardButton(
                text="📢 Kanalga obuna bo‘lish",
                url=url
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="✅ Obunani tekshirish",
            callback_data="check_sub"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def safe_text(value, default="—"):
    if value is None or str(value).strip() == "":
        return default

    return str(value)


def movie_text(movie):
    title = safe_text(movie.get("title"))
    alt = safe_text(movie.get("alternative_title"), "")
    description = safe_text(movie.get("description"), "")
    year = safe_text(movie.get("year"), "")
    genre = safe_text(movie.get("genre"), "")
    rating = safe_text(movie.get("rating"), "")
    views = safe_text(movie.get("views"), "0")
    code = safe_text(movie.get("code"))

    text = f"🎬 <b>{title}</b>\n"

    if alt:
        text += f"🔤 <b>Muqobil:</b> {alt}\n"

    if year:
        text += f"📅 <b>Yil:</b> {year}\n"

    if genre:
        text += f"🎭 <b>Janr:</b> {genre}\n"

    if rating:
        text += f"⭐ <b>Reyting:</b> {rating}\n"

    text += f"👁 <b>Ko‘rishlar:</b> {views}\n"
    text += f"🔢 <b>Kod:</b> <code>{code}</code>\n"

    if description:
        text += f"\n📝 {description}"

    return text


async def send_movie_card(
    message: Message,
    movie: dict
):
    is_fav = await db_is_favorite(
        message.from_user.id,
        int(movie["id"])
    )

    await message.answer(
        movie_text(movie),
        reply_markup=movie_keyboard(
            int(movie["id"]),
            is_fav
        )
    )


async def check_subscription(user_id: int):
    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )

        return member.status in {
            "creator",
            "administrator",
            "member"
        }

    except Exception as e:
        logger.warning(
            "Subscription check error: %s",
            e
        )

        # Agar Telegram tekshira olmasa,
        # bot userni bloklab qo‘ymaydi.
        return True


async def require_subscription(message: Message):
    subscribed = await check_subscription(
        message.from_user.id
    )

    if subscribed:
        return True

    await message.answer(
        "🔒 <b>Botdan foydalanish uchun kanalga obuna bo‘ling.</b>\n\n"
        "Obuna bo‘lgach, «Obunani tekshirish» tugmasini bosing.",
        reply_markup=subscription_keyboard()
    )

    return False


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start_handler(
    message: Message,
    state: FSMContext
):
    await state.clear()

    await db_register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if not await require_subscription(message):
        return

    await message.answer(
        "🎬 <b>Kino botga xush kelibsiz!</b>\n\n"
        "Kerakli bo‘limni tanlang:",
        reply_markup=main_menu()
    )


# =========================================================
# SUBSCRIPTION CALLBACK
# =========================================================

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(
    callback: CallbackQuery
):
    subscribed = await check_subscription(
        callback.from_user.id
    )

    if subscribed:
        await callback.answer(
            "✅ Obuna tasdiqlandi!",
            show_alert=True
        )

        try:
            await callback.message.edit_text(
                "✅ <b>Obuna tasdiqlandi!</b>\n\n"
                "Endi botdan foydalanishingiz mumkin."
            )
        except TelegramBadRequest:
            pass

        await callback.message.answer(
            "🎬 Asosiy menyu:",
            reply_markup=main_menu()
        )

    else:
        await callback.answer(
            "❌ Siz hali kanalga obuna bo‘lmagansiz.",
            show_alert=True
        )


# =========================================================
# SEARCH
# =========================================================

@dp.message(F.text == "🎬 Kino qidirish")
async def search_button(
    message: Message,
    state: FSMContext
):
    if not await require_subscription(message):
        return

    await state.set_state(SearchMovie.query)

    await message.answer(
        "🔎 Kino nomini yozing.\n\n"
        "Masalan:\n"
        "<code>Avatar</code>\n"
        "<code>Interstellar</code>"
    )


@dp.message(SearchMovie.query)
async def search_query(
    message: Message,
    state: FSMContext
):
    query = message.text.strip()

    movies = await db_search_movies(query)

    await state.clear()

    if not movies:
        await message.answer(
            "❌ Kino topilmadi.",
            reply_markup=main_menu()
        )
        return

    if len(movies) == 1:
        await send_movie_card(
            message,
            movies[0]
        )
        return

    text = "🔎 <b>Topilgan kinolar:</b>\n\n"

    buttons = []

    for movie in movies[:20]:
        text += (
            f"🎬 {safe_text(movie.get('title'))} "
            f"— <code>{safe_text(movie.get('code'))}</code>\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=safe_text(movie.get("title"))[:50],
                callback_data=f"movie:{movie['id']}"
            )
        ])

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


# =========================================================
# CODE SEARCH
# =========================================================

@dp.message(F.text == "🔢 Kod orqali qidirish")
async def code_search_button(
    message: Message
):
    if not await require_subscription(message):
        return

    await message.answer(
        "🔢 <b>Kino kodini yuboring.</b>\n\n"
        "Masalan: <code>125</code>"
    )


@dp.message(F.text.regexp(r"^\d+$"))
async def code_search_handler(
    message: Message
):
    if not await require_subscription(message):
        return

    movie = await db_get_movie_by_code(
        message.text.strip()
    )

    if not movie:
        await message.answer(
            "❌ Bu kod bilan kino topilmadi."
        )
        return

    await send_movie_card(
        message,
        movie
    )


# =========================================================
# MOVIE CALLBACK
# =========================================================

@dp.callback_query(F.data.startswith("movie:"))
async def movie_callback(
    callback: CallbackQuery
):
    movie_id = int(
        callback.data.split(":")[1]
    )

    movie = await db_get_movie(movie_id)

    if not movie:
        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True
        )
        return

    is_fav = await db_is_favorite(
        callback.from_user.id,
        movie_id
    )

    await callback.message.edit_text(
        movie_text(movie),
        reply_markup=movie_keyboard(
            movie_id,
            is_fav
        )
    )

    await callback.answer()


# =========================================================
# WATCH MOVIE
# =========================================================

@dp.callback_query(F.data.startswith("watch:"))
async def watch_movie(
    callback: CallbackQuery
):
    movie_id = int(
        callback.data.split(":")[1]
    )

    movie = await db_get_movie(movie_id)

    if not movie:
        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True
        )
        return

    subscribed = await check_subscription(
        callback.from_user.id
    )

    if not subscribed:
        await callback.answer(
            "❌ Avval kanalga obuna bo‘ling.",
            show_alert=True
        )

        try:
            await callback.message.answer(
                "🔒 Kanalga obuna bo‘ling:",
                reply_markup=subscription_keyboard()
            )
        except Exception:
            pass

        return

    try:
        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=int(movie["channel_id"]),
            message_id=int(movie["message_id"])
        )

        await db_increment_views(movie_id)

        await callback.answer(
            "▶️ Kino yuborildi!"
        )

    except Exception as e:
        logger.error(
            "Movie copy error: %s",
            e
        )

        await callback.answer(
            "❌ Kinoni yuborib bo‘lmadi.\n"
            "Kanal yoki message ID noto‘g‘ri bo‘lishi mumkin.",
            show_alert=True
        )


# =========================================================
# FAVORITE
# =========================================================

@dp.callback_query(F.data.startswith("favorite:"))
async def favorite_callback(
    callback: CallbackQuery
):
    movie_id = int(
        callback.data.split(":")[1]
    )

    user_id = callback.from_user.id

    movie = await db_get_movie(movie_id)

    if not movie:
        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True
        )
        return

    favorite = await db_is_favorite(
        user_id,
        movie_id
    )

    if favorite:
        await db_remove_favorite(
            user_id,
            movie_id
        )

        new_state = False

        await callback.answer(
            "💔 Sevimlilardan olib tashlandi."
        )

    else:
        await db_add_favorite(
            user_id,
            movie_id
        )

        new_state = True

        await callback.answer(
            "❤️ Sevimlilarga qo‘shildi."
        )

    try:
        await callback.message.edit_reply_markup(
            reply_markup=movie_keyboard(
                movie_id,
                new_state
            )
        )
    except TelegramBadRequest:
        pass


# =========================================================
# BACK
# =========================================================

@dp.callback_query(F.data == "back_main")
async def back_main(
    callback: CallbackQuery
):
    await callback.answer()

    await callback.message.answer(
        "🎬 Asosiy menyu:",
        reply_markup=main_menu()
    )


# =========================================================
# GENRES
# =========================================================

@dp.message(F.text == "🎭 Janrlar")
async def genres_handler(
    message: Message
):
    if not await require_subscription(message):
        return

    genres = await db_get_genres()

    if not genres:
        await message.answer(
            "❌ Janrlar mavjud emas."
        )
        return

    buttons = []

    for genre in genres:
        buttons.append([
            InlineKeyboardButton(
                text=f"🎭 {genre['name']}",
                callback_data=f"genre:{genre['name']}"
            )
        ])

    await message.answer(
        "🎭 <b>Janrni tanlang:</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@dp.callback_query(F.data.startswith("genre:"))
async def genre_callback(
    callback: CallbackQuery
):
    genre = callback.data.split(":", 1)[1]

    movies = await db_movies_by_genre(genre)

    if not movies:
        await callback.answer(
            "❌ Bu janrda kino yo‘q.",
            show_alert=True
        )
        return

    text = f"🎭 <b>{genre}</b>\n\n"

    buttons = []

    for movie in movies:
        text += (
            f"🎬 {safe_text(movie.get('title'))} "
            f"— <code>{safe_text(movie.get('code'))}</code>\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=safe_text(movie.get("title"))[:50],
                callback_data=f"movie:{movie['id']}"
            )
        ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


# =========================================================
# POPULAR
# =========================================================

@dp.message(F.text == "🔥 Eng ko‘p ko‘rilgan")
async def popular_handler(
    message: Message
):
    if not await require_subscription(message):
        return

    movies = await db_popular_movies()

    if not movies:
        await message.answer(
            "❌ Hozircha kinolar yo‘q."
        )
        return

    text = "🔥 <b>Eng ko‘p ko‘rilgan kinolar:</b>\n\n"

    buttons = []

    for index, movie in enumerate(movies, 1):
        text += (
            f"{index}. {safe_text(movie.get('title'))} "
            f"— 👁 {safe_text(movie.get('views'), '0')}\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"{index}. {safe_text(movie.get('title'))[:45]}",
                callback_data=f"movie:{movie['id']}"
            )
        ])

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


# =========================================================
# NEW MOVIES
# =========================================================

@dp.message(F.text == "🆕 Yangi kinolar")
async def new_movies_handler(
    message: Message
):
    if not await require_subscription(message):
        return

    movies = await db_new_movies()

    if not movies:
        await message.answer(
            "❌ Hozircha kinolar yo‘q."
        )
        return

    text = "🆕 <b>Yangi kinolar:</b>\n\n"

    buttons = []

    for movie in movies:
        text += (
            f"🎬 {safe_text(movie.get('title'))}\n"
            f"🔢 <code>{safe_text(movie.get('code'))}</code>\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=safe_text(movie.get("title"))[:50],
                callback_data=f"movie:{movie['id']}"
            )
        ])

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


# =========================================================
# FAVORITES
# =========================================================

@dp.message(F.text == "❤️ Sevimlilar")
async def favorites_handler(
    message: Message
):
    if not await require_subscription(message):
        return

    movies = await db_get_favorites(
        message.from_user.id
    )

    if not movies:
        await message.answer(
            "❤️ <b>Sevimlilar ro‘yxati bo‘sh.</b>"
        )
        return

    text = "❤️ <b>Sevimli kinolaringiz:</b>\n\n"

    buttons = []

    for movie in movies:
        text += (
            f"🎬 {safe_text(movie.get('title'))}\n"
            f"🔢 <code>{safe_text(movie.get('code'))}</code>\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=safe_text(movie.get("title"))[:50],
                callback_data=f"movie:{movie['id']}"
            )
        ])

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


# =========================================================
# HELP
# =========================================================

@dp.message(F.text == "ℹ️ Yordam")
async def help_handler(
    message: Message
):
    if not await require_subscription(message):
        return

    await message.answer(
        "ℹ️ <b>Botdan foydalanish</b>\n\n"
        "🎬 <b>Kino qidirish</b> — nomi orqali qidirish.\n"
        "🔢 <b>Kod orqali qidirish</b> — kino kodini yuborish.\n"
        "🎭 <b>Janrlar</b> — janr bo‘yicha qidirish.\n"
        "🔥 <b>Eng ko‘p ko‘rilgan</b> — mashhur kinolar.\n"
        "🆕 <b>Yangi kinolar</b> — oxirgi qo‘shilganlar.\n"
        "❤️ <b>Sevimlilar</b> — saqlangan kinolar.\n\n"
        f"👨‍💻 Yordam: {SUPPORT_USERNAME}",
        reply_markup=main_menu()
    )


# =========================================================
# ADMIN CHECK
# =========================================================

async def admin_only(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ Sizda admin huquqi yo‘q."
        )
        return False

    return True


# =========================================================
# ADMIN MENU
# =========================================================

@dp.message(Command("admin"))
async def admin_command(
    message: Message,
    state: FSMContext
):
    if not await admin_only(message):
        return

    await state.clear()

    await message.answer(
        "⚙️ <b>Admin panel</b>",
        reply_markup=admin_menu()
    )


@dp.message(F.text == "⬅️ Asosiy menyu")
async def back_to_main_menu(
    message: Message,
    state: FSMContext
):
    await state.clear()

    await message.answer(
        "🎬 Asosiy menyu:",
        reply_markup=main_menu()
    )


# =========================================================
# ADMIN ADD MOVIE
# =========================================================

@dp.message(F.text == "➕ Kino qo‘shish")
async def admin_add_start(
    message: Message,
    state: FSMContext
):
    if not await admin_only(message):
        return

    await state.set_state(AddMovie.code)

    await message.answer(
        "➕ <b>Kino qo‘shish</b>\n\n"
        "1️⃣ Kino kodini yuboring:"
    )


@dp.message(AddMovie.code)
async def add_movie_code(
    message: Message,
    state: FSMContext
):
    code = message.text.strip()

    existing = await db_get_movie_by_code(code)

    if existing:
        await message.answer(
            "❌ Bu kod allaqachon mavjud.\n"
            "Boshqa kod yuboring."
        )
        return

    await state.update_data(code=code)
    await state.set_state(AddMovie.title)

    await message.answer(
        "2️⃣ Kino nomini yuboring:"
    )


@dp.message(AddMovie.title)
async def add_movie_title(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        title=message.text.strip()
    )

    await state.set_state(
        AddMovie.alternative_title
    )

    await message.answer(
        "3️⃣ Muqobil nomini yuboring.\n"
        "Agar yo‘q bo‘lsa: <code>-</code>"
    )


@dp.message(AddMovie.alternative_title)
async def add_movie_alt(
    message: Message,
    state: FSMContext
):
    value = message.text.strip()

    if value == "-":
        value = None

    await state.update_data(
        alternative_title=value
    )

    await state.set_state(
        AddMovie.description
    )

    await message.answer(
        "4️⃣ Kino tavsifini yuboring.\n"
        "Agar kerak bo‘lmasa: <code>-</code>"
    )


@dp.message(AddMovie.description)
async def add_movie_description(
    message: Message,
    state: FSMContext
):
    value = message.text.strip()

    if value == "-":
        value = None

    await state.update_data(
        description=value
    )

    await state.set_state(
        AddMovie.year
    )

    await message.answer(
        "5️⃣ Kino yilini yuboring.\n"
        "Masalan: <code>2025</code>\n"
        "Agar noma’lum bo‘lsa: <code>0</code>"
    )


@dp.message(AddMovie.year)
async def add_movie_year(
    message: Message,
    state: FSMContext
):
    try:
        year = int(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ Yil faqat raqam bo‘lishi kerak."
        )
        return

    if year == 0:
        year = None

    await state.update_data(year=year)

    await state.set_state(
        AddMovie.genre
    )

    genres = await db_get_genres()

    buttons = []

    for genre in genres:
        buttons.append([
            InlineKeyboardButton(
                text=genre["name"],
                callback_data=f"addgenre:{genre['name']}"
            )
        ])

    await message.answer(
        "6️⃣ Janrni tanlang yoki o‘zingiz yozing:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@dp.callback_query(
    AddMovie.genre,
    F.data.startswith("addgenre:")
)
async def add_movie_genre_callback(
    callback: CallbackQuery,
    state: FSMContext
):
    genre = callback.data.split(
        ":",
        1
    )[1]

    await state.update_data(
        genre=genre
    )

    await state.set_state(
        AddMovie.rating
    )

    await callback.message.answer(
        "7️⃣ Reytingni yuboring.\n"
        "Masalan: <code>8.5</code>\n"
        "Agar yo‘q bo‘lsa: <code>0</code>"
    )

    await callback.answer()


@dp.message(AddMovie.genre)
async def add_movie_genre_text(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        genre=message.text.strip()
    )

    await state.set_state(
        AddMovie.rating
    )

    await message.answer(
        "7️⃣ Reytingni yuboring.\n"
        "Masalan: <code>8.5</code>\n"
        "Agar yo‘q bo‘lsa: <code>0</code>"
    )


@dp.message(AddMovie.rating)
async def add_movie_rating(
    message: Message,
    state: FSMContext
):
    try:
        rating = float(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ Reyting raqam bo‘lishi kerak."
        )
        return

    if rating == 0:
        rating = None

    await state.update_data(
        rating=rating
    )

    await state.set_state(
        AddMovie.channel_id
    )

    await message.answer(
        "8️⃣ Kino joylashgan Telegram kanal ID'sini yuboring.\n\n"
        "Masalan:\n"
        "<code>-1001234567890</code>\n\n"
        "Agar hozirgi asosiy kanal bo‘lsa:\n"
        "<code>default</code>"
    )


@dp.message(AddMovie.channel_id)
async def add_movie_channel(
    message: Message,
    state: FSMContext
):
    value = message.text.strip()

    if value.lower() == "default":
        channel_id = CHANNEL_ID
    else:
        try:
            channel_id = int(value)
        except ValueError:
            await message.answer(
                "❌ Kanal ID raqam bo‘lishi kerak."
            )
            return

    await state.update_data(
        channel_id=channel_id
    )

    await state.set_state(
        AddMovie.message_id
    )

    await message.answer(
        "9️⃣ Telegram kanalidagi kino message ID'sini yuboring.\n\n"
        "Masalan: <code>4821</code>"
    )


@dp.message(AddMovie.message_id)
async def add_movie_message_id(
    message: Message,
    state: FSMContext
):
    try:
        message_id = int(
            message.text.strip()
        )
    except ValueError:
        await message.answer(
            "❌ Message ID raqam bo‘lishi kerak."
        )
        return

    data = await state.get_data()

    movie_data = {
        "code": data["code"],
        "title": data["title"],
        "alternative_title": data.get(
            "alternative_title"
        ),
        "description": data.get(
            "description"
        ),
        "year": data.get("year"),
        "genre": data.get("genre"),
        "rating": data.get("rating"),
        "channel_id": data["channel_id"],
        "message_id": message_id,
        "views": 0
    }

    try:
        await db_add_movie(movie_data)

        if data.get("genre"):
            await db_add_genre(
                data["genre"]
            )

        await state.clear()

        await message.answer(
            "✅ <b>Kino muvaffaqiyatli qo‘shildi!</b>\n\n"
            f"🎬 {movie_data['title']}\n"
            f"🔢 Kod: <code>{movie_data['code']}</code>\n"
            f"📢 Channel ID: <code>{movie_data['channel_id']}</code>\n"
            f"💬 Message ID: <code>{movie_data['message_id']}</code>",
            reply_markup=admin_menu()
        )

    except Exception as e:
        logger.error(
            "Add movie error: %s",
            e
        )

        await message.answer(
            "❌ Kino qo‘shishda xatolik.\n\n"
            f"<code>{str(e)[:1000]}</code>"
        )


# =========================================================
# FAST ADD
# =========================================================

@dp.message(F.text == "⚡ Tez qo‘shish")
async def fast_add_start(
    message: Message,
    state: FSMContext
):
    if not await admin_only(message):
        return

    if not message.reply_to_message:
        await message.answer(
            "⚡ <b>Tez qo‘shish</b>\n\n"
            "Avval kanalga yuborilgan kino xabariga "
            "Reply qilib, keyin shu botga yuboring.\n\n"
            "Bot reply qilingan xabarning "
            "channel ID va message ID'sini avtomatik oladi."
        )
        return

    reply = message.reply_to_message

    await state.update_data(
        channel_id=reply.chat.id,
        message_id=reply.message_id
    )

    await state.set_state(
        FastAddMovie.code
    )

    await message.answer(
        "1️⃣ Kino kodini yuboring:"
    )


@dp.message(FastAddMovie.code)
async def fast_add_code(
    message: Message,
    state: FSMContext
):
    code = message.text.strip()

    existing = await db_get_movie_by_code(code)

    if existing:
        await message.answer(
            "❌ Bu kod allaqachon mavjud."
        )
        return

    await state.update_data(
        code=code
    )

    await state.set_state(
        FastAddMovie.title
    )

    await message.answer(
        "2️⃣ Kino nomi:"
    )


@dp.message(FastAddMovie.title)
async def fast_add_title(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        title=message.text.strip()
    )

    await state.set_state(
        FastAddMovie.description
    )

    await message.answer(
        "3️⃣ Tavsif.\n"
        "Kerak bo‘lmasa: <code>-</code>"
    )


@dp.message(FastAddMovie.description)
async def fast_add_description(
    message: Message,
    state: FSMContext
):
    value = message.text.strip()

    if value == "-":
        value = None

    await state.update_data(
        description=value
    )

    await state.set_state(
        FastAddMovie.year
    )

    await message.answer(
        "4️⃣ Yil:\n"
        "Masalan: <code>2025</code>"
    )


@dp.message(FastAddMovie.year)
async def fast_add_year(
    message: Message,
    state: FSMContext
):
    try:
        year = int(
            message.text.strip()
        )
    except ValueError:
        await message.answer(
            "❌ Yil raqam bo‘lishi kerak."
        )
        return

    await state.update_data(
        year=year
    )

    await state.set_state(
        FastAddMovie.genre
    )

    genres = await db_get_genres()

    buttons = []

    for genre in genres:
        buttons.append([
            InlineKeyboardButton(
                text=genre["name"],
                callback_data=f"fastgenre:{genre['name']}"
            )
        ])

    await message.answer(
        "5️⃣ Janrni tanlang yoki yozing:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@dp.callback_query(
    FastAddMovie.genre,
    F.data.startswith("fastgenre:")
)
async def fast_genre_callback(
    callback: CallbackQuery,
    state: FSMContext
):
    genre = callback.data.split(
        ":",
        1
    )[1]

    await state.update_data(
        genre=genre
    )

    await state.set_state(
        FastAddMovie.rating
    )

    await callback.message.answer(
        "6️⃣ Reyting:\n"
        "Masalan: <code>8.5</code>"
    )

    await callback.answer()


@dp.message(FastAddMovie.genre)
async def fast_genre_text(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        genre=message.text.strip()
    )

    await state.set_state(
        FastAddMovie.rating
    )

    await message.answer(
        "6️⃣ Reyting:\n"
        "Masalan: <code>8.5</code>"
    )


@dp.message(FastAddMovie.rating)
async def fast_add_rating(
    message: Message,
    state: FSMContext
):
    try:
        rating = float(
            message.text.strip()
        )
    except ValueError:
        await message.answer(
            "❌ Reyting raqam bo‘lishi kerak."
        )
        return

    data = await state.get_data()

    movie_data = {
        "code": data["code"],
        "title": data["title"],
        "alternative_title": None,
        "description": data.get(
            "description"
        ),
        "year": data.get("year"),
        "genre": data.get("genre"),
        "rating": rating,
        "channel_id": data["channel_id"],
        "message_id": data["message_id"],
        "views": 0
    }

    try:
        await db_add_movie(movie_data)

        if movie_data.get("genre"):
            await db_add_genre(
                movie_data["genre"]
            )

        await state.clear()

        await message.answer(
            "✅ <b>Kino tez qo‘shildi!</b>\n\n"
            f"🎬 {movie_data['title']}\n"
            f"🔢 <code>{movie_data['code']}</code>\n"
            f"📢 <code>{movie_data['channel_id']}</code>\n"
            f"💬 <code>{movie_data['message_id']}</code>",
            reply_markup=admin_menu()
        )

    except Exception as e:
        logger.error(
            "Fast add error: %s",
            e
        )

        await message.answer(
            "❌ Kino qo‘shilmadi.\n\n"
            f"<code>{str(e)[:1000]}</code>"
        )


# =========================================================
# ADMIN MOVIE LIST
# =========================================================

@dp.message(F.text == "📋 Kinolar")
async def admin_movies(
    message: Message
):
    if not await admin_only(message):
        return

    movies = await db_get_all_movies(50)

    if not movies:
        await message.answer(
            "📭 Kinolar bazasi bo‘sh."
        )
        return

    text = "📋 <b>Oxirgi kinolar:</b>\n\n"

    for movie in movies:
        text += (
            f"🆔 {movie['id']} | "
            f"<code>{safe_text(movie.get('code'))}</code>\n"
            f"🎬 {safe_text(movie.get('title'))}\n"
            f"👁 {safe_text(movie.get('views'), '0')}\n"
            f"📢 {safe_text(movie.get('channel_id'))}\n"
            f"💬 {safe_text(movie.get('message_id'))}\n\n"
        )

    await message.answer(
        text[:4000]
    )


# =========================================================
# ADMIN DELETE
# =========================================================

@dp.message(F.text == "🗑 Kino o‘chirish")
async def admin_delete_start(
    message: Message,
    state: FSMContext
):
    if not await admin_only(message):
        return

    await state.set_state(
        EditMovie.code
    )

    await message.answer(
        "🗑 O‘chirish uchun kino kodini yuboring:"
    )

    await state.update_data(
        action="delete"
    )


@dp.message(EditMovie.code)
async def edit_or_delete_code(
    message: Message,
    state: FSMContext
):
    data = await state.get_data()

    movie = await db_get_movie_by_code(
        message.text.strip()
    )

    if not movie:
        await message.answer(
            "❌ Kino topilmadi."
        )
        await state.clear()
        return

    if data.get("action") == "delete":
        await db_delete_favorites_for_movie(
            int(movie["id"])
        )

        await db_delete_movie(
            int(movie["id"])
        )

        await state.clear()

        await message.answer(
            "✅ Kino o‘chirildi.",
            reply_markup=admin_menu()
        )

        return

    await state.update_data(
        movie_id=int(movie["id"])
    )

    await state.set_state(
        EditMovie.field
    )

    await message.answer(
        "✏️ Qaysi maydonni o‘zgartirmoqchisiz?\n\n"
        "title\n"
        "alternative_title\n"
        "description\n"
        "year\n"
        "genre\n"
        "rating\n"
        "channel_id\n"
        "message_id\n"
        "code"
    )


# =========================================================
# ADMIN EDIT START
# =========================================================

@dp.message(F.text == "✏️ Kino tahrirlash")
async def admin_edit_start(
    message: Message,
    state: FSMContext
):
    if not await admin_only(message):
        return

    await state.update_data(
        action="edit"
    )

    await state.set_state(
        EditMovie.code
    )

    await message.answer(
        "✏️ Tahrirlash uchun kino kodini yuboring:"
    )


@dp.message(EditMovie.field)
async def edit_field(
    message: Message,
    state: FSMContext
):
    field = message.text.strip()

    allowed = {
        "title",
        "alternative_title",
        "description",
        "year",
        "genre",
        "rating",
        "channel_id",
        "message_id",
        "code"
    }

    if field not in allowed:
        await message.answer(
            "❌ Noto‘g‘ri maydon.\n\n"
            "Quyidagilardan birini yozing:\n"
            + "\n".join(sorted(allowed))
        )
        return

    await state.update_data(
        field=field
    )

    await state.set_state(
        EditMovie.value
    )

    await message.answer(
        f"✏️ <b>{field}</b> uchun yangi qiymatni yuboring:"
    )


@dp.message(EditMovie.value)
async def edit_value(
    message: Message,
    state: FSMContext
):
    data = await state.get_data()

    movie_id = int(data["movie_id"])
    field = data["field"]

    value = message.text.strip()

    if field in {
        "year",
        "channel_id",
        "message_id"
    }:
        try:
            value = int(value)
        except ValueError:
            await message.answer(
                "❌ Bu maydon raqam bo‘lishi kerak."
            )
            return

    elif field == "rating":
        try:
            value = float(value)
        except ValueError:
            await message.answer(
                "❌ Reyting raqam bo‘lishi kerak."
            )
            return

    elif field in {
        "alternative_title",
        "description"
    }:
        if value == "-":
            value = None

    if field == "code":
        existing = await db_get_movie_by_code(value)

        if existing and int(existing["id"]) != movie_id:
            await message.answer(
                "❌ Bu kod boshqa kinoga tegishli."
            )
            return

    try:
        await db_update_movie(
            movie_id,
            {field: value}
        )

        if field == "genre" and value:
            await db_add_genre(value)

        await state.clear()

        await message.answer(
            "✅ Kino ma’lumoti yangilandi.",
            reply_markup=admin_menu()
        )

    except Exception as e:
        logger.error(
            "Edit movie error: %s",
            e
        )

        await message.answer(
            "❌ Yangilashda xatolik.\n\n"
            f"<code>{str(e)[:1000]}</code>"
        )


# =========================================================
# STATISTICS
# =========================================================

@dp.message(F.text == "📊 Statistika")
async def admin_stats(
    message: Message
):
    if not await admin_only(message):
        return

    try:
        movie_count = await db_count_table(
            "movies"
        )

        user_count = await db_count_table(
            "users"
        )

        favorite_count = await db_count_table(
            "favorites"
        )

        blocked_count = await db_count_blocked_users()

        today_users = await db_get_today_users()

        top_movie = await db_get_top_movie()

        text = (
            "📊 <b>BOT STATISTIKASI</b>\n\n"
            f"🎬 Kinolar: <b>{movie_count}</b>\n"
            f"👥 Userlar: <b>{user_count}</b>\n"
            f"❤️ Sevimlilar: <b>{favorite_count}</b>\n"
            f"🚫 Bloklaganlar: <b>{blocked_count}</b>\n"
            f"🟢 Bugungi faollar: <b>{today_users}</b>\n"
        )

        if top_movie:
            text += (
                "\n🔥 <b>Eng ko‘p ko‘rilgan:</b>\n"
                f"🎬 {safe_text(top_movie.get('title'))}\n"
                f"🔢 {safe_text(top_movie.get('code'))}\n"
                f"👁 {safe_text(top_movie.get('views'), '0')}\n"
            )

        await message.answer(text)

    except Exception as e:
        logger.error(
            "Stats error: %s",
            e
        )

        await message.answer(
            f"❌ Statistika xatosi:\n<code>{str(e)[:1000]}</code>"
        )


# =========================================================
# USERS
# =========================================================

@dp.message(F.text == "👥 Userlar")
async def admin_users(
    message: Message
):
    if not await admin_only(message):
        return

    count = await db_count_table(
        "users"
    )

    blocked = await db_count_blocked_users()

    await message.answer(
        "👥 <b>Foydalanuvchilar</b>\n\n"
        f"👤 Jami: <b>{count}</b>\n"
        f"🚫 Bloklagan: <b>{blocked}</b>\n"
        f"🟢 Faol: <b>{count - blocked}</b>"
    )


# =========================================================
# BROADCAST
# =========================================================

@dp.message(F.text == "📢 Reklama")
async def broadcast_start(
    message: Message,
    state: FSMContext
):
    if not await admin_only(message):
        return

    await state.set_state(
        Broadcast.text
    )

    await message.answer(
        "📢 Yuboriladigan xabarni yozing.\n\n"
        "Bot barcha bloklamagan userlarga yuboradi."
    )


@dp.message(Broadcast.text)
async def broadcast_send(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    await state.clear()

    await message.answer(
        "⏳ Broadcast boshlandi..."
    )

    sent = 0
    failed = 0

    offset = 0
    page_size = 1000

    while True:
        users = await db_get_all_users_page(
            offset,
            page_size
        )

        if not users:
            break

        for user in users:
            telegram_id = int(
                user["telegram_id"]
            )

            if user.get("is_blocked"):
                continue

            try:
                await bot.copy_message(
                    chat_id=telegram_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )

                sent += 1

                await asyncio.sleep(
                    0.05
                )

            except TelegramForbiddenError:
                failed += 1

                try:
                    await db_update_user_blocked(
                        telegram_id,
                        True
                    )
                except Exception:
                    pass

            except Exception:
                failed += 1

        if len(users) < page_size:
            break

        offset += page_size

    await message.answer(
        "📢 <b>Broadcast tugadi.</b>\n\n"
        f"✅ Yuborildi: <b>{sent}</b>\n"
        f"❌ Xatolik: <b>{failed}</b>",
        reply_markup=admin_menu()
    )


async def db_update_user_blocked(
    telegram_id: int,
    blocked: bool
):
    def query():
        return (
            supabase
            .table("users")
            .update({
                "is_blocked": blocked
            })
            .eq("telegram_id", telegram_id)
            .execute()
        )

    return await db_call(query)


# =========================================================
# SUBSCRIPTION ADMIN
# =========================================================

@dp.message(F.text == "🔐 Obuna")
async def admin_subscription(
    message: Message
):
    if not await admin_only(message):
        return

    await message.answer(
        "🔐 <b>Obuna sozlamalari</b>\n\n"
        f"📢 Channel ID:\n"
        f"<code>{CHANNEL_ID}</code>\n\n"
        f"🔗 Channel link:\n"
        f"{safe_text(CHANNEL_LINK, 'Belgilanmagan')}"
    )


# =========================================================
# SETTINGS ADMIN
# =========================================================

@dp.message(F.text == "⚙️ Sozlamalar")
async def admin_settings(
    message: Message
):
    if not await admin_only(message):
        return

    await message.answer(
        "⚙️ <b>Bot sozlamalari</b>\n\n"
        f"🤖 Bot token: <code>configured</code>\n"
        f"🗄 Supabase: <code>connected</code>\n"
        f"📢 Channel ID: <code>{CHANNEL_ID}</code>\n"
        f"👨‍💻 Support: {SUPPORT_USERNAME}\n\n"
        "Database: <b>Supabase PostgreSQL</b>\n"
        "SQLite ishlatilmaydi."
    )


# =========================================================
# UNKNOWN TEXT / FALLBACK SEARCH
# =========================================================

@dp.message(F.text)
async def fallback_text(
    message: Message
):
    # Admin menu commands
    if message.text.startswith("/"):
        return

    # Menu tugmalari
    known_buttons = {
        "🎬 Kino qidirish",
        "🔢 Kod orqali qidirish",
        "🎭 Janrlar",
        "🔥 Eng ko‘p ko‘rilgan",
        "🆕 Yangi kinolar",
        "❤️ Sevimlilar",
        "ℹ️ Yordam",
        "➕ Kino qo‘shish",
        "⚡ Tez qo‘shish",
        "📋 Kinolar",
        "📊 Statistika",
        "👥 Userlar",
        "🗑 Kino o‘chirish",
        "✏️ Kino tahrirlash",
        "📢 Reklama",
        "🔐 Obuna",
        "⚙️ Sozlamalar",
        "⬅️ Asosiy menyu"
    }

    if message.text in known_buttons:
        return

    if not await require_subscription(message):
        return

    query = message.text.strip()

    if not query:
        return

    movies = await db_search_movies(query)

    if not movies:
        await message.answer(
            "❌ Kino topilmadi.\n\n"
            "Kino nomi yoki kodini tekshirib ko‘ring."
        )
        return

    if len(movies) == 1:
        await send_movie_card(
            message,
            movies[0]
        )
        return

    text = "🔎 <b>Natijalar:</b>\n\n"

    buttons = []

    for movie in movies[:20]:
        text += (
            f"🎬 {safe_text(movie.get('title'))}\n"
            f"🔢 <code>{safe_text(movie.get('code'))}</code>\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=safe_text(movie.get("title"))[:50],
                callback_data=f"movie:{movie['id']}"
            )
        ])

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


# =========================================================
# ERROR HANDLER
# =========================================================

@dp.errors()
async def global_error_handler(
    event
):
    logger.exception(
        "Unhandled bot error: %s",
        event.exception
    )


# =========================================================
# STARTUP
# =========================================================

async def main():
    logger.info("Starting bot...")
    logger.info(
        "Supabase: %s",
        SUPABASE_URL
    )

    # Test Supabase connection
    try:
        await db_get_genres()
        logger.info(
            "Supabase connection: OK"
        )
    except Exception as e:
        logger.error(
            "Supabase connection failed: %s",
            e
        )
        raise

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    logger.info(
        "Bot started successfully."
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(
            "Bot stopped."
        )
