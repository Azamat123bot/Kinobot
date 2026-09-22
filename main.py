import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List

from dotenv import load_dotenv
from supabase import create_client, Client

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest


# =========================================================
# CONFIGURATION (.env)
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@username").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# SUPABASE
# =========================================================

supabase: Optional[Client] = None


def get_supabase() -> Client:
    if supabase is None:
        raise RuntimeError("Supabase hali ishga tushirilmagan.")
    return supabase


# =========================================================
# FSM STATES
# =========================================================

class AddMovie(StatesGroup):
    code = State()
    title = State()
    alt_title = State()
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


class SearchCode(StatesGroup):
    waiting = State()


class SearchTitle(StatesGroup):
    waiting = State()


class AdminDelete(StatesGroup):
    confirm = State()


class Broadcast(StatesGroup):
    waiting = State()


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🎬 Kino qidirish"),
                KeyboardButton(text="🔢 Kod orqali qidirish"),
            ],
            [
                KeyboardButton(text="🎭 Janrlar"),
                KeyboardButton(text="🔥 Eng ko‘p ko‘rilgan"),
            ],
            [
                KeyboardButton(text="🆕 Yangi kinolar"),
                KeyboardButton(text="❤️ Sevimlilar"),
            ],
            [
                KeyboardButton(text="ℹ️ Yordam"),
            ],
        ],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Kino qo‘shish", callback_data="admin_add")],
            [InlineKeyboardButton(text="✏️ Kino tahrirlash", callback_data="admin_edit")],
            [InlineKeyboardButton(text="🗑 Kino o‘chirish", callback_data="admin_delete")],
            [InlineKeyboardButton(text="📋 Kinolar", callback_data="admin_list")],
            [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
            [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users")],
            [InlineKeyboardButton(text="📢 Reklama", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="📢 Majburiy obuna", callback_data="admin_force_sub")],
            [InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="admin_settings")],
        ]
    )


def movie_actions_kb(movie_id: int, is_fav: bool = False) -> InlineKeyboardMarkup:
    fav_button = InlineKeyboardButton(
        text="💔 Sevimlilardan olib tashlash" if is_fav else "❤️ Sevimlilarga qo‘shish",
        callback_data=f"unfav_{movie_id}" if is_fav else f"fav_{movie_id}",
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Kinoni ko‘rish",
                    callback_data=f"watch_{movie_id}",
                )
            ],
            [fav_button],
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="back_main",
                )
            ],
        ]
    )


def force_sub_kb() -> InlineKeyboardMarkup:
    channel = str(CHANNEL_ID)

    if channel.startswith("-100"):
        channel_link = f"https://t.me/c/{channel[4:]}"
    else:
        channel_link = "https://t.me/"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Kanalga obuna bo‘lish",
                    url=channel_link,
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Tekshirish",
                    callback_data="check_sub",
                )
            ],
        ]
    )


def confirm_delete_kb(movie_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha",
                    callback_data=f"del_yes_{movie_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Yo‘q",
                    callback_data="del_no",
                ),
            ]
        ]
    )


def genres_kb(genres: List[str]) -> InlineKeyboardMarkup:
    buttons = []
    row = []

    for genre in genres:
        row.append(
            InlineKeyboardButton(
                text=genre,
                callback_data=f"genre_{genre}",
            )
        )

        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="back_main",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def register_user(message: Message):
    user = message.from_user
    sb = get_supabase()

    data = {
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_activity": now_iso(),
        "is_blocked": False,
    }

    try:
        result = (
            sb.table("users")
            .select("id")
            .eq("telegram_id", user.id)
            .limit(1)
            .execute()
        )

        if result.data:
            (
                sb.table("users")
                .update(
                    {
                        "username": user.username,
                        "first_name": user.first_name,
                        "last_activity": now_iso(),
                        "is_blocked": False,
                    }
                )
                .eq("telegram_id", user.id)
                .execute()
            )
        else:
            sb.table("users").insert(data).execute()

    except Exception as e:
        logger.error(f"register_user error: {e}")


async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)

        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.RESTRICTED,
        )

    except Exception as e:
        logger.warning(f"Subscription check failed for {user_id}: {e}")

        # Bot kanalni tekshira olmasa foydalanuvchini bloklab qo‘ymaslik.
        return True


async def get_movie_by_code(code: str) -> Optional[Dict]:
    try:
        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .eq("code", code)
            .limit(1)
            .execute()
        )

        return result.data[0] if result.data else None

    except Exception as e:
        logger.error(f"get_movie_by_code error: {e}")
        return None


async def get_movie_by_id(movie_id: int) -> Optional[Dict]:
    try:
        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .eq("id", movie_id)
            .limit(1)
            .execute()
        )

        return result.data[0] if result.data else None

    except Exception as e:
        logger.error(f"get_movie_by_id error: {e}")
        return None


async def increment_views(movie_id: int):
    try:
        # Supabase'dagi RPC funksiyasi:
        # increment_movie_views(movie_id bigint/integer)
        get_supabase().rpc(
            "increment_movie_views",
            {"movie_id": movie_id},
        ).execute()

    except Exception as e:
        logger.error(f"increment_views error: {e}")


