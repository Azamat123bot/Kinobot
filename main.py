"""
Kino Bot — Professional (aiogram 3 + Supabase)
FSM tuzatilgan, xatolar yopilgan, barqaror ishlaydi.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from supabase import Client, create_client

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: List[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("kino_bot")

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
            [KeyboardButton(text="ℹ️ Yordam")],
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
    fav_text = "💔 Sevimlilardan olib tashlash" if is_fav else "❤️ Sevimlilarga qo‘shish"
    fav_data = f"unfav_{movie_id}" if is_fav else f"fav_{movie_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Kinoni ko‘rish", callback_data=f"watch_{movie_id}")],
            [InlineKeyboardButton(text=fav_text, callback_data=fav_data)],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")],
        ]
    )


def force_sub_kb() -> InlineKeyboardMarkup:
    if CHANNEL_USERNAME:
        link = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
    elif str(CHANNEL_ID).startswith("-100"):
        link = f"https://t.me/c/{str(CHANNEL_ID)[4:]}"
    else:
        link = "https://t.me/"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Kanalga obuna bo‘lish", url=link)],
            [InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")],
        ]
    )


def confirm_delete_kb(movie_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ha", callback_data=f"del_yes_{movie_id}"),
                InlineKeyboardButton(text="❌ Yo‘q", callback_data="del_no"),
            ]
        ]
    )


def genres_kb(genres: List[str]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for g in genres:
        row.append(InlineKeyboardButton(text=g, callback_data=f"genre_{g}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_html(text: Any) -> str:
    if text is None:
        return ""
    return escape(str(text))


def sanitize_query(q: str) -> str:
    return re.sub(r"[%_\\]", "", (q or "").strip())[:100]


async def register_user(message: Message) -> None:
    user = message.from_user
    if not user:
        return
    sb = get_supabase()
    try:
        existing = (
            sb.table("users")
            .select("id")
            .eq("telegram_id", user.id)
            .limit(1)
            .execute()
        )
        if existing.data:
            sb.table("users").update(
                {
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_activity": now_iso(),
                    "is_blocked": False,
                }
            ).eq("telegram_id", user.id).execute()
        else:
            sb.table("users").insert(
                {
                    "telegram_id": user.id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_activity": now_iso(),
                    "is_blocked": False,
                }
            ).execute()
    except Exception as e:
        logger.error("register_user: %s", e)


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
        logger.warning("sub check %s: %s", user_id, e)
        return True


async def get_movie_by_code(code: str) -> Optional[Dict]:
    try:
        r = (
            get_supabase()
            .table("movies")
            .select("*")
            .eq("code", code)
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception as e:
        logger.error("get_movie_by_code: %s", e)
        return None


async def get_movie_by_id(movie_id: int) -> Optional[Dict]:
    try:
        r = (
            get_supabase()
            .table("movies")
            .select("*")
            .eq("id", movie_id)
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception as e:
        logger.error("get_movie_by_id: %s", e)
        return None


async def increment_views(movie_id: int) -> None:
    try:
        get_supabase().rpc("increment_movie_views", {"movie_id": movie_id}).execute()
    except Exception as e:
        logger.error("increment_views: %s", e)


async def is_favorite(telegram_id: int, movie_id: int) -> bool:
    try:
        r = (
            get_supabase()
            .table("favorites")
            .select("id")
            .eq("telegram_id", telegram_id)
            .eq("movie_id", movie_id)
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception as e:
        logger.error("is_favorite: %s", e)
        return False


def format_movie_card(movie: Dict) -> str:
    title = safe_html(movie.get("title") or "Noma'lum")
    alt = movie.get("alternative_title")
    year = movie.get("year") or "—"
    genre = safe_html(movie.get("genre") or "—")
    rating = movie.get("rating")
    views = movie.get("views") or 0
    desc = movie.get("description") or ""
    rating_text = rating if rating is not None else "—"

    text = f"🎬 <b>{title}</b>"
    if alt:
        text += f"\n📌 {safe_html(alt)}"
    text += f"\n\n📅 {year}\n🎭 {genre}\n⭐ {rating_text}\n👀 {views:,}"
    if desc:
        text += f"\n\n📝 {safe_html(desc)}"
    return text


async def ensure_default_genres() -> None:
    defaults = [
        "Action", "Comedy", "Horror", "Romance", "Sci-Fi",
        "Fantasy", "Drama", "Thriller", "Family", "Animation",
    ]
    sb = get_supabase()
    for g in defaults:
        try:
            ex = sb.table("genres").select("id").eq("name", g).limit(1).execute()
            if not ex.data:
                sb.table("genres").insert({"name": g}).execute()
        except Exception as e:
            logger.warning("genre %s: %s", g, e)


async def save_movie(movie_data: Dict) -> tuple[bool, str]:
    try:
        r = get_supabase().table("movies").insert(movie_data).execute()
        if not r.data:
            return False, "Insert javobida ma'lumot yo'q."
        return True, ""
    except Exception as e:
        logger.exception("save_movie")
        return False, str(e)


async def finish_add(
    message: Message,
    state: FSMContext,
    data: Dict,
    *,
    fast: bool = False,
) -> None:
    required = ("code", "title", "genre", "channel_id", "message_id")
    missing = [k for k in required if data.get(k) in (None, "")]
    if missing:
        await message.answer(
            "❌ Ma'lumot yetishmayapti: " + ", ".join(missing) + "\nQaytadan boshlang."
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
            f"❌ <b>{safe_html(movie_data['code'])}</b> kodi allaqachon mavjud."
        )
        return

    ok, err = await save_movie(movie_data)
    if not ok:
        await message.answer(
            "❌ <b>Bazaga yozilmadi.</b>\n\n"
            f"<code>{safe_html(err[:2000])}</code>"
        )
        return

    await state.clear()
    prefix = "tez " if fast else ""
    await message.answer(
        f"✅ <b>Kino {prefix}qo‘shildi!</b>\n\n"
        f"🔢 Kod: <code>{safe_html(movie_data['code'])}</code>\n"
        f"🎬 {safe_html(movie_data['title'])}\n"
        f"🎭 {safe_html(movie_data['genre'])}\n"
        f"📢 <code>{movie_data['channel_id']}</code>\n"
        f"💬 <code>{movie_data['message_id']}</code>",
        reply_markup=main_menu_kb(),
    )


# =========================================================
# ROUTER
# =========================================================

router = Router()


# ---------------------------------------------------------
# START / HELP
# ---------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    await register_user(message)

    if not await check_subscription(bot, message.from_user.id):
        await message.answer(
            "📢 Botdan foydalanish uchun kanalga obuna bo‘ling.\n\n"
            "Obuna bo‘lgach «✅ Tekshirish» ni bosing.",
            reply_markup=force_sub_kb(),
        )
        return

    await message.answer(
        "🎬 <b>Kino Bot</b>\n\nKerakli kinoni kod yoki nom orqali toping.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Yordam")
async def cmd_help(message: Message) -> None:
    await register_user(message)
    await message.answer(
        "ℹ️ <b>Yordam</b>\n\n"
        "🔢 Kod orqali — kino kodini yuboring\n"
        "🔎 Nom orqali — kino nomini yozing\n"
        "🎭 Janr — janr bo‘yicha\n"
        "🔥 Mashhur — eng ko‘p ko‘rilganlar\n"
        "❤️ Sevimlilar — tanlanganlar\n\n"
        f"💬 Savollar: {SUPPORT_USERNAME}",
        reply_markup=main_menu_kb(),
    )


# ---------------------------------------------------------
# SUBSCRIPTION
# ---------------------------------------------------------

@router.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery, bot: Bot) -> None:
    if await check_subscription(bot, callback.from_user.id):
        try:
            await callback.message.edit_text("✅ Obuna tasdiqlandi!")
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            "🎬 <b>Kino Bot</b>\n\nKerakli kinoni kod yoki nom orqali toping.",
            reply_markup=main_menu_kb(),
        )
        await callback.answer()
    else:
        await callback.answer("❌ Hali obuna bo‘lmagansiz.", show_alert=True)


@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("🎬 Asosiy menyu", reply_markup=main_menu_kb())
    await callback.answer()


# ---------------------------------------------------------
# SEARCH BY CODE
# ---------------------------------------------------------

@router.message(F.text == "🔢 Kod orqali qidirish")
async def search_by_code_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await register_user(message)
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("📢 Kanalga obuna bo‘ling.", reply_markup=force_sub_kb())
        return

    await state.clear()
    await state.set_state(SearchCode.waiting)
    await message.answer("🔢 Kino kodini yuboring:", reply_markup=cancel_kb())


@router.message(StateFilter(SearchCode.waiting), F.text)
async def search_by_code_process(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    code = (message.text or "").strip()
    movie = await get_movie_by_code(code)
    if not movie:
        await message.answer("❌ Kino topilmadi.\nBoshqa kod yuboring.")
        return

    await state.clear()
    fav = await is_favorite(message.from_user.id, movie["id"])
    await message.answer(
        format_movie_card(movie),
        reply_markup=movie_actions_kb(movie["id"], fav),
    )


# ---------------------------------------------------------
# SEARCH BY TITLE
# ---------------------------------------------------------

@router.message(F.text == "🎬 Kino qidirish")
async def search_by_title_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await register_user(message)
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("📢 Kanalga obuna bo‘ling.", reply_markup=force_sub_kb())
        return

    await state.clear()
    await state.set_state(SearchTitle.waiting)
    await message.answer("🎬 Kino nomini yozing:", reply_markup=cancel_kb())


@router.message(StateFilter(SearchTitle.waiting), F.text)
async def search_by_title_process(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    query = sanitize_query(message.text or "")
    if len(query) < 2:
        await message.answer("❌ Kamida 2 ta belgi yozing.")
        return

    try:
        r = (
            get_supabase()
            .table("movies")
            .select("*")
            .or_(f"title.ilike.%{query}%,alternative_title.ilike.%{query}%")
            .order("views", desc=True)
            .limit(20)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("title search: %s", e)
        await message.answer("❌ Qidirishda xatolik.")
        return

    if not rows:
        await message.answer("❌ Hech narsa topilmadi.\nBoshqa nom yuboring.")
        return

    await state.clear()

    if len(rows) == 1:
        m = rows[0]
        fav = await is_favorite(message.from_user.id, m["id"])
        await message.answer(format_movie_card(m), reply_markup=movie_actions_kb(m["id"], fav))
        return

    text = "🔍 <b>Topilgan kinolar:</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"🎬 {safe_html(row['title'])} ({row.get('year') or '—'})\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:30], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------
# SHOW / WATCH MOVIE
# ---------------------------------------------------------

@router.callback_query(F.data.startswith("movie_"))
async def show_movie_callback(callback: CallbackQuery) -> None:
    try:
        movie_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto‘g‘ri ID.", show_alert=True)
        return

    movie = await get_movie_by_id(movie_id)
    if not movie:
        await callback.answer("Kino topilmadi.", show_alert=True)
        return

    fav = await is_favorite(callback.from_user.id, movie_id)
    await callback.message.answer(
        format_movie_card(movie),
        reply_markup=movie_actions_kb(movie_id, fav),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("watch_"))
async def watch_movie(callback: CallbackQuery, bot: Bot) -> None:
    try:
        movie_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto‘g‘ri ID.", show_alert=True)
        return

    movie = await get_movie_by_id(movie_id)
    if not movie:
        await callback.answer("Kino topilmadi.", show_alert=True)
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
        await callback.answer("❌ Botni bloklagansiz.", show_alert=True)
    except TelegramBadRequest as e:
        logger.error("copy_message: %s", e)
        await callback.answer("❌ Xabar topilmadi yoki o‘chirilgan.", show_alert=True)
    except Exception as e:
        logger.error("watch: %s", e)
        await callback.answer("❌ Xatolik.", show_alert=True)


# ---------------------------------------------------------
# FAVORITES
# ---------------------------------------------------------

@router.callback_query(F.data.startswith("fav_"))
async def add_favorite(callback: CallbackQuery) -> None:
    try:
        movie_id = int(callback.data.split("_", 1)[1])
        get_supabase().table("favorites").upsert(
            {"telegram_id": callback.from_user.id, "movie_id": movie_id},
            on_conflict="telegram_id,movie_id",
        ).execute()
        await callback.answer("❤️ Qo‘shildi")
        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(movie_id, True)
        )
    except Exception as e:
        logger.error("fav: %s", e)
        await callback.answer("❌ Xatolik.", show_alert=True)


@router.callback_query(F.data.startswith("unfav_"))
async def remove_favorite(callback: CallbackQuery) -> None:
    try:
        movie_id = int(callback.data.split("_", 1)[1])
        get_supabase().table("favorites").delete().eq(
            "telegram_id", callback.from_user.id
        ).eq("movie_id", movie_id).execute()
        await callback.answer("💔 Olib tashlandi")
        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(movie_id, False)
        )
    except Exception as e:
        logger.error("unfav: %s", e)
        await callback.answer("❌ Xatolik.", show_alert=True)


@router.message(F.text == "❤️ Sevimlilar")
async def show_favorites(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("📢 Kanalga obuna bo‘ling.", reply_markup=force_sub_kb())
        return

    try:
        r = (
            get_supabase()
            .table("favorites")
            .select("movie_id, movies(*)")
            .eq("telegram_id", message.from_user.id)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("favorites: %s", e)
        await message.answer("❌ Xatolik.")
        return

    movies = []
    for row in rows:
        m = row.get("movies")
        if isinstance(m, list):
            m = m[0] if m else None
        if m:
            movies.append(m)

    if not movies:
        await message.answer("❤️ Sevimlilar bo‘sh.", reply_markup=main_menu_kb())
        return

    text = "❤️ <b>Sevimlilaringiz:</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for m in movies:
        text += f"🎬 {safe_html(m['title'])}\n"
        buttons.append(
            [InlineKeyboardButton(text=m["title"][:40], callback_data=f"movie_{m['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------
# GENRES / POPULAR / NEW
# ---------------------------------------------------------

@router.message(F.text == "🎭 Janrlar")
async def show_genres(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("📢 Kanalga obuna bo‘ling.", reply_markup=force_sub_kb())
        return

    try:
        r = get_supabase().table("genres").select("name").order("name").execute()
        genres = [x["name"] for x in (r.data or []) if x.get("name")]
    except Exception as e:
        logger.error("genres: %s", e)
        await message.answer("❌ Xatolik.")
        return

    if not genres:
        await message.answer("Janrlar yo‘q.")
        return

    await message.answer("🎭 <b>Janrni tanlang:</b>", reply_markup=genres_kb(genres))


@router.callback_query(F.data.startswith("genre_"))
async def genre_movies(callback: CallbackQuery) -> None:
    genre = callback.data[6:]
    try:
        r = (
            get_supabase()
            .table("movies")
            .select("*")
            .ilike("genre", f"%{genre}%")
            .order("views", desc=True)
            .limit(30)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("genre movies: %s", e)
        await callback.answer("❌ Xatolik.", show_alert=True)
        return

    if not rows:
        await callback.answer("Bu janrda kino yo‘q.", show_alert=True)
        return

    text = f"🎭 <b>{safe_html(genre)}</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"🎬 {safe_html(row['title'])} ({row.get('year') or '—'})\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:35], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")])
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.message(F.text == "🔥 Eng ko‘p ko‘rilgan")
async def popular_movies(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("📢 Kanalga obuna bo‘ling.", reply_markup=force_sub_kb())
        return

    try:
        r = (
            get_supabase()
            .table("movies")
            .select("*")
            .order("views", desc=True)
            .limit(10)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("popular: %s", e)
        await message.answer("❌ Xatolik.")
        return

    if not rows:
        await message.answer("Hali kino yo‘q.")
        return

    text = "🔥 <b>TOP 10</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for i, row in enumerate(rows, 1):
        text += f"{i}. {safe_html(row['title'])} — {row.get('views', 0):,}\n"
        buttons.append(
            [InlineKeyboardButton(text=f"{i}. {row['title'][:30]}", callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(F.text == "🆕 Yangi kinolar")
async def new_movies(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("📢 Kanalga obuna bo‘ling.", reply_markup=force_sub_kb())
        return

    try:
        r = (
            get_supabase()
            .table("movies")
            .select("*")
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("new movies: %s", e)
        await message.answer("❌ Xatolik.")
        return

    if not rows:
        await message.answer("Hali kino yo‘q.")
        return

    text = "🆕 <b>Yangi kinolar</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"🎬 {safe_html(row['title'])} ({row.get('year') or '—'})\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:35], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------
# ADMIN PANEL
# ---------------------------------------------------------

@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer("👨‍💻 <b>ADMIN PANEL</b>", reply_markup=admin_menu_kb())


# ---------------------------------------------------------
# ADMIN ADD (to'liq FSM) — ASOSIY TUZATISH SHU YERDA
# ---------------------------------------------------------

@router.callback_query(F.data == "admin_add")
async def admin_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await state.set_state(AddMovie.code)

    await callback.message.answer(
        "➕ <b>Kino qo‘shish</b>\n\n"
        "1️⃣ Kino kodini yuboring:\n\n"
        "<i>Bu qidiruv emas — yangi kino qo‘shish.</i>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(StateFilter(AddMovie.code), F.text)
async def add_code(message: Message, state: FSMContext) -> None:
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
    await state.set_state(AddMovie.title)
    await message.answer("2️⃣ Kino nomini yuboring:")


@router.message(StateFilter(AddMovie.title), F.text)
async def add_title(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ Kino nomi bo‘sh bo‘lmasin.")
        return

    await state.update_data(title=title)
    await state.set_state(AddMovie.alt_title)
    await message.answer("3️⃣ Muqobil nom (yoki -):")


@router.message(StateFilter(AddMovie.alt_title), F.text)
async def add_alt(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    await state.update_data(alternative_title=None if value == "-" else value)
    await state.set_state(AddMovie.description)
    await message.answer("4️⃣ Tavsif (yoki -):")


@router.message(StateFilter(AddMovie.description), F.text)
async def add_desc(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    await state.update_data(description=None if value == "-" else value)
    await state.set_state(AddMovie.year)
    await message.answer("5️⃣ Yil (yoki -):")


@router.message(StateFilter(AddMovie.year), F.text)
async def add_year(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    year = None
    if value != "-":
        try:
            year = int(value)
            if year < 1800 or year > datetime.now().year + 2:
                await message.answer("❌ Yil noto‘g‘ri.")
                return
        except ValueError:
            await message.answer("❌ Yil faqat raqam bo‘lsin.")
            return

    await state.update_data(year=year)
    await state.set_state(AddMovie.genre)
    await message.answer("6️⃣ Janr:")


@router.message(StateFilter(AddMovie.genre), F.text)
async def add_genre(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    genre = (message.text or "").strip()
    if not genre:
        await message.answer("❌ Janr bo‘sh bo‘lmasin.")
        return

    await state.update_data(genre=genre)
    await state.set_state(AddMovie.rating)
    await message.answer("7️⃣ Reyting (yoki -):")


@router.message(StateFilter(AddMovie.rating), F.text)
async def add_rating(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    rating = None
    if value != "-":
        try:
            rating = float(value.replace(",", "."))
            if not 0 <= rating <= 10:
                await message.answer("❌ Reyting 0–10 oralig‘ida.")
                return
        except ValueError:
            await message.answer("❌ Reyting noto‘g‘ri.")
            return

    await state.update_data(rating=rating)
    await state.set_state(AddMovie.channel_id)
    await message.answer(
        "8️⃣ Kanal ID:\n\nMasalan:\n<code>-1001234567890</code>"
    )


@router.message(StateFilter(AddMovie.channel_id), F.text)
async def add_channel(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    try:
        channel_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Channel ID noto‘g‘ri.")
        return

    await state.update_data(channel_id=channel_id)
    await state.set_state(AddMovie.message_id)
    await message.answer("9️⃣ Message ID:")


@router.message(StateFilter(AddMovie.message_id), F.text)
async def add_message_id(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    try:
        message_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Message ID noto‘g‘ri.")
        return

    await state.update_data(message_id=message_id)
    data = await state.get_data()
    await finish_add(message, state, data, fast=False)


# ---------------------------------------------------------
# FAST ADD: Reply + /add
# ---------------------------------------------------------

@router.message(Command("add"))
async def fast_add_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.clear()

    replied = message.reply_to_message
    if replied is None:
        await message.answer(
            "📌 Kanaldagi kino xabariga <b>Reply</b> qiling, "
            "keyin <code>/add</code> yuboring."
        )
        return

    await state.update_data(
        channel_id=replied.chat.id,
        message_id=replied.message_id,
    )
    await state.set_state(FastAddMovie.code)
    await message.answer(
        "➕ <b>Tez qo‘shish</b>\n\n1️⃣ Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(StateFilter(FastAddMovie.code), F.text)
async def fast_code(message: Message, state: FSMContext) -> None:
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
        await message.answer("❌ Bu kod allaqachon mavjud.")
        return

    await state.update_data(code=code)
    await state.set_state(FastAddMovie.title)
    await message.answer("2️⃣ Kino nomini yuboring:")


@router.message(StateFilter(FastAddMovie.title), F.text)
async def fast_title(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ Nom bo‘sh bo‘lmasin.")
        return

    await state.update_data(title=title)
    await state.set_state(FastAddMovie.description)
    await message.answer("3️⃣ Tavsif (yoki -):")


@router.message(StateFilter(FastAddMovie.description), F.text)
async def fast_desc(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    await state.update_data(description=None if value == "-" else value)
    await state.set_state(FastAddMovie.year)
    await message.answer("4️⃣ Yil (yoki -):")


@router.message(StateFilter(FastAddMovie.year), F.text)
async def fast_year(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    year = None
    if value != "-":
        try:
            year = int(value)
            if year < 1800 or year > datetime.now().year + 2:
                await message.answer("❌ Yil noto‘g‘ri.")
                return
        except ValueError:
            await message.answer("❌ Yil faqat raqam.")
            return

    await state.update_data(year=year)
    await state.set_state(FastAddMovie.genre)
    await message.answer("5️⃣ Janr:")


@router.message(StateFilter(FastAddMovie.genre), F.text)
async def fast_genre(message: Message, state: FSMContext) -> None:
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
    await message.answer("6️⃣ Reyting (yoki -):")


@router.message(StateFilter(FastAddMovie.rating), F.text)
async def fast_rating(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    value = (message.text or "").strip()
    rating = None
    if value != "-":
        try:
            rating = float(value.replace(",", "."))
            if not 0 <= rating <= 10:
                await message.answer("❌ Reyting 0–10.")
                return
        except ValueError:
            await message.answer("❌ Reyting noto‘g‘ri.")
            return

    await state.update_data(rating=rating)
    data = await state.get_data()
    await finish_add(message, state, data, fast=True)


# ---------------------------------------------------------
# ADMIN LIST / STATS / USERS
# ---------------------------------------------------------

@router.callback_query(F.data == "admin_list")
@router.message(Command("movies"))
async def admin_list(event: Union[Message, CallbackQuery]) -> None:
    if not is_admin(event.from_user.id):
        return

    try:
        r = (
            get_supabase()
            .table("movies")
            .select("id,code,title,channel_id,message_id,views,year,genre,rating")
            .order("id", desc=True)
            .limit(40)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("admin list: %s", e)
        text = "❌ Bazani o‘qishda xatolik."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return

    if not rows:
        text = "📋 Bazada kino yo‘q."
    else:
        parts = ["📋 <b>KINOLAR</b>\n"]
        for row in rows:
            parts.append(
                "━━━━━━━━━━━━━━\n"
                f"🆔 <code>{row['id']}</code>\n"
                f"🎬 <b>{safe_html(row['title'])}</b>\n"
                f"🔢 <code>{safe_html(row['code'])}</code>\n"
                f"📢 <code>{row['channel_id']}</code> | 💬 <code>{row['message_id']}</code>\n"
                f"📅 {row.get('year') or '—'} | 🎭 {safe_html(row.get('genre') or '—')}\n"
                f"⭐ {row.get('rating') if row.get('rating') is not None else '—'} | "
                f"👀 {row.get('views', 0)}"
            )
        text = "\n".join(parts)

    if len(text) > 4000:
        text = text[:3900] + "\n\n… (qisqartirildi)"

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


@router.callback_query(F.data == "admin_stats")
@router.message(Command("stats"))
async def admin_stats(event: Union[Message, CallbackQuery]) -> None:
    if not is_admin(event.from_user.id):
        return

    try:
        users_r = get_supabase().table("users").select("id", count="exact").execute()
        movies_r = get_supabase().table("movies").select("id", count="exact").execute()
        views_r = get_supabase().table("movies").select("views").execute()
        total_views = sum((x.get("views") or 0) for x in (views_r.data or []))

        today = datetime.now(timezone.utc).date().isoformat()
        today_r = (
            get_supabase()
            .table("users")
            .select("id", count="exact")
            .gte("last_activity", f"{today}T00:00:00+00:00")
            .execute()
        )

        top_r = (
            get_supabase()
            .table("movies")
            .select("title,views")
            .order("views", desc=True)
            .limit(1)
            .execute()
        )
        top = top_r.data[0] if top_r.data else None
        top_text = (
            f"{safe_html(top['title'])} ({top.get('views', 0)})" if top else "—"
        )

        text = (
            "📊 <b>STATISTIKA</b>\n\n"
            f"👥 Foydalanuvchilar: {users_r.count or 0}\n"
            f"🎬 Kinolar: {movies_r.count or 0}\n"
            f"👀 Jami ko‘rishlar: {total_views:,}\n"
            f"🟢 Bugungi faol: {today_r.count or 0}\n"
            f"🔥 Top: {top_text}"
        )
    except Exception as e:
        logger.error("stats: %s", e)
        text = "❌ Statistikani olishda xatolik."

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


@router.callback_query(F.data == "admin_users")
@router.message(Command("users"))
async def admin_users(event: Union[Message, CallbackQuery]) -> None:
    if not is_admin(event.from_user.id):
        return

    try:
        total = get_supabase().table("users").select("id", count="exact").execute()
        blocked = (
            get_supabase()
            .table("users")
            .select("id", count="exact")
            .eq("is_blocked", True)
            .execute()
        )
        text = (
            "👥 <b>FOYDALANUVCHILAR</b>\n\n"
            f"👤 Jami: {total.count or 0}\n"
            f"🚫 Bloklagan: {blocked.count or 0}"
        )
    except Exception as e:
        logger.error("users: %s", e)
        text = "❌ Xatolik."

    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


# ---------------------------------------------------------
# ADMIN DELETE
# ---------------------------------------------------------

@router.callback_query(F.data == "admin_delete")
async def admin_delete_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return

    await state.clear()
    await state.set_state(AdminDelete.confirm)
    await callback.message.answer(
        "🗑 O‘chirmoqchi bo‘lgan kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(StateFilter(AdminDelete.confirm), F.text)
async def admin_delete_confirm(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    code = (message.text or "").strip()
    movie = await get_movie_by_code(code)
    if not movie:
        await message.answer("❌ Kino topilmadi.")
        await state.clear()
        return

    await state.update_data(movie_id=movie["id"])
    await message.answer(
        "⚠️ <b>Rostdan o‘chirmoqchimisiz?</b>\n\n"
        f"🎬 {safe_html(movie['title'])}\n"
        f"🔢 {safe_html(movie['code'])}",
        reply_markup=confirm_delete_kb(movie["id"]),
    )


@router.callback_query(F.data.startswith("del_yes_"))
async def del_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return

    try:
        movie_id = int(callback.data.split("_")[2])
        get_supabase().table("favorites").delete().eq("movie_id", movie_id).execute()
        get_supabase().table("movies").delete().eq("id", movie_id).execute()
        await state.clear()
        await callback.message.edit_text("✅ Kino o‘chirildi.")
        await callback.answer()
    except Exception as e:
        logger.error("delete: %s", e)
        await callback.answer("❌ O‘chirishda xatolik.", show_alert=True)


@router.callback_query(F.data == "del_no")
async def del_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


# ---------------------------------------------------------
# BROADCAST
# ---------------------------------------------------------

@router.callback_query(F.data == "admin_broadcast")
@router.message(Command("broadcast"))
async def broadcast_start(event: Union[Message, CallbackQuery], state: FSMContext) -> None:
    if not is_admin(event.from_user.id):
        return

    await state.clear()
    await state.set_state(Broadcast.waiting)
    text = (
        "📢 Reklama xabarini yuboring.\n\n"
        "Matn, rasm, video yoki document mumkin.\n\n"
        "Bekor: /cancel"
    )
    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


@router.message(Command("cancel"))
async def cancel_any(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())


@router.message(StateFilter(Broadcast.waiting))
async def broadcast_process(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.clear()

    try:
        r = (
            get_supabase()
            .table("users")
            .select("telegram_id")
            .eq("is_blocked", False)
            .execute()
        )
        users = [row["telegram_id"] for row in (r.data or [])]
    except Exception as e:
        logger.error("broadcast users: %s", e)
        await message.answer("❌ Foydalanuvchilarni olishda xatolik.")
        return

    success = failed = blocked = 0
    status = await message.answer("📢 Yuborilmoqda...")

    for i, uid in enumerate(users, 1):
        try:
            if message.photo:
                await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(uid, message.video.file_id, caption=message.caption)
            elif message.document:
                await bot.send_document(uid, message.document.file_id, caption=message.caption)
            elif message.audio:
                await bot.send_audio(uid, message.audio.file_id, caption=message.caption)
            elif message.animation:
                await bot.send_animation(uid, message.animation.file_id, caption=message.caption)
            else:
                await bot.send_message(uid, message.text or message.caption or "")

            success += 1
            await asyncio.sleep(0.07)

        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, message.text or message.caption or "")
                success += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
            try:
                get_supabase().table("users").update({"is_blocked": True}).eq(
                    "telegram_id", uid
                ).execute()
            except Exception as e:
                logger.warning("block update %s: %s", uid, e)
        except Exception as e:
            failed += 1
            logger.warning("broadcast %s: %s", uid, e)

        if i % 50 == 0:
            try:
                await status.edit_text(
                    f"📢 {i}/{len(users)}\n✅ {success} | ❌ {failed} | 🚫 {blocked}"
                )
            except TelegramBadRequest:
                pass

    await message.answer(
        "📢 <b>Yakunlandi</b>\n\n"
        f"✅ {success}\n❌ {failed}\n🚫 {blocked}"
    )


# ---------------------------------------------------------
# FORCE SUB / SETTINGS / EDIT
# ---------------------------------------------------------

@router.callback_query(F.data == "admin_force_sub")
async def admin_force_info(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        f"📢 <b>Majburiy obuna</b>\n\n"
        f"Kanal ID: <code>{CHANNEL_ID}</code>\n"
        f"Username: {CHANNEL_USERNAME or '—'}\n\n"
        "Bot kanal admini bo‘lishi kerak."
    )
    await callback.answer()


@router.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        "⚙️ <b>SOZLAMALAR</b>\n\n"
        f"🤖 BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}\n"
        f"👨‍💻 ADMIN_IDS: {ADMIN_IDS}\n"
        f"📢 CHANNEL_ID: <code>{CHANNEL_ID}</code>\n"
        f"📢 CHANNEL_USERNAME: {CHANNEL_USERNAME or '—'}\n"
        f"💬 SUPPORT: {SUPPORT_USERNAME}\n"
        "🗄 DATABASE: Supabase ✅"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_edit")
async def admin_edit_info(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        "✏️ <b>Kino tahrirlash</b>\n\n"
        "Hozircha Supabase dashboard orqali tahrirlang.\n"
        "Keyingi versiyada to‘liq FSM tahrirlash qo‘shiladi."
    )
    await callback.answer()


# ---------------------------------------------------------
# FALLBACK SEARCH (faqat holat yo'q bo'lganda)
# ---------------------------------------------------------

@router.message(F.text)
async def fallback_text(message: Message, state: FSMContext, bot: Bot) -> None:
    current = await state.get_state()
    if current is not None:
        return  # FSM jarayonida — hech narsa qilmaymiz

    await register_user(message)
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("📢 Kanalga obuna bo‘ling.", reply_markup=force_sub_kb())
        return

    query = sanitize_query(message.text or "")
    if len(query) < 2:
        return

    try:
        r = (
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
        rows = r.data or []
    except Exception as e:
        logger.error("fallback: %s", e)
        await message.answer("❌ Qidirishda xatolik.")
        return

    if not rows:
        await message.answer("❌ Hech narsa topilmadi.", reply_markup=main_menu_kb())
        return

    if len(rows) == 1:
        m = rows[0]
        fav = await is_favorite(message.from_user.id, m["id"])
        await message.answer(format_movie_card(m), reply_markup=movie_actions_kb(m["id"], fav))
        return

    text = "🔍 <b>Natijalar:</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"🎬 {safe_html(row['title'])}\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:35], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# =========================================================
# MAIN
# =========================================================

async def main() -> None:
    global supabase

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN yo‘q!")
        return
    if not ADMIN_IDS:
        print("ERROR: ADMIN_IDS yo‘q!")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL yoki SUPABASE_KEY yo‘q!")
        return

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    await ensure_default_genres()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Kino Bot ishga tushmoqda...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