async def is_favorite(telegram_id: int, movie_id: int) -> bool:
    try:
        result = (
            get_supabase()
            .table("favorites")
            .select("id")
            .eq("telegram_id", telegram_id)
            .eq("movie_id", movie_id)
            .limit(1)
            .execute()
        )

        return bool(result.data)

    except Exception as e:
        logger.error(f"is_favorite error: {e}")
        return False


def format_movie_card(movie: Dict) -> str:
    title = movie.get("title") or "Noma'lum"
    alt = movie.get("alternative_title")
    year = movie.get("year") or "—"
    genre = movie.get("genre") or "—"
    rating = movie.get("rating")
    views = movie.get("views") or 0
    desc = movie.get("description") or ""

    rating_text = rating if rating is not None else "—"

    text = f"🎬 <b>{title}</b>"

    if alt:
        text += f"\n📌 {alt}"

    text += (
        f"\n\n📅 {year}"
        f"\n🎭 {genre}"
        f"\n⭐ {rating_text}"
        f"\n👀 {views:,}"
    )

    if desc:
        text += f"\n\n📝 {desc}"

    return text


async def ensure_default_genres():
    """Birinchi botdagi default janrlarni Supabase genres jadvaliga qo‘shadi."""
    default_genres = [
        "Action",
        "Comedy",
        "Horror",
        "Romance",
        "Sci-Fi",
        "Fantasy",
        "Drama",
        "Thriller",
        "Family",
        "Animation",
    ]

    sb = get_supabase()

    for genre in default_genres:
        try:
            existing = (
                sb.table("genres")
                .select("id")
                .eq("name", genre)
                .limit(1)
                .execute()
            )

            if not existing.data:
                sb.table("genres").insert({"name": genre}).execute()

        except Exception as e:
            logger.warning(f"Default genre '{genre}' error: {e}")


# =========================================================
# ROUTER
# =========================================================

router = Router()


# =========================================================
# START / HELP
# =========================================================

@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await register_user(message)

    if not await check_subscription(bot, message.from_user.id):
        await message.answer(
            "📢 Botdan foydalanish uchun kanalimizga obuna bo‘ling.\n\n"
            "Obuna bo‘lgandan keyin «✅ Tekshirish» tugmasini bosing.",
            reply_markup=force_sub_kb(),
        )
        return

    await message.answer(
        "🎬 <b>Kino Bot</b>\n\n"
        "Kerakli kinoni kod yoki nom orqali tez toping.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Yordam")
async def cmd_help(message: Message):
    await register_user(message)

    text = (
        "ℹ️ <b>Yordam</b>\n\n"
        "🔢 <b>Kod orqali</b> — kino kodini yuboring\n"
        "🔎 <b>Nom orqali</b> — kino nomini yozing\n"
        "🎭 <b>Janr</b> — janr bo‘yicha qidirish\n"
        "🔥 <b>Mashhur</b> — eng ko‘p ko‘rilganlar\n"
        "❤️ <b>Sevimlilar</b> — tanlangan kinolar\n\n"
        f"💬 Savollar: {SUPPORT_USERNAME}"
    )

    await message.answer(
        text,
        reply_markup=main_menu_kb(),
    )


# =========================================================
# SUBSCRIPTION
# =========================================================

@router.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery, bot: Bot):
    if await check_subscription(bot, callback.from_user.id):
        try:
            await callback.message.edit_text("✅ Obuna tasdiqlandi!")
        except Exception:
            pass

        await callback.message.answer(
            "🎬 <b>Kino Bot</b>\n\n"
            "Kerakli kinoni kod yoki nom orqali tez toping.",
            reply_markup=main_menu_kb(),
        )

        await callback.answer()

    else:
        await callback.answer(
            "❌ Siz hali kanalga obuna bo‘lmagansiz.",
            show_alert=True,
        )


# =========================================================
# BACK
# =========================================================

@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    await callback.message.answer(
        "🎬 Asosiy menyu",
        reply_markup=main_menu_kb(),
    )

    await callback.answer()


# =========================================================
# SEARCH BY CODE
# =========================================================

@router.message(F.text == "🔢 Kod orqali qidirish")
async def search_by_code_start(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    await register_user(message)

    if not await check_subscription(bot, message.from_user.id):
        await message.answer(
            "📢 Kanalga obuna bo‘ling.",
            reply_markup=force_sub_kb(),
        )
        return

    await state.set_state(SearchCode.waiting)

    await message.answer(
        "🔢 Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(SearchCode.waiting)
async def search_by_code_process(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    code = (message.text or "").strip()

    movie = await get_movie_by_code(code)

    if not movie:
        await message.answer(
            "❌ Kino topilmadi.\n"
            "Boshqa kod yuboring."
        )
        return

    await state.clear()

    favorite = await is_favorite(
        message.from_user.id,
        movie["id"],
    )

    await message.answer(
        format_movie_card(movie),
        reply_markup=movie_actions_kb(
            movie["id"],
            favorite,
        ),
    )


# =========================================================
# SEARCH BY TITLE
# =========================================================

@router.message(F.text == "🎬 Kino qidirish")
async def search_by_title_start(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    await register_user(message)

    if not await check_subscription(bot, message.from_user.id):
        await message.answer(
            "📢 Kanalga obuna bo‘ling.",
            reply_markup=force_sub_kb(),
        )
        return

    await state.set_state(SearchTitle.waiting)

    await message.answer(
        "🎬 Kino nomini yozing:",
        reply_markup=cancel_kb(),
    )


@router.message(SearchTitle.waiting)
async def search_by_title_process(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    query = (message.text or "").strip()

    try:
        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .or_(
                f"title.ilike.%{query}%,"
                f"alternative_title.ilike.%{query}%"
            )
            .order("views", desc=True)
            .limit(20)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(f"title search error: {e}")
        await message.answer("❌ Qidirishda xatolik.")
        return

    if not rows:
        await message.answer(
            "❌ Hech narsa topilmadi.\n"
            "Boshqa nom yuboring."
        )
        return

    await state.clear()

    if len(rows) == 1:
        movie = rows[0]

        favorite = await is_favorite(
            message.from_user.id,
            movie["id"],
        )

        await message.answer(
            format_movie_card(movie),
            reply_markup=movie_actions_kb(
                movie["id"],
                favorite,
            ),
        )
        return

    text = "🔍 <b>Topilgan kinolar:</b>\n\n"
    buttons = []

    for row in rows:
        text += (
            f"🎬 {row['title']} "
            f"({row.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=row["title"][:30],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="back_main",
            )
        ]
    )

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


# =========================================================
# SHOW MOVIE
# =========================================================

@router.callback_query(F.data.startswith("movie_"))
async def show_movie_callback(callback: CallbackQuery):
    try:
        movie_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto‘g‘ri kino ID.", show_alert=True)
        return

    movie = await get_movie_by_id(movie_id)

    if not movie:
        await callback.answer(
            "Kino topilmadi.",
            show_alert=True,
        )
        return

    favorite = await is_favorite(
        callback.from_user.id,
        movie_id,
    )

    await callback.message.answer(
        format_movie_card(movie),
        reply_markup=movie_actions_kb(
            movie_id,
            favorite,
        ),
    )

    await callback.answer()


# =========================================================
# WATCH MOVIE
# =========================================================

@router.callback_query(F.data.startswith("watch_"))
async def watch_movie(
    callback: CallbackQuery,
    bot: Bot,
):
    try:
        movie_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            "Noto‘g‘ri kino ID.",
            show_alert=True,
        )
        return

    movie = await get_movie_by_id(movie_id)

    if not movie:
        await callback.answer(
            "Kino topilmadi.",
            show_alert=True,
        )
        return

    try:
        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=movie["channel_id"],
            message_id=movie["message_id"],
        )

        await increment_views(movie_id)

        await callback.answer("🎬 Kino yuborildi ✅")

    except TelegramForbiddenError:
        await callback.answer(
            "❌ Botni bloklagansiz.",
            show_alert=True,
        )

    except TelegramBadRequest as e:
        logger.error(f"copy_message error: {e}")

        await callback.answer(
            "❌ Kino xabari topilmadi yoki o‘chirilgan.",
            show_alert=True,
        )

    except Exception as e:
        logger.error(f"watch error: {e}")

        await callback.answer(
            "❌ Xatolik yuz berdi.",
            show_alert=True,
        )


# =========================================================
# FAVORITES
# =========================================================

@router.callback_query(F.data.startswith("fav_"))
async def add_favorite(callback: CallbackQuery):
    try:
        movie_id = int(callback.data.split("_", 1)[1])

        (
            get_supabase()
            .table("favorites")
            .upsert(
                {
                    "telegram_id": callback.from_user.id,
                    "movie_id": movie_id,
                },
                on_conflict="telegram_id,movie_id",
            )
            .execute()
        )

        await callback.answer("❤️ Sevimlilarga qo‘shildi")

        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(
                movie_id,
                True,
            )
        )

    except Exception as e:
        logger.error(f"favorite error: {e}")

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("unfav_"))
async def remove_favorite(callback: CallbackQuery):
    try:
        movie_id = int(callback.data.split("_", 1)[1])

        (
            get_supabase()
            .table("favorites")
            .delete()
            .eq("telegram_id", callback.from_user.id)
            .eq("movie_id", movie_id)
            .execute()
        )

        await callback.answer("💔 Olib tashlandi")

        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(
                movie_id,
                False,
            )
        )

    except Exception as e:
        logger.error(f"unfavorite error: {e}")

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )


@router.message(F.text == "❤️ Sevimlilar")
async def show_favorites(
    message: Message,
    bot: Bot,
):
    await register_user(message)

    if not await check_subscription(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Kanalga obuna bo‘ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_supabase()
            .table("favorites")
            .select("movie_id, movies(*)")
            .eq("telegram_id", message.from_user.id)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(f"favorites list error: {e}")
        await message.answer("❌ Xatolik.")
        return

    movies = []

    for row in rows:
        movie = row.get("movies")

        if isinstance(movie, list):
            movie = movie[0] if movie else None

        if movie:
            movies.append(movie)

    if not movies:
        await message.answer(
            "❤️ Sevimlilar bo‘sh.",
            reply_markup=main_menu_kb(),
        )
        return

    text = "❤️ <b>Sevimlilaringiz:</b>\n\n"
    buttons = []

    for movie in movies:
        text += f"🎬 {movie['title']}\n"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=movie["title"][:40],
                    callback_data=f"movie_{movie['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="back_main",
            )
        ]
    )

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


# =========================================================
# GENRES
# =========================================================

@router.message(F.text == "🎭 Janrlar")
async def show_genres(
    message: Message,
    bot: Bot,
):
    await register_user(message)

    if not await check_subscription(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Kanalga obuna bo‘ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_supabase()
            .table("genres")
            .select("name")
            .order("name")
            .execute()
        )

        genres = [
            x["name"]
            for x in (result.data or [])
            if x.get("name")
        ]

    except Exception as e:
        logger.error(f"genres error: {e}")
        await message.answer("❌ Xatolik.")
        return

    if not genres:
        await message.answer("Janrlar yo‘q.")
        return

    await message.answer(
        "🎭 <b>Janrni tanlang:</b>",
        reply_markup=genres_kb(genres),
    )


@router.callback_query(F.data.startswith("genre_"))
async def genre_movies(callback: CallbackQuery):
    genre = callback.data[6:]

    try:
        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .ilike("genre", f"%{genre}%")
            .order("views", desc=True)
            .limit(30)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(f"genre movies error: {e}")

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )
        return

    if not rows:
        await callback.answer(
            "Bu janrda kino yo‘q.",
            show_alert=True,
        )
        return

    text = f"🎭 <b>{genre}</b>\n\n"
    buttons = []

    for row in rows:
        text += (
            f"🎬 {row['title']} "
            f"({row.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=row["title"][:35],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="back_main",
            )
        ]
    )

    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


# =========================================================
# POPULAR
# =========================================================

@router.message(F.text == "🔥 Eng ko‘p ko‘rilgan")
async def popular_movies(
    message: Message,
    bot: Bot,
):
    await register_user(message)

    if not await check_subscription(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Kanalga obuna bo‘ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .order("views", desc=True)
            .limit(10)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(f"popular movies error: {e}")
        await message.answer("❌ Xatolik.")
        return

    if not rows:
        await message.answer("Hali kino yo‘q.")
        return

    text = "🔥 <b>TOP 10</b>\n\n"
    buttons = []

    for i, row in enumerate(rows, 1):
        text += (
            f"{i}. {row['title']} — "
            f"{row.get('views', 0):,} views\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{i}. {row['title'][:30]}",
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="back_main",
            )
        ]
    )

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


# =========================================================
# NEW MOVIES
# =========================================================

@router.message(F.text == "🆕 Yangi kinolar")
async def new_movies(
    message: Message,
    bot: Bot,
):
    await register_user(message)

    if not await check_subscription(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Kanalga obuna bo‘ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(f"new movies error: {e}")
        await message.answer("❌ Xatolik.")
        return

    if not rows:
        await message.answer("Hali kino yo‘q.")
        return

    text = "🆕 <b>Yangi kinolar</b>\n\n"
    buttons = []

    for row in rows:
        text += (
            f"🎬 {row['title']} "
            f"({row.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=row["title"][:35],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="back_main",
            )
        ]
    )

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


# =========================================================
# ADMIN PANEL
# =========================================================

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "👨‍💻 <b>ADMIN PANEL</b>",
        reply_markup=admin_menu_kb(),
    )


# =========================================================
# ADMIN ADD
# =========================================================

@router.callback_query(F.data == "admin_add")
async def admin_add_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AddMovie.code)

    await callback.message.answer(
        "1️⃣ Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )

    await callback.answer()


@router.message(AddMovie.code)
async def add_code(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    code = (message.text or "").strip()

    if not code:
        await message.answer("❌ Kod bo‘sh bo‘lmasin.")
        return

    if await get_movie_by_code(code):
        await message.answer(
            "❌ Bu kod allaqachon mavjud."
        )
        return

    await state.update_data(code=code)
    await state.set_state(AddMovie.title)

    await message.answer(
        "2️⃣ Kino nomini yuboring:"
    )


@router.message(AddMovie.title)
async def add_title(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    title = (message.text or "").strip()

    if not title:
        await message.answer("❌ Kino nomi bo‘sh bo‘lmasin.")
        return

    await state.update_data(title=title)
    await state.set_state(AddMovie.alt_title)

    await message.answer(
        "3️⃣ Muqobil nomini yuboring yoki -:"
    )


@router.message(AddMovie.alt_title)
async def add_alt(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    value = (message.text or "").strip()

    if value == "-":
        value = None

    await state.update_data(
        alternative_title=value
    )

    await state.set_state(AddMovie.description)

    await message.answer(
        "4️⃣ Tavsifni yuboring yoki -:"
    )


@router.message(AddMovie.description)
async def add_desc(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    value = (message.text or "").strip()

    if value == "-":
        value = None

    await state.update_data(description=value)
    await state.set_state(AddMovie.year)

    await message.answer(
        "5️⃣ Yilini yuboring yoki -:"
    )


@router.message(AddMovie.year)
async def add_year(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    value = (message.text or "").strip()
    year = None

    if value != "-":
        try:
            year = int(value)
        except ValueError:
            await message.answer(
                "❌ Yil faqat raqam bo‘lishi kerak."
            )
            return

    await state.update_data(year=year)
    await state.set_state(AddMovie.genre)

    await message.answer(
        "6️⃣ Janrini yuboring:"
    )


@router.message(AddMovie.genre)
async def add_genre(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    genre = (message.text or "").strip()

    if not genre:
        await message.answer("❌ Janr bo‘sh bo‘lmasin.")
        return

    await state.update_data(genre=genre)
    await state.set_state(AddMovie.rating)

    await message.answer(
        "7️⃣ Reytingini yuboring yoki -:"
    )


@router.message(AddMovie.rating)
async def add_rating(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    value = (message.text or "").strip()
    rating = None

    if value != "-":
        try:
            rating = float(value.replace(",", "."))
        except ValueError:
            await message.answer(
                "❌ Reyting noto‘g‘ri."
            )
            return

    await state.update_data(rating=rating)
    await state.set_state(AddMovie.channel_id)

    await message.answer(
        "8️⃣ Kanal ID sini yuboring:\n\n"
        "Masalan:\n"
        "-1001234567890"
    )


@router.message(AddMovie.channel_id)
async def add_channel(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    try:
        channel_id = int((message.text or "").strip())
    except ValueError:
        await message.answer(
            "❌ Channel ID noto‘g‘ri."
        )
        return

    await state.update_data(channel_id=channel_id)
    await state.set_state(AddMovie.message_id)

    await message.answer(
        "9️⃣ Telegram Message ID sini yuboring:"
    )


@router.message(AddMovie.message_id)
async def add_message_id(
    message: Message,
    state: FSMContext,
):
    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    try:
        message_id = int((message.text or "").strip())
    except ValueError:
        await message.answer(
            "❌ Message ID noto‘g‘ri."
        )
        return

    data = await state.get_data()

    await state.update_data(message_id=message_id)
    data = await state.get_data()
    await finish_add(message, state, data, fast=False)


# =========================================================
# FAST ADD: CHANNEL MESSAGE REPLY + /add
# =========================================================

async def save_movie_to_supabase(movie_data: Dict) -> tuple[bool, str]:
    """Kinoni Supabase'ga xavfsiz saqlaydi va aniq xatoni qaytaradi."""
    try:
        sb = get_supabase()
        result = sb.table("movies").insert(movie_data).execute()
        if not result.data:
            return False, "Supabase insert javobida ma'lumot qaytmadi."
        return True, ""
    except Exception as e:
        logger.exception("movie insert error")
        return False, str(e)


async def finish_add(message: Message, state: FSMContext, data: Dict, *, fast: bool = False):
    """FSM'dagi ma'lumotlarni tekshiradi va kinoni bitta joydan saqlaydi."""
    required = ("code", "title", "genre", "channel_id", "message_id")
    missing = [key for key in required if data.get(key) in (None, "")]
    if missing:
        await message.answer(
            "❌ Ma'lumot yetishmayapti: " + ", ".join(missing) +
            ". /add jarayonini qaytadan boshlang."
        )
        await state.clear()
        return

    movie_data = {
        "code": str(data["code"]).strip(),
        "title": str(data["title"]).strip(),
        "alternative_title": data.get("alternative_title"),
        "description": data.get("description"),
        "year": data.get("year"),
        "genre": str(data["genre"]).strip(),
        "rating": data.get("rating"),
        "channel_id": int(data["channel_id"]),
        "message_id": int(data["message_id"]),
        "views": 0,
    }

    if await get_movie_by_code(movie_data["code"]):
        await message.answer(
            f"❌ <b>{movie_data['code']}</b> kodi allaqachon mavjud.\n"
            "Boshqa kod kiriting."
        )
        return

    ok, error = await save_movie_to_supabase(movie_data)
    if not ok:
        await message.answer(
            "❌ <b>Kino bazaga qo‘shilmadi.</b>\n\n"
            "Supabase xatosi:\n"
            f"<code>{error[:3000]}</code>\n\n"
            "Tekshiring: movies jadvalidagi ustun nomlari, RLS/policy va SUPABASE_KEY."
        )
        return

    await state.clear()

    prefix = "tez " if fast else ""
    await message.answer(
        f"✅ <b>Kino {prefix}qo‘shildi!</b>\n\n"
        f"🔢 Kod: <code>{movie_data['code']}</code>\n"
        f"🎬 {movie_data['title']}\n"
        f"🎭 Janr: {movie_data['genre']}\n"
        f"📢 Kanal: <code>{movie_data['channel_id']}</code>\n"
        f"💬 Message ID: <code>{movie_data['message_id']}</code>",
        reply_markup=main_menu_kb(),
    )


# =========================================================
# FAST ADD: CHANNEL MESSAGE REPLY + /add
# =========================================================

@router.message(Command("add"))
async def fast_add_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    # Har bir yangi /add eski FSM jarayonini tozalaydi.
    await state.clear()

    replied = message.reply_to_message
    if replied is None:
        await message.answer(
            "📌 Avval kanaldagi kino xabariga <b>Reply</b> qiling, "
            "keyin shu reply ustiga <code>/add</code> yuboring."
        )
        return

    # /add qaysi xabarga reply qilingan bo‘lsa, aynan o‘sha
    # Telegram chat_id + message_id bazaga yoziladi.
    channel_id = replied.chat.id
    message_id = replied.message_id

    if not channel_id or not message_id:
        await message.answer("❌ Reply qilingan xabar ma'lumotlari topilmadi.")
        return

    await state.update_data(
        channel_id=channel_id,
        message_id=message_id,
    )
    await state.set_state(FastAddMovie.code)

    await message.answer(
        "1️⃣ Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(FastAddMovie.code)
async def fast_code(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    code = (message.text or "").strip()
    if not code:
        await message.answer("❌ Kod bo‘sh bo‘lmasin.")
        return
    if len(code) > 100:
        await message.answer("❌ Kod juda uzun.")
        return
    if await get_movie_by_code(code):
        await message.answer("❌ Bu kod allaqachon mavjud. Boshqa kod yuboring.")
        return

    await state.update_data(code=code)
    await state.set_state(FastAddMovie.title)
    await message.answer("2️⃣ Kino nomini yuboring:")


@router.message(FastAddMovie.title)
async def fast_title(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ Kino nomi bo‘sh bo‘lmasin.")
        return
    await state.update_data(title=title)
    await state.set_state(FastAddMovie.description)
    await message.answer("3️⃣ Tavsif yoki -:")


@router.message(FastAddMovie.description)
async def fast_desc(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    value = (message.text or "").strip()
    await state.update_data(description=None if value == "-" else value)
    await state.set_state(FastAddMovie.year)
    await message.answer("4️⃣ Yil yoki -:")


@router.message(FastAddMovie.year)
async def fast_year(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    value = (message.text or "").strip()
    year = None
    if value != "-":
        try:
            year = int(value)
        except ValueError:
            await message.answer("❌ Yil faqat raqam bo‘lishi kerak.")
            return
        if year < 1800 or year > datetime.now().year + 2:
            await message.answer("❌ Yil noto‘g‘ri. Masalan: 2026")
            return
    await state.update_data(year=year)
    await state.set_state(FastAddMovie.genre)
    await message.answer("5️⃣ Janr:")


@router.message(FastAddMovie.genre)
async def fast_genre(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    genre = (message.text or "").strip()
    if not genre:
        await message.answer("❌ Janr bo‘sh bo‘lmasin.")
        return
    await state.update_data(genre=genre)
    await state.set_state(FastAddMovie.rating)
    await message.answer("6️⃣ Reyting yoki -:")


@router.message(FastAddMovie.rating)
async def fast_rating(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    rating = None
    if value != "-":
        try:
            rating = float(value.replace(",", "."))
        except ValueError:
            await message.answer("❌ Reyting noto‘g‘ri. Masalan: 8.5")
            return
        if rating < 0 or rating > 10:
            await message.answer("❌ Reyting 0 dan 10 gacha bo‘lishi kerak.")
            return

    await state.update_data(rating=rating)
    data = await state.get_data()
    await finish_add(message, state, data, fast=True)


# =========================================================
# ADMIN MOVIES
# =========================================================

@router.callback_query(F.data == "admin_list")
@router.message(Command("movies"))
async def admin_list(event: Message | CallbackQuery):
    if not is_admin(event.from_user.id):
        return

    try:
        result = (
            get_supabase()
            .table("movies")
            .select(
                "id,code,title,channel_id,message_id,"
                "views,year,genre,rating"
            )
            .order("id", desc=True)
            .limit(50)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(f"admin list error: {e}")

        if isinstance(event, CallbackQuery):
            await event.answer(
                "❌ Bazani o‘qishda xatolik.",
                show_alert=True,
            )
        else:
            await event.answer(
                "❌ Bazani o‘qishda xatolik."
            )
        return

    if not rows:
        text = "📋 Bazada kino yo‘q."
    else:
        parts = ["📋 <b>KINOLAR BAZASI</b>\n"]

        for row in rows:
            parts.append(
                "━━━━━━━━━━━━━━\n"
                f"🆔 ID: <code>{row['id']}</code>\n"
                f"🎬 <b>{row['title']}</b>\n"
                f"🔢 Kod: <code>{row['code']}</code>\n"
                f"📢 Kanal: <code>{row['channel_id']}</code>\n"
                f"💬 Message ID: <code>{row['message_id']}</code>\n"
                f"📅 Yil: {row.get('year') or '—'}\n"
                f"🎭 Janr: {row.get('genre') or '—'}\n"
                f"⭐ Reyting: {row.get('rating') if row.get('rating') is not None else '—'}\n"
                f"👀 Ko‘rishlar: {row.get('views', 0)}"
            )

        text = "\n".join(parts)

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


# =========================================================
# ADMIN STATS
# =========================================================

@router.callback_query(F.data == "admin_stats")
@router.message(Command("stats"))
async def admin_stats(event: Message | CallbackQuery):
    if not is_admin(event.from_user.id):
        return

    try:
        users_result = (
            get_supabase()
            .table("users")
            .select("id", count="exact")
            .execute()
        )

        movies_result = (
            get_supabase()
            .table("movies")
            .select("id", count="exact")
            .execute()
        )

        movies = (
            get_supabase()
            .table("movies")
            .select("views")
            .execute()
        )

        total_views = sum(
            (x.get("views") or 0)
            for x in (movies.data or [])
        )

        today_start = datetime.now(timezone.utc).date().isoformat()

        today_result = (
            get_supabase()
            .table("users")
            .select("id", count="exact")
            .gte("last_activity", f"{today_start}T00:00:00+00:00")
            .execute()
        )

        top_result = (
            get_supabase()
            .table("movies")
            .select("title,views")
            .order("views", desc=True)
            .limit(1)
            .execute()
        )

        top = (
            top_result.data[0]
            if top_result.data
            else None
        )

        top_text = (
            f"{top['title']} ({top.get('views', 0)} views)"
            if top
            else "—"
        )

        text = (
            "📊 <b>STATISTIKA</b>\n\n"
            f"👥 Foydalanuvchilar: "
            f"{users_result.count or 0}\n"
            f"🎬 Kinolar: "
            f"{movies_result.count or 0}\n"
            f"👀 Jami ko‘rishlar: "
            f"{total_views:,}\n"
            f"🟢 Bugungi faol: "
            f"{today_result.count or 0}\n"
            f"🔥 Eng ko‘p ko‘rilgan: "
            f"{top_text}"
        )

    except Exception as e:
        logger.error(f"admin stats error: {e}")
        text = "❌ Statistikani olishda xatolik."

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


# =========================================================
# ADMIN USERS
# =========================================================

@router.callback_query(F.data == "admin_users")
@router.message(Command("users"))
async def admin_users(event: Message | CallbackQuery):
    if not is_admin(event.from_user.id):
        return

    try:
        total_result = (
            get_supabase()
            .table("users")
            .select("id", count="exact")
            .execute()
        )

        blocked_result = (
            get_supabase()
            .table("users")
            .select("id", count="exact")
            .eq("is_blocked", True)
            .execute()
        )

        text = (
            "👥 <b>FOYDALANUVCHILAR</b>\n\n"
            f"👤 Jami: {total_result.count or 0}\n"
            f"🚫 Bloklagan: {blocked_result.count or 0}"
        )

    except Exception as e:
        logger.error(f"admin users error: {e}")
        text = "❌ Foydalanuvchilarni olishda xatolik."

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


# =========================================================
# ADMIN DELETE
# =========================================================

@router.callback_query(F.data == "admin_delete")
async def admin_delete_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(AdminDelete.confirm)

    await callback.message.answer(
        "🗑 O‘chirmoqchi bo‘lgan kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )

    await callback.answer()


@router.message(AdminDelete.confirm)
async def admin_delete_confirm(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        return

    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    code = (message.text or "").strip()

    movie = await get_movie_by_code(code)

    if not movie:
        await message.answer(
            "❌ Kino topilmadi."
        )
        await state.clear()
        return

    await state.update_data(
        movie_id=movie["id"]
    )

    await message.answer(
        "⚠️ <b>Rostdan ham o‘chirmoqchimisiz?</b>\n\n"
        f"🎬 {movie['title']}\n"
        f"🔢 Kod: {movie['code']}",
        reply_markup=confirm_delete_kb(
            movie["id"]
        ),
    )


@router.callback_query(F.data.startswith("del_yes_"))
async def del_yes(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        return

    try:
        movie_id = int(
            callback.data.split("_")[2]
        )

        # FK sababli favorites avval o‘chiriladi.
        (
            get_supabase()
            .table("favorites")
            .delete()
            .eq("movie_id", movie_id)
            .execute()
        )

        (
            get_supabase()
            .table("movies")
            .delete()
            .eq("id", movie_id)
            .execute()
        )

        await state.clear()

        await callback.message.edit_text(
            "✅ Kino o‘chirildi."
        )

        await callback.answer()

    except Exception as e:
        logger.error(f"delete movie error: {e}")

        await callback.answer(
            "❌ O‘chirishda xatolik.",
            show_alert=True,
        )


@router.callback_query(F.data == "del_no")
async def del_no(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.edit_text(
        "❌ Bekor qilindi."
    )

    await callback.answer()


# =========================================================
# BROADCAST
# =========================================================

@router.callback_query(F.data == "admin_broadcast")
@router.message(Command("broadcast"))
async def broadcast_start(
    event: Message | CallbackQuery,
    state: FSMContext,
):
    if not is_admin(event.from_user.id):
        return

    await state.set_state(Broadcast.waiting)

    text = (
        "📢 Reklama xabarini yuboring.\n\n"
        "Matn, rasm, video yoki document "
        "yuborishingiz mumkin.\n\n"
        "Bekor qilish: /cancel"
    )

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


@router.message(Command("cancel"))
async def cancel_any(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "Bekor qilindi.",
        reply_markup=main_menu_kb(),
    )


@router.message(Broadcast.waiting)
async def broadcast_process(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    if not is_admin(message.from_user.id):
        return

    await state.clear()

    try:
        result = (
            get_supabase()
            .table("users")
            .select("telegram_id")
            .eq("is_blocked", False)
            .execute()
        )

        users = [
            row["telegram_id"]
            for row in (result.data or [])
        ]

    except Exception as e:
        logger.error(f"broadcast users error: {e}")

        await message.answer(
            "❌ Foydalanuvchilarni olishda xatolik."
        )
        return

    success = 0
    failed = 0
    blocked = 0

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(
                    user_id,
                    message.photo[-1].file_id,
                    caption=message.caption,
                )

            elif message.video:
                await bot.send_video(
                    user_id,
                    message.video.file_id,
                    caption=message.caption,
                )

            elif message.document:
                await bot.send_document(
                    user_id,
                    message.document.file_id,
                    caption=message.caption,
                )

            elif message.audio:
                await bot.send_audio(
                    user_id,
                    message.audio.file_id,
                    caption=message.caption,
                )

            elif message.animation:
                await bot.send_animation(
                    user_id,
                    message.animation.file_id,
                    caption=message.caption,
                )

            else:
                await bot.send_message(
                    user_id,
                    message.text or message.caption or "",
                )

            success += 1

            # Telegram rate limitga tushmaslik uchun kichik pauza.
            await asyncio.sleep(0.05)

        except TelegramForbiddenError:
            blocked += 1

            try:
                (
                    get_supabase()
                    .table("users")
                    .update({"is_blocked": True})
                    .eq("telegram_id", user_id)
                    .execute()
                )
            except Exception as e:
                logger.warning(
                    f"block status update error {user_id}: {e}"
                )

        except Exception as e:
            failed += 1

            logger.warning(
                f"broadcast {user_id}: {e}"
            )

    await message.answer(
        "📢 <b>Reklama yakunlandi</b>\n\n"
        f"✅ Yuborildi: {success}\n"
        f"❌ Xatolik: {failed}\n"
        f"🚫 Bloklagan: {blocked}"
    )


# =========================================================
# FORCE SUB INFO
# =========================================================

@router.callback_query(F.data == "admin_force_sub")
async def admin_force_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    await callback.message.answer(
        f"📢 <b>Majburiy obuna</b>\n\n"
        f"Kanal ID:\n"
        f"<code>{CHANNEL_ID}</code>\n\n"
        "Bot kanal administratori bo‘lishi kerak."
    )

    await callback.answer()


# =========================================================
# SETTINGS
# =========================================================

@router.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    await callback.message.answer(
        "⚙️ <b>SOZLAMALAR</b>\n\n"
        f"🤖 BOT_TOKEN: "
        f"{'Sozlangan' if BOT_TOKEN else 'Yo‘q'}\n"
        f"👨‍💻 ADMIN_IDS: {ADMIN_IDS}\n"
        f"📢 CHANNEL_ID: <code>{CHANNEL_ID}</code>\n"
        f"💬 SUPPORT: {SUPPORT_USERNAME}\n"
        "🗄 DATABASE: Supabase ✅"
    )

    await callback.answer()


# =========================================================
# ADMIN EDIT INFO
# =========================================================

@router.callback_query(F.data == "admin_edit")
async def admin_edit_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    await callback.message.answer(
        "✏️ <b>Kino tahrirlash</b>\n\n"
        "Kino ma’lumotlari Supabase bazasida saqlanadi.\n\n"
        "Hozirgi versiyada qo‘shish, qidirish, "
        "o‘chirish va ko‘rish funksiyalari ishlaydi.\n"
        "Tahrirlash uchun Supabase jadvalidan "
        "ma’lumotni o‘zgartirish mumkin."
    )

    await callback.answer()


# =========================================================
# FALLBACK SEARCH
# =========================================================

@router.message(F.text)
async def fallback_text(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    current = await state.get_state()

    if current is not None:
        return

    await register_user(message)

    if not await check_subscription(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Kanalga obuna bo‘ling.",
            reply_markup=force_sub_kb(),
        )
        return

    query = (message.text or "").strip()

    if len(query) < 2:
        return

    try:
        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .or_(
                f"title.ilike.%{query}%,"
                f"alternative_title.ilike.%{query}%,"
                f"code.eq.{query}"
            )
            .order("views", desc=True)
            .limit(15)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(f"fallback search error: {e}")

        await message.answer(
            "❌ Qidirishda xatolik."
        )
        return

    if not rows:
        await message.answer(
            "❌ Hech narsa topilmadi.",
            reply_markup=main_menu_kb(),
        )
        return

    if len(rows) == 1:
        movie = rows[0]

        favorite = await is_favorite(
            message.from_user.id,
            movie["id"],
        )

        await message.answer(
            format_movie_card(movie),
            reply_markup=movie_actions_kb(
                movie["id"],
                favorite,
            ),
        )
        return

    text = "🔍 <b>Natijalar:</b>\n\n"
    buttons = []

    for row in rows:
        text += f"🎬 {row['title']}\n"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=row["title"][:35],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="back_main",
            )
        ]
    )

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


# =========================================================
# MAIN
# =========================================================

async def main():
    global supabase

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN .env faylda yo‘q!")
        return

    if not ADMIN_IDS:
        print("ERROR: ADMIN_IDS .env faylda yo‘q!")
        return

    if not SUPABASE_URL:
        print("ERROR: SUPABASE_URL .env faylda yo‘q!")
        return

    if not SUPABASE_KEY:
        print("ERROR: SUPABASE_KEY .env faylda yo‘q!")
        return

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    await ensure_default_genres()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = Dispatcher(
        storage=MemoryStorage()
    )

    dp.include_router(router)

    logger.info(
        "Kino Bot + Supabase starting..."
    )

    try:
        await dp.start_polling(bot)

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
