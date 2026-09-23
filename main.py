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
    if x.strip().lstrip("-").isdigit()
]

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()
SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "@support"
).strip()

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
        raise RuntimeError("Supabase ishga tushmagan.")
    return supabase


# =========================================================
# FSM
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
    alternative_title = State()
    description = State()
    year = State()
    genre = State()
    rating = State()


class SearchCode(StatesGroup):
    waiting = State()


class SearchTitle(StatesGroup):
    waiting = State()


class AdminDelete(StatesGroup):
    waiting_code = State()


class AdminEdit(StatesGroup):
    waiting_code = State()
    waiting_value = State()


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
                KeyboardButton(text="ℹ️ Yordam")
            ],
        ],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="❌ Bekor qilish")
            ]
        ],
        resize_keyboard=True,
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Kino qo‘shish",
                    callback_data="admin_add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Kino tahrirlash",
                    callback_data="admin_edit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Kino o‘chirish",
                    callback_data="admin_delete",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Kinolar",
                    callback_data="admin_list",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Statistika",
                    callback_data="admin_stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Foydalanuvchilar",
                    callback_data="admin_users",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Reklama",
                    callback_data="admin_broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Majburiy obuna",
                    callback_data="admin_force_sub",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Sozlamalar",
                    callback_data="admin_settings",
                )
            ],
        ]
    )


def movie_actions_kb(
    movie_id: int,
    is_fav: bool = False,
) -> InlineKeyboardMarkup:

    if is_fav:
        fav_text = "💔 Sevimlilardan olib tashlash"
        fav_data = f"unfav_{movie_id}"
    else:
        fav_text = "❤️ Sevimlilarga qo‘shish"
        fav_data = f"fav_{movie_id}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Kinoni ko‘rish",
                    callback_data=f"watch_{movie_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=fav_text,
                    callback_data=fav_data,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="back_main",
                )
            ],
        ]
    )


def force_sub_kb() -> InlineKeyboardMarkup:
    buttons = []

    if CHANNEL_USERNAME:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="📢 Kanalga obuna bo‘lish",
                    url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="✅ Tekshirish",
                callback_data="check_sub",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


def genres_kb(
    genres: List[Dict[str, Any]]
) -> InlineKeyboardMarkup:

    buttons = []
    row = []

    for genre in genres:
        genre_id = genre["id"]
        name = genre["name"]

        row.append(
            InlineKeyboardButton(
                text=name,
                callback_data=f"genre_{genre_id}",
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

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def edit_fields_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Nom",
                    callback_data="edit_title",
                ),
                InlineKeyboardButton(
                    text="📌 Muqobil nom",
                    callback_data="edit_alt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Tavsif",
                    callback_data="edit_desc",
                ),
                InlineKeyboardButton(
                    text="📅 Yil",
                    callback_data="edit_year",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎭 Janr",
                    callback_data="edit_genre",
                ),
                InlineKeyboardButton(
                    text="⭐ Reyting",
                    callback_data="edit_rating",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Kod",
                    callback_data="edit_code",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="edit_cancel",
                )
            ],
        ]
    )


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_html(value: Any) -> str:
    if value is None:
        return ""

    return escape(str(value))


def sanitize_query(value: str) -> str:
    value = (value or "").strip()

    value = value.replace("\\", "")
    value = value.replace("%", "")
    value = value.replace("_", "")

    return value[:100]


def is_cancel(text: Optional[str]) -> bool:
    return (text or "").strip() == "❌ Bekor qilish"


# =========================================================
# USER
# =========================================================

async def register_user(message: Message) -> None:

    user = message.from_user

    if not user:
        return

    try:
        sb = get_supabase()

        existing = (
            sb.table("users")
            .select("id")
            .eq("telegram_id", user.id)
            .limit(1)
            .execute()
        )

        data = {
            "telegram_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_activity": now_iso(),
            "is_blocked": False,
        }

        if existing.data:
            (
                sb.table("users")
                .update(data)
                .eq("telegram_id", user.id)
                .execute()
            )
        else:
            sb.table("users").insert(data).execute()

    except Exception as e:
        logger.exception("register_user: %s", e)


# =========================================================
# SUBSCRIPTION
# =========================================================

async def check_subscription(
    bot: Bot,
    user_id: int,
) -> bool:

    try:
        member = await bot.get_chat_member(
            CHANNEL_ID,
            user_id,
        )

        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.RESTRICTED,
        )

    except TelegramForbiddenError:
        logger.error(
            "Bot kanalni tekshira olmayapti. "
            "Bot kanal admini ekanini tekshiring."
        )
        return False

    except TelegramBadRequest as e:
        logger.error(
            "CHANNEL_ID noto‘g‘ri yoki kanalga kirish yo‘q: %s",
            e,
        )
        return False

    except Exception as e:
        logger.exception("subscription check: %s", e)
        return False


# =========================================================
# MOVIES
# =========================================================

async def get_movie_by_code(
    code: str,
) -> Optional[Dict[str, Any]]:

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
        logger.exception("get_movie_by_code: %s", e)
        return None


async def get_movie_by_id(
    movie_id: int,
) -> Optional[Dict[str, Any]]:

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
        logger.exception("get_movie_by_id: %s", e)
        return None


async def increment_views(movie_id: int) -> None:

    try:
        (
            get_supabase()
            .rpc(
                "increment_movie_views",
                {"movie_id": movie_id},
            )
            .execute()
        )

    except Exception as e:
        logger.exception(
            "increment_movie_views: %s",
            e,
        )


async def is_favorite(
    telegram_id: int,
    movie_id: int,
) -> bool:

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
        logger.exception("is_favorite: %s", e)
        return False


def format_movie_card(
    movie: Dict[str, Any]
) -> str:

    title = safe_html(
        movie.get("title") or "Noma'lum"
    )

    alt = movie.get("alternative_title")

    year = movie.get("year") or "—"

    genre = safe_html(
        movie.get("genre") or "—"
    )

    rating = movie.get("rating")

    views = movie.get("views") or 0

    description = movie.get("description") or ""

    text = f"🎬 <b>{title}</b>"

    if alt:
        text += f"\n📌 {safe_html(alt)}"

    text += (
        f"\n\n"
        f"📅 {year}\n"
        f"🎭 {genre}\n"
        f"⭐ {rating if rating is not None else '—'}\n"
        f"👀 {views:,}"
    )

    if description:
        text += (
            f"\n\n"
            f"📝 {safe_html(description)}"
        )

    return text


# =========================================================
# SOURCE MESSAGE VALIDATION
# =========================================================

async def validate_source_message(
    bot: Bot,
    channel_id: int,
    message_id: int,
    admin_id: int,
) -> bool:

    try:

        copied = await bot.copy_message(
            chat_id=admin_id,
            from_chat_id=channel_id,
            message_id=message_id,
        )

        try:
            await bot.delete_message(
                chat_id=admin_id,
                message_id=copied.message_id,
            )
        except Exception:
            pass

        return True

    except TelegramBadRequest as e:
        logger.warning(
            "Source message invalid: %s",
            e,
        )
        return False

    except TelegramForbiddenError as e:
        logger.warning(
            "Source access denied: %s",
            e,
        )
        return False

    except Exception as e:
        logger.exception(
            "validate source: %s",
            e,
        )
        return False


# =========================================================
# SAVE MOVIE
# =========================================================

async def save_movie(
    movie_data: Dict[str, Any]
) -> tuple[bool, str]:

    try:

        result = (
            get_supabase()
            .table("movies")
            .insert(movie_data)
            .execute()
        )

        if not result.data:
            return False, "Supabase insert javob bermadi."

        return True, ""

    except Exception as e:

        logger.exception("save_movie: %s", e)

        error = str(e)

        if "duplicate" in error.lower():
            return False, "Bu kod allaqachon mavjud."

        if "unique" in error.lower():
            return False, "Bu kod allaqachon mavjud."

        return False, error


async def finish_add(
    message: Message,
    state: FSMContext,
    data: Dict[str, Any],
    fast: bool = False,
) -> None:

    required = (
        "code",
        "title",
        "genre",
        "channel_id",
        "message_id",
    )

    missing = [
        key
        for key in required
        if data.get(key) in (None, "")
    ]

    if missing:
        await state.clear()

        await message.answer(
            "❌ Ma'lumot yetishmayapti:\n"
            + ", ".join(missing),
            reply_markup=main_menu_kb(),
        )

        return

    code = str(data["code"]).strip()

    title = str(data["title"]).strip()

    existing = await get_movie_by_code(code)

    if existing:
        await message.answer(
            "❌ Bu kod allaqachon mavjud.\n"
            "Boshqa kod tanlang."
        )
        return

    movie_data = {
        "code": code,
        "title": title,
        "alternative_title": data.get(
            "alternative_title"
        ),
        "description": data.get(
            "description"
        ),
        "year": data.get("year"),
        "genre": str(
            data["genre"]
        ).strip(),
        "rating": data.get("rating"),
        "channel_id": int(
            data["channel_id"]
        ),
        "message_id": int(
            data["message_id"]
        ),
        "views": 0,
    }

    ok, error = await save_movie(
        movie_data
    )

    if not ok:
        await message.answer(
            "❌ <b>Kino saqlanmadi.</b>\n\n"
            f"<code>{safe_html(error[:1500])}</code>"
        )
        return

    await state.clear()

    await message.answer(
        "✅ <b>Kino muvaffaqiyatli qo‘shildi!</b>\n\n"
        f"🔢 Kod: <code>{safe_html(code)}</code>\n"
        f"🎬 {safe_html(title)}\n"
        f"🎭 {safe_html(movie_data['genre'])}\n"
        f"📅 {movie_data['year'] or '—'}\n"
        f"⭐ {movie_data['rating'] or '—'}\n"
        f"📢 <code>{movie_data['channel_id']}</code>\n"
        f"💬 <code>{movie_data['message_id']}</code>",
        reply_markup=main_menu_kb(),
    )


# =========================================================
# GENRES
# =========================================================

async def ensure_default_genres() -> None:

    defaults = [
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

    for name in defaults:

        try:

            existing = (
                sb.table("genres")
                .select("id")
                .eq("name", name)
                .limit(1)
                .execute()
            )

            if not existing.data:
                sb.table("genres").insert(
                    {"name": name}
                ).execute()

        except Exception as e:
            logger.warning(
                "genre %s: %s",
                name,
                e,
            )


# =========================================================
# ROUTER
# =========================================================

router = Router()


# =========================================================
# START
# =========================================================

@router.message(Command("start"))
async def cmd_start(
    message: Message,
    bot: Bot,
    state: FSMContext,
):

    await state.clear()

    await register_user(message)

    if not await check_subscription(
        bot,
        message.from_user.id,
    ):

        await message.answer(
            "📢 <b>Botdan foydalanish uchun "
            "kanalga obuna bo‘ling.</b>\n\n"
            "Obuna bo‘lgach "
            "«✅ Tekshirish» tugmasini bosing.",
            reply_markup=force_sub_kb(),
        )

        return

    await message.answer(
        "🎬 <b>Kino Bot</b>\n\n"
        "Kerakli kinoni kod yoki nom orqali toping.",
        reply_markup=main_menu_kb(),
    )


# =========================================================
# HELP
# =========================================================

@router.message(Command("help"))
@router.message(F.text == "ℹ️ Yordam")
async def cmd_help(message: Message):

    await register_user(message)

    await message.answer(
        "ℹ️ <b>Yordam</b>\n\n"
        "🔢 Kod orqali — kino kodini yuboring\n"
        "🎬 Nom orqali — kino nomini yozing\n"
        "🎭 Janr — janr bo‘yicha\n"
        "🔥 Mashhur — eng ko‘p ko‘rilganlar\n"
        "🆕 Yangi kinolar\n"
        "❤️ Sevimlilar\n\n"
        f"💬 Savollar: {safe_html(SUPPORT_USERNAME)}",
        reply_markup=main_menu_kb(),
    )


# =========================================================
# SUBSCRIPTION
# =========================================================

@router.callback_query(
    F.data == "check_sub"
)
async def check_sub_callback(
    callback: CallbackQuery,
    bot: Bot,
):

    if await check_subscription(
        bot,
        callback.from_user.id,
    ):

        try:
            await callback.message.edit_text(
                "✅ <b>Obuna tasdiqlandi!</b>"
            )
        except TelegramBadRequest:
            pass

        await callback.message.answer(
            "🎬 <b>Kino Bot</b>\n\n"
            "Kerakli kinoni kod yoki nom orqali toping.",
            reply_markup=main_menu_kb(),
        )

        await callback.answer()

    else:

        await callback.answer(
            "❌ Hali kanalga obuna bo‘lmagansiz.",
            show_alert=True,
        )


# =========================================================
# BACK
# =========================================================

@router.callback_query(
    F.data == "back_main"
)
async def back_main(
    callback: CallbackQuery,
    state: FSMContext,
):

    await state.clear()

    await callback.message.answer(
        "🎬 Asosiy menyu",
        reply_markup=main_menu_kb(),
    )

    await callback.answer()


# =========================================================
# SEARCH CODE
# =========================================================

@router.message(
    F.text == "🔢 Kod orqali qidirish"
)
async def search_code_start(
    message: Message,
    state: FSMContext,
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

    await state.clear()

    await state.set_state(
        SearchCode.waiting
    )

    await message.answer(
        "🔢 Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(
    StateFilter(SearchCode.waiting),
    F.text,
)
async def search_code_process(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    code = (
        message.text or ""
    ).strip()

    movie = await get_movie_by_code(
        code
    )

    if not movie:

        await message.answer(
            "❌ Kino topilmadi.\n"
            "Boshqa kod yuboring."
        )

        return

    await state.clear()

    fav = await is_favorite(
        message.from_user.id,
        movie["id"],
    )

    await message.answer(
        format_movie_card(movie),
        reply_markup=movie_actions_kb(
            movie["id"],
            fav,
        ),
    )


# =========================================================
# SEARCH TITLE
# =========================================================

@router.message(
    F.text == "🎬 Kino qidirish"
)
async def search_title_start(
    message: Message,
    state: FSMContext,
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

    await state.clear()

    await state.set_state(
        SearchTitle.waiting
    )

    await message.answer(
        "🎬 Kino nomini yozing:",
        reply_markup=cancel_kb(),
    )


@router.message(
    StateFilter(SearchTitle.waiting),
    F.text,
)
async def search_title_process(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    query = sanitize_query(
        message.text or ""
    )

    if len(query) < 2:

        await message.answer(
            "❌ Kamida 2 ta belgi yozing."
        )

        return

    try:

        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .or_(
                f"title.ilike.%{query}%,"
                f"alternative_title.ilike.%{query}%"
            )
            .order(
                "views",
                desc=True,
            )
            .limit(20)
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        logger.exception(
            "title search: %s",
            e,
        )

        await message.answer(
            "❌ Qidirishda xatolik."
        )

        return

    if not rows:

        await message.answer(
            "❌ Hech narsa topilmadi."
        )

        return

    await state.clear()

    if len(rows) == 1:

        movie = rows[0]

        fav = await is_favorite(
            message.from_user.id,
            movie["id"],
        )

        await message.answer(
            format_movie_card(movie),
            reply_markup=movie_actions_kb(
                movie["id"],
                fav,
            ),
        )

        return

    text = "🔍 <b>Topilgan kinolar:</b>\n\n"

    buttons = []

    for row in rows:

        text += (
            f"🎬 {safe_html(row['title'])} "
            f"({row.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        row["title"]
                    )[:35],
                    callback_data=(
                        f"movie_{row['id']}"
                    ),
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
# MOVIE
# =========================================================

@router.callback_query(
    F.data.startswith("movie_")
)
async def show_movie(
    callback: CallbackQuery,
):

    try:
        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )
    except Exception:
        await callback.answer(
            "❌ ID noto‘g‘ri.",
            show_alert=True,
        )
        return

    movie = await get_movie_by_id(
        movie_id
    )

    if not movie:

        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True,
        )

        return

    fav = await is_favorite(
        callback.from_user.id,
        movie_id,
    )

    await callback.message.answer(
        format_movie_card(movie),
        reply_markup=movie_actions_kb(
            movie_id,
            fav,
        ),
    )

    await callback.answer()


# =========================================================
# WATCH
# =========================================================

@router.callback_query(
    F.data.startswith("watch_")
)
async def watch_movie(
    callback: CallbackQuery,
    bot: Bot,
):

    try:

        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

    except Exception:

        await callback.answer(
            "❌ ID noto‘g‘ri.",
            show_alert=True,
        )

        return

    movie = await get_movie_by_id(
        movie_id
    )

    if not movie:

        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True,
        )

        return

    try:

        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=movie["channel_id"],
            message_id=movie["message_id"],
        )

        await increment_views(
            movie_id
        )

        await callback.answer(
            "🎬 Kino yuborildi ✅"
        )

    except TelegramForbiddenError:

        await callback.answer(
            "❌ Botni bloklagansiz.",
            show_alert=True,
        )

    except TelegramBadRequest as e:

        logger.error(
            "watch copy error: %s",
            e,
        )

        await callback.answer(
            "❌ Kino xabari mavjud emas "
            "yoki bot kanalga kira olmayapti.",
            show_alert=True,
        )

    except Exception as e:

        logger.exception(
            "watch: %s",
            e,
        )

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )


# =========================================================
# FAVORITES
# =========================================================

@router.callback_query(
    F.data.startswith("fav_")
)
async def add_favorite(
    callback: CallbackQuery,
):

    try:

        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

        (
            get_supabase()
            .table("favorites")
            .upsert(
                {
                    "telegram_id":
                        callback.from_user.id,
                    "movie_id":
                        movie_id,
                },
                on_conflict=(
                    "telegram_id,movie_id"
                ),
            )
            .execute()
        )

        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(
                movie_id,
                True,
            )
        )

        await callback.answer(
            "❤️ Sevimlilarga qo‘shildi."
        )

    except Exception as e:

        logger.exception(
            "favorite: %s",
            e,
        )

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith("unfav_")
)
async def remove_favorite(
    callback: CallbackQuery,
):

    try:

        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

        (
            get_supabase()
            .table("favorites")
            .delete()
            .eq(
                "telegram_id",
                callback.from_user.id,
            )
            .eq(
                "movie_id",
                movie_id,
            )
            .execute()
        )

        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(
                movie_id,
                False,
            )
        )

        await callback.answer(
            "💔 Sevimlilardan olib tashlandi."
        )

    except Exception as e:

        logger.exception(
            "unfavorite: %s",
            e,
        )

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )


# =========================================================
# FAVORITES LIST
# =========================================================

@router.message(
    F.text == "❤️ Sevimlilar"
)
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
            .select(
                "movie_id, movies(*)"
            )
            .eq(
                "telegram_id",
                message.from_user.id,
            )
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        logger.exception(
            "favorites list: %s",
            e,
        )

        await message.answer(
            "❌ Xatolik."
        )

        return

    movies = []

    for row in rows:

        movie = row.get("movies")

        if isinstance(movie, list):
            movie = (
                movie[0]
                if movie
                else None
            )

        if movie:
            movies.append(movie)

    if not movies:

        await message.answer(
            "❤️ Sevimlilar bo‘sh.",
            reply_markup=main_menu_kb(),
        )

        return

    text = (
        "❤️ <b>Sevimlilaringiz:</b>\n\n"
    )

    buttons = []

    for movie in movies:

        text += (
            f"🎬 {safe_html(movie['title'])}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        movie["title"]
                    )[:35],
                    callback_data=(
                        f"movie_{movie['id']}"
                    ),
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

@router.message(
    F.text == "🎭 Janrlar"
)
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
            .select("id,name")
            .order("name")
            .execute()
        )

        genres = result.data or []

    except Exception as e:

        logger.exception(
            "genres: %s",
            e,
        )

        await message.answer(
            "❌ Janrlarni olishda xatolik."
        )

        return

    if not genres:

        await message.answer(
            "Janrlar yo‘q."
        )

        return

    await message.answer(
        "🎭 <b>Janrni tanlang:</b>",
        reply_markup=genres_kb(
            genres
        ),
    )


@router.callback_query(
    F.data.startswith("genre_")
)
async def genre_movies(
    callback: CallbackQuery,
):

    try:

        genre_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

    except Exception:

        await callback.answer(
            "❌ Janr ID noto‘g‘ri.",
            show_alert=True,
        )

        return

    try:

        genre_result = (
            get_supabase()
            .table("genres")
            .select("name")
            .eq("id", genre_id)
            .limit(1)
            .execute()
        )

        if not genre_result.data:

            await callback.answer(
                "❌ Janr topilmadi.",
                show_alert=True,
            )

            return

        genre_name = genre_result.data[0][
            "name"
        ]

        result = (
            get_supabase()
            .table("movies")
            .select("*")
            .ilike(
                "genre",
                f"%{genre_name}%",
            )
            .order(
                "views",
                desc=True,
            )
            .limit(30)
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        logger.exception(
            "genre movies: %s",
            e,
        )

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

    text = (
        f"🎭 <b>{safe_html(genre_name)}</b>\n\n"
    )

    buttons = []

    for movie in rows:

        text += (
            f"🎬 {safe_html(movie['title'])} "
            f"({movie.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        movie["title"]
                    )[:35],
                    callback_data=(
                        f"movie_{movie['id']}"
                    ),
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

@router.message(
    F.text == "🔥 Eng ko‘p ko‘rilgan"
)
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
            .order(
                "views",
                desc=True,
            )
            .limit(10)
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        logger.exception(
            "popular: %s",
            e,
        )

        await message.answer(
            "❌ Xatolik."
        )

        return

    if not rows:

        await message.answer(
            "Hali kino yo‘q."
        )

        return

    text = "🔥 <b>TOP 10</b>\n\n"

    buttons = []

    for index, movie in enumerate(
        rows,
        1,
    ):

        text += (
            f"{index}. "
            f"{safe_html(movie['title'])} — "
            f"{movie.get('views', 0):,}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{index}. "
                        f"{str(movie['title'])[:30]}"
                    ),
                    callback_data=(
                        f"movie_{movie['id']}"
                    ),
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
# NEW
# =========================================================

@router.message(
    F.text == "🆕 Yangi kinolar"
)
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
            .order(
                "created_at",
                desc=True,
            )
            .limit(15)
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        logger.exception(
            "new movies: %s",
            e,
        )

        await message.answer(
            "❌ Xatolik."
        )

        return

    if not rows:

        await message.answer(
            "Hali kino yo‘q."
        )

        return

    text = "🆕 <b>Yangi kinolar</b>\n\n"

    buttons = []

    for movie in rows:

        text += (
            f"🎬 {safe_html(movie['title'])} "
            f"({movie.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        movie["title"]
                    )[:35],
                    callback_data=(
                        f"movie_{movie['id']}"
                    ),
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
# ADMIN
# =========================================================

@router.message(Command("admin"))
async def admin_panel(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    await message.answer(
        "👨‍💻 <b>ADMIN PANEL</b>",
        reply_markup=admin_menu_kb(),
    )


# =========================================================
# ADMIN ADD
# =========================================================

@router.callback_query(
    F.data == "admin_add"
)
async def admin_add_start(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer()
        return

    await state.clear()

    await state.set_state(
        AddMovie.code
    )

    await callback.message.answer(
        "➕ <b>Kino qo‘shish</b>\n\n"
        "1️⃣ Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )

    await callback.answer()


@router.message(
    StateFilter(AddMovie.code),
    F.text,
)
async def add_code(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    code = (
        message.text or ""
    ).strip()

    if not code:

        await message.answer(
            "❌ Kod bo‘sh bo‘lmasin."
        )

        return

    if len(code) > 100:

        await message.answer(
            "❌ Kod juda uzun."
        )

        return

    if await get_movie_by_code(code):

        await message.answer(
            "❌ Bu kod allaqachon mavjud."
        )

        return

    await state.update_data(
        code=code
    )

    await state.set_state(
        AddMovie.title
    )

    await message.answer(
        "2️⃣ Kino nomini yuboring:"
    )


@router.message(
    StateFilter(AddMovie.title),
    F.text,
)
async def add_title(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    title = (
        message.text or ""
    ).strip()

    if not title:

        await message.answer(
            "❌ Nom bo‘sh bo‘lmasin."
        )

        return

    await state.update_data(
        title=title
    )

    await state.set_state(
        AddMovie.alternative_title
    )

    await message.answer(
        "3️⃣ Muqobil nomni yuboring.\n"
        "Agar bo‘lmasa: <code>-</code>"
    )


@router.message(
    StateFilter(AddMovie.alternative_title),
    F.text,
)
async def add_alt(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    await state.update_data(
        alternative_title=(
            None
            if value == "-"
            else value
        )
    )

    await state.set_state(
        AddMovie.description
    )

    await message.answer(
        "4️⃣ Tavsifni yuboring.\n"
        "Agar bo‘lmasa: <code>-</code>"
    )


@router.message(
    StateFilter(AddMovie.description),
    F.text,
)
async def add_description(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    await state.update_data(
        description=(
            None
            if value == "-"
            else value
        )
    )

    await state.set_state(
        AddMovie.year
    )

    await message.answer(
        "5️⃣ Yilni yuboring.\n"
        "Agar bo‘lmasa: <code>-</code>"
    )


@router.message(
    StateFilter(AddMovie.year),
    F.text,
)
async def add_year(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    year = None

    if value != "-":

        try:

            year = int(value)

            if (
                year < 1800
                or year > datetime.now().year + 2
            ):

                await message.answer(
                    "❌ Yil noto‘g‘ri."
                )

                return

        except ValueError:

            await message.answer(
                "❌ Yil faqat raqam bo‘lsin."
            )

            return

    await state.update_data(
        year=year
    )

    await state.set_state(
        AddMovie.genre
    )

    await message.answer(
        "6️⃣ Janrni yuboring:"
    )


@router.message(
    StateFilter(AddMovie.genre),
    F.text,
)
async def add_genre(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    genre = (
        message.text or ""
    ).strip()

    if not genre:

        await message.answer(
            "❌ Janr bo‘sh bo‘lmasin."
        )

        return

    await state.update_data(
        genre=genre
    )

    await state.set_state(
        AddMovie.rating
    )

    await message.answer(
        "7️⃣ Reytingni yuboring.\n"
        "Masalan: <code>8.5</code>\n"
        "Yoki: <code>-</code>"
    )


@router.message(
    StateFilter(AddMovie.rating),
    F.text,
)
async def add_rating(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    rating = None

    if value != "-":

        try:

            rating = float(
                value.replace(
                    ",",
                    ".",
                )
            )

            if not 0 <= rating <= 10:

                await message.answer(
                    "❌ Reyting 0–10 oralig‘ida."
                )

                return

        except ValueError:

            await message.answer(
                "❌ Reyting noto‘g‘ri."
            )

            return

    await state.update_data(
        rating=rating
    )

    await state.set_state(
        AddMovie.channel_id
    )

    await message.answer(
        "8️⃣ Kanal ID yuboring:\n\n"
        "<code>-1001234567890</code>"
    )


@router.message(
    StateFilter(AddMovie.channel_id),
    F.text,
)
async def add_channel(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    try:

        channel_id = int(
            (
                message.text or ""
            ).strip()
        )

    except ValueError:

        await message.answer(
            "❌ Channel ID noto‘g‘ri."
        )

        return

    await state.update_data(
        channel_id=channel_id
    )

    await state.set_state(
        AddMovie.message_id
    )

    await message.answer(
        "9️⃣ Kanal postining Message ID'sini yuboring:"
    )


@router.message(
    StateFilter(AddMovie.message_id),
    F.text,
)
async def add_message_id(
    message: Message,
    state: FSMContext,
    bot: Bot,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    try:

        message_id = int(
            (
                message.text or ""
            ).strip()
        )

    except ValueError:

        await message.answer(
            "❌ Message ID noto‘g‘ri."
        )

        return

    data = await state.get_data()

    channel_id = data.get(
        "channel_id"
    )

    if not channel_id:

        await state.clear()

        await message.answer(
            "❌ Kanal ID topilmadi."
        )

        return

    await message.answer(
        "🔎 Kino xabari tekshirilmoqda..."
    )

    valid = await validate_source_message(
        bot=bot,
        channel_id=int(channel_id),
        message_id=message_id,
        admin_id=message.from_user.id,
    )

    if not valid:

        await message.answer(
            "❌ Bu kanal/post mavjud emas "
            "yoki bot uni nusxalay olmaydi.\n\n"
            "Bot kanal admini ekanini va "
            "Message ID to‘g‘ri ekanini tekshiring."
        )

        return

    await state.update_data(
        message_id=message_id
    )

    data = await state.get_data()

    await finish_add(
        message,
        state,
        data,
        fast=False,
    )


# =========================================================
# FAST ADD /add
# =========================================================

@router.message(Command("add"))
async def fast_add_start(
    message: Message,
    state: FSMContext,
    bot: Bot,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    replied = message.reply_to_message

    if replied is None:

        await message.answer(
            "❌ Kanal kino postiga <b>Reply</b> qiling "
            "va keyin <code>/add</code> yuboring."
        )

        return

    channel_id = replied.chat.id
    message_id = replied.message_id

    await message.answer(
        "🔎 Kanal xabari tekshirilmoqda..."
    )

    valid = await validate_source_message(
        bot=bot,
        channel_id=channel_id,
        message_id=message_id,
        admin_id=message.from_user.id,
    )

    if not valid:

        await message.answer(
            "❌ Bu xabarni bot nusxalay olmaydi.\n"
            "Bot kanal admini ekanini tekshiring."
        )

        return

    await state.clear()

    await state.update_data(
        channel_id=channel_id,
        message_id=message_id,
    )

    await state.set_state(
        FastAddMovie.code
    )

    await message.answer(
        "➕ <b>Tez qo‘shish</b>\n\n"
        "1️⃣ Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(
    StateFilter(FastAddMovie.code),
    F.text,
)
async def fast_code(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    code = (
        message.text or ""
    ).strip()

    if not code:

        await message.answer(
            "❌ Kod bo‘sh bo‘lmasin."
        )

        return

    if len(code) > 100:

        await message.answer(
            "❌ Kod juda uzun."
        )

        return

    if await get_movie_by_code(code):

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
        "2️⃣ Kino nomini yuboring:"
    )


@router.message(
    StateFilter(FastAddMovie.title),
    F.text,
)
async def fast_title(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    title = (
        message.text or ""
    ).strip()

    if not title:

        await message.answer(
            "❌ Nom bo‘sh bo‘lmasin."
        )

        return

    await state.update_data(
        title=title
    )

    await state.set_state(
        FastAddMovie.alternative_title
    )

    await message.answer(
        "3️⃣ Muqobil nom.\n"
        "Agar bo‘lmasa: <code>-</code>"
    )


@router.message(
    StateFilter(FastAddMovie.alternative_title),
    F.text,
)
async def fast_alt(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    await state.update_data(
        alternative_title=(
            None
            if value == "-"
            else value
        )
    )

    await state.set_state(
        FastAddMovie.description
    )

    await message.answer(
        "4️⃣ Tavsif.\n"
        "Agar bo‘lmasa: <code>-</code>"
    )


@router.message(
    StateFilter(FastAddMovie.description),
    F.text,
)
async def fast_description(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    await state.update_data(
        description=(
            None
            if value == "-"
            else value
        )
    )

    await state.set_state(
        FastAddMovie.year
    )

    await message.answer(
        "5️⃣ Yil.\n"
        "Agar bo‘lmasa: <code>-</code>"
    )


@router.message(
    StateFilter(FastAddMovie.year),
    F.text,
)
async def fast_year(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    year = None

    if value != "-":

        try:

            year = int(value)

            if (
                year < 1800
                or year > datetime.now().year + 2
            ):

                await message.answer(
                    "❌ Yil noto‘g‘ri."
                )

                return

        except ValueError:

            await message.answer(
                "❌ Yil faqat raqam."
            )

            return

    await state.update_data(
        year=year
    )

    await state.set_state(
        FastAddMovie.genre
    )

    await message.answer(
        "6️⃣ Janr:"
    )


@router.message(
    StateFilter(FastAddMovie.genre),
    F.text,
)
async def fast_genre(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    genre = (
        message.text or ""
    ).strip()

    if not genre:

        await message.answer(
            "❌ Janr bo‘sh bo‘lmasin."
        )

        return

    await state.update_data(
        genre=genre
    )

    await state.set_state(
        FastAddMovie.rating
    )

    await message.answer(
        "7️⃣ Reyting.\n"
        "Masalan: <code>8.5</code>\n"
        "Yoki: <code>-</code>"
    )


@router.message(
    StateFilter(FastAddMovie.rating),
    F.text,
)
async def fast_rating(
    message: Message,
    state: FSMContext,
):

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    value = (
        message.text or ""
    ).strip()

    rating = None

    if value != "-":

        try:

            rating = float(
                value.replace(
                    ",",
                    ".",
                )
            )

            if not 0 <= rating <= 10:

                await message.answer(
                    "❌ Reyting 0–10."
                )

                return

        except ValueError:

            await message.answer(
                "❌ Reyting noto‘g‘ri."
            )

            return

    await state.update_data(
        rating=rating
    )

    data = await state.get_data()

    await finish_add(
        message,
        state,
        data,
        fast=True,
    )


# =========================================================
# ADMIN LIST
# =========================================================

@router.callback_query(
    F.data == "admin_list"
)
@router.message(Command("movies"))
async def admin_list(
    event: Union[Message, CallbackQuery],
):

    if not is_admin(
        event.from_user.id
    ):
        return

    try:

        result = (
            get_supabase()
            .table("movies")
            .select(
                "id,code,title,"
                "channel_id,message_id,"
                "views,year,genre,rating"
            )
            .order(
                "id",
                desc=True,
            )
            .limit(50)
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        logger.exception(
            "admin list: %s",
            e,
        )

        if isinstance(
            event,
            CallbackQuery,
        ):

            await event.answer(
                "❌ Xatolik.",
                show_alert=True,
            )

        else:

            await event.answer(
                "❌ Xatolik."
            )

        return

    if not rows:

        text = "📋 Bazada kino yo‘q."

    else:

        chunks = []

        for movie in rows:

            chunks.append(
                "━━━━━━━━━━━━━━\n"
                f"🆔 <code>{movie['id']}</code>\n"
                f"🎬 <b>{safe_html(movie['title'])}</b>\n"
                f"🔢 <code>{safe_html(movie['code'])}</code>\n"
                f"📢 <code>{movie['channel_id']}</code>\n"
                f"💬 <code>{movie['message_id']}</code>\n"
                f"📅 {movie.get('year') or '—'}\n"
                f"🎭 {safe_html(movie.get('genre') or '—')}\n"
                f"⭐ {movie.get('rating') if movie.get('rating') is not None else '—'}\n"
                f"👀 {movie.get('views', 0):,}"
            )

        # Telegram limitini buzmaslik uchun alohida xabarlar
        current = "📋 <b>KINOLAR</b>\n\n"

        messages = []

        for chunk in chunks:

            if len(current) + len(chunk) > 3900:

                messages.append(current)

                current = ""

            current += chunk + "\n"

        if current:
            messages.append(current)

        if isinstance(
            event,
            CallbackQuery,
        ):

            for msg in messages:

                await event.message.answer(
                    msg
                )

            await event.answer()

        else:

            for msg in messages:

                await event.answer(
                    msg
                )

        return

    # Bu joy yuqoridagi else'da qaytarilgan.
    return


# =========================================================
# ADMIN STATS
# =========================================================

@router.callback_query(
    F.data == "admin_stats"
)
@router.message(Command("stats"))
async def admin_stats(
    event: Union[Message, CallbackQuery],
):

    if not is_admin(
        event.from_user.id
    ):
        return

    try:

        users = (
            get_supabase()
            .table("users")
            .select(
                "id",
                count="exact",
            )
            .execute()
        )

        movies = (
            get_supabase()
            .table("movies")
            .select(
                "id",
                count="exact",
            )
            .execute()
        )

        views = (
            get_supabase()
            .table("movies")
            .select("views")
            .execute()
        )

        total_views = sum(
            (
                row.get("views") or 0
            )
            for row in (
                views.data or []
            )
        )

        today = (
            datetime.now(
                timezone.utc
            )
            .date()
            .isoformat()
        )

        today_users = (
            get_supabase()
            .table("users")
            .select(
                "id",
                count="exact",
            )
            .gte(
                "last_activity",
                f"{today}T00:00:00+00:00",
            )
            .execute()
        )

        top = (
            get_supabase()
            .table("movies")
            .select(
                "title,views"
            )
            .order(
                "views",
                desc=True,
            )
            .limit(1)
            .execute()
        )

        top_movie = (
            top.data[0]
            if top.data
            else None
        )

        top_text = (
            f"{safe_html(top_movie['title'])} "
            f"({top_movie.get('views', 0):,})"
            if top_movie
            else "—"
        )

        text = (
            "📊 <b>STATISTIKA</b>\n\n"
            f"👥 Foydalanuvchilar: "
            f"{users.count or 0}\n"
            f"🎬 Kinolar: "
            f"{movies.count or 0}\n"
            f"👀 Jami ko‘rishlar: "
            f"{total_views:,}\n"
            f"🟢 Bugungi faol: "
            f"{today_users.count or 0}\n"
            f"🔥 Top kino: {top_text}"
        )

    except Exception as e:

        logger.exception(
            "stats: %s",
            e,
        )

        text = "❌ Statistikani olishda xatolik."

    if isinstance(
        event,
        CallbackQuery,
    ):

        await event.message.answer(
            text
        )

        await event.answer()

    else:

        await event.answer(
            text
        )


# =========================================================
# ADMIN USERS
# =========================================================

@router.callback_query(
    F.data == "admin_users"
)
@router.message(Command("users"))
async def admin_users(
    event: Union[Message, CallbackQuery],
):

    if not is_admin(
        event.from_user.id
    ):
        return

    try:

        total = (
            get_supabase()
            .table("users")
            .select(
                "id",
                count="exact",
            )
            .execute()
        )

        blocked = (
            get_supabase()
            .table("users")
            .select(
                "id",
                count="exact",
            )
            .eq(
                "is_blocked",
                True,
            )
            .execute()
        )

        text = (
            "👥 <b>FOYDALANUVCHILAR</b>\n\n"
            f"👤 Jami: {total.count or 0}\n"
            f"🚫 Bloklagan: "
            f"{blocked.count or 0}"
        )

    except Exception as e:

        logger.exception(
            "users: %s",
            e,
        )

        text = "❌ Xatolik."

    if isinstance(
        event,
        CallbackQuery,
    ):

        await event.message.answer(
            text
        )

        await event.answer()

    else:

        await event.answer(
            text
        )


# =========================================================
# DELETE
# =========================================================

@router.callback_query(
    F.data == "admin_delete"
)
async def admin_delete_start(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    await state.clear()

    await state.set_state(
        AdminDelete.waiting_code
    )

    await callback.message.answer(
        "🗑 O‘chirmoqchi bo‘lgan kino "
        "kodini yuboring:",
        reply_markup=cancel_kb(),
    )

    await callback.answer()


@router.message(
    StateFilter(AdminDelete.waiting_code),
    F.text,
)
async def admin_delete_confirm(
    message: Message,
    state: FSMContext,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    code = (
        message.text or ""
    ).strip()

    movie = await get_movie_by_code(
        code
    )

    if not movie:

        await message.answer(
            "❌ Kino topilmadi."
        )

        return

    await state.update_data(
        movie_id=movie["id"]
    )

    await message.answer(
        "⚠️ <b>Rostdan o‘chirmoqchimisiz?</b>\n\n"
        f"🎬 {safe_html(movie['title'])}\n"
        f"🔢 {safe_html(movie['code'])}",
        reply_markup=confirm_delete_kb(
            movie["id"]
        ),
    )


@router.callback_query(
    F.data.startswith("del_yes_")
)
async def delete_yes(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    try:

        movie_id = int(
            callback.data.split(
                "_"
            )[2]
        )

        (
            get_supabase()
            .table("favorites")
            .delete()
            .eq(
                "movie_id",
                movie_id,
            )
            .execute()
        )

        result = (
            get_supabase()
            .table("movies")
            .delete()
            .eq(
                "id",
                movie_id,
            )
            .execute()
        )

        await state.clear()

        if result.data:

            await callback.message.edit_text(
                "✅ Kino o‘chirildi."
            )

        else:

            await callback.message.edit_text(
                "❌ Kino topilmadi."
            )

        await callback.answer()

    except Exception as e:

        logger.exception(
            "delete: %s",
            e,
        )

        await callback.answer(
            "❌ O‘chirishda xatolik.",
            show_alert=True,
        )


@router.callback_query(
    F.data == "del_no"
)
async def delete_no(
    callback: CallbackQuery,
    state: FSMContext,
):

    await state.clear()

    await callback.message.edit_text(
        "❌ O‘chirish bekor qilindi."
    )

    await callback.answer()


# =========================================================
# EDIT
# =========================================================

@router.callback_query(
    F.data == "admin_edit"
)
async def admin_edit_start(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    await state.clear()

    await state.set_state(
        AdminEdit.waiting_code
    )

    await callback.message.answer(
        "✏️ Tahrir qilinadigan kino "
        "kodini yuboring:",
        reply_markup=cancel_kb(),
    )

    await callback.answer()


@router.message(
    StateFilter(AdminEdit.waiting_code),
    F.text,
)
async def admin_edit_code(
    message: Message,
    state: FSMContext,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    code = (
        message.text or ""
    ).strip()

    movie = await get_movie_by_code(
        code
    )

    if not movie:

        await message.answer(
            "❌ Kino topilmadi."
        )

        return

    await state.update_data(
        movie_id=movie["id"]
    )

    await message.answer(
        "✏️ Qaysi maydonni tahrirlaysiz?\n\n"
        f"🎬 {safe_html(movie['title'])}",
        reply_markup=edit_fields_kb(),
    )


@router.callback_query(
    F.data.startswith("edit_")
)
async def edit_field(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    if callback.data == "edit_cancel":

        await state.clear()

        await callback.message.edit_text(
            "❌ Tahrirlash bekor qilindi."
        )

        await callback.answer()

        return

    field = callback.data.replace(
        "edit_",
        "",
        1,
    )

    field_map = {
        "title": "title",
        "alt": "alternative_title",
        "desc": "description",
        "year": "year",
        "genre": "genre",
        "rating": "rating",
        "code": "code",
    }

    if field not in field_map:

        await callback.answer(
            "❌ Noma'lum maydon.",
            show_alert=True,
        )

        return

    await state.update_data(
        edit_field=field_map[field]
    )

    await state.set_state(
        AdminEdit.waiting_value
    )

    await callback.message.answer(
        "✏️ Yangi qiymatni yuboring.\n\n"
        "Maydonni o‘chirish uchun: <code>-</code>",
        reply_markup=cancel_kb(),
    )

    await callback.answer()


@router.message(
    StateFilter(AdminEdit.waiting_value),
    F.text,
)
async def edit_value(
    message: Message,
    state: FSMContext,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    data = await state.get_data()

    movie_id = data.get(
        "movie_id"
    )

    field = data.get(
        "edit_field"
    )

    if not movie_id or not field:

        await state.clear()

        await message.answer(
            "❌ Tahrirlash sessiyasi buzilgan."
        )

        return

    value = (
        message.text or ""
    ).strip()

    # CODE
    if field == "code":

        if value == "-":

            await message.answer(
                "❌ Kino kodi bo‘sh bo‘lishi mumkin emas."
            )

            return

        other = await get_movie_by_code(
            value
        )

        if other and other["id"] != movie_id:

            await message.answer(
                "❌ Bu kod boshqa kinoda ishlatilgan."
            )

            return

    # YEAR
    if field == "year":

        if value == "-":

            value = None

        else:

            try:

                value = int(value)

                if (
                    value < 1800
                    or value > datetime.now().year + 2
                ):

                    await message.answer(
                        "❌ Yil noto‘g‘ri."
                    )

                    return

            except ValueError:

                await message.answer(
                    "❌ Yil raqam bo‘lishi kerak."
                )

                return

    # RATING
    if field == "rating":

        if value == "-":

            value = None

        else:

            try:

                value = float(
                    value.replace(
                        ",",
                        ".",
                    )
                )

                if not 0 <= value <= 10:

                    await message.answer(
                        "❌ Reyting 0–10."
                    )

                    return

            except ValueError:

                await message.answer(
                    "❌ Reyting noto‘g‘ri."
                )

                return

    # ALT/DESC
    if field in (
        "alternative_title",
        "description",
    ):

        if value == "-":
            value = None

    try:

        (
            get_supabase()
            .table("movies")
            .update(
                {
                    field: value
                }
            )
            .eq(
                "id",
                movie_id,
            )
            .execute()
        )

        await state.clear()

        await message.answer(
            "✅ Kino muvaffaqiyatli tahrirlandi.",
            reply_markup=main_menu_kb(),
        )

    except Exception as e:

        logger.exception(
            "edit movie: %s",
            e,
        )

        await message.answer(
            "❌ Tahrirlashda xatolik."
        )


# =========================================================
# BROADCAST
# =========================================================

@router.callback_query(
    F.data == "admin_broadcast"
)
@router.message(Command("broadcast"))
async def broadcast_start(
    event: Union[Message, CallbackQuery],
    state: FSMContext,
):

    if not is_admin(
        event.from_user.id
    ):
        return

    await state.clear()

    await state.set_state(
        Broadcast.waiting
    )

    text = (
        "📢 <b>Reklama yuborish</b>\n\n"
        "Reklama xabarini shu yerga yuboring.\n\n"
        "✅ Matn\n"
        "✅ Rasm\n"
        "✅ Video\n"
        "✅ Document\n"
        "✅ Audio\n"
        "✅ Animation\n\n"
        "Bot xabarni foydalanuvchilarga "
        "aynan o‘sha formatda yuboradi.\n\n"
        "❌ Bekor qilish: /cancel"
    )

    if isinstance(
        event,
        CallbackQuery,
    ):

        await event.message.answer(
            text,
            reply_markup=cancel_kb(),
        )

        await event.answer()

    else:

        await event.answer(
            text,
            reply_markup=cancel_kb(),
        )


@router.message(Command("cancel"))
async def cancel_any(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        "❌ Bekor qilindi.",
        reply_markup=main_menu_kb(),
    )


async def send_copy_with_retry(
    bot: Bot,
    from_chat_id: int,
    message_id: int,
    to_chat_id: int,
) -> bool:

    for attempt in range(3):

        try:

            await bot.copy_message(
                chat_id=to_chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )

            return True

        except TelegramRetryAfter as e:

            wait_time = (
                e.retry_after + 1
            )

            logger.warning(
                "Flood limit. %s sekund kutish.",
                wait_time,
            )

            await asyncio.sleep(
                wait_time
            )

        except TelegramForbiddenError:

            raise

        except Exception as e:

            if attempt == 2:

                logger.warning(
                    "copy failed: %s",
                    e,
                )

                return False

            await asyncio.sleep(
                1 + attempt
            )

    return False


@router.message(
    StateFilter(Broadcast.waiting)
)
async def broadcast_process(
    message: Message,
    state: FSMContext,
    bot: Bot,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    if is_cancel(message.text):

        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )

        return

    await state.clear()

    try:

        result = (
            get_supabase()
            .table("users")
            .select("telegram_id")
            .eq(
                "is_blocked",
                False,
            )
            .execute()
        )

        users = [
            row["telegram_id"]
            for row in (
                result.data or []
            )
            if row.get("telegram_id")
        ]

    except Exception as e:

        logger.exception(
            "broadcast users: %s",
            e,
        )

        await message.answer(
            "❌ Foydalanuvchilarni olishda xatolik."
        )

        return

    if not users:

        await message.answer(
            "❌ Reklama yuboriladigan "
            "foydalanuvchilar yo‘q."
        )

        return

    status = await message.answer(
        "📢 <b>Reklama yuborilmoqda...</b>\n\n"
        f"👥 Jami: {len(users)}"
    )

    success = 0
    failed = 0
    blocked = 0

    for index, user_id in enumerate(
        users,
        1,
    ):

        try:

            ok = await send_copy_with_retry(
                bot=bot,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                to_chat_id=user_id,
            )

            if ok:

                success += 1

            else:

                failed += 1

        except TelegramForbiddenError:

            blocked += 1

            try:

                (
                    get_supabase()
                    .table("users")
                    .update(
                        {
                            "is_blocked": True
                        }
                    )
                    .eq(
                        "telegram_id",
                        user_id,
                    )
                    .execute()
                )

            except Exception as e:

                logger.warning(
                    "blocked update: %s",
                    e,
                )

        except Exception as e:

            failed += 1

            logger.warning(
                "broadcast user %s: %s",
                user_id,
                e,
            )

        # Telegram flood limitini kamaytirish
        await asyncio.sleep(
            0.08
        )

        if index % 25 == 0:

            try:

                await status.edit_text(
                    "📢 <b>Reklama yuborilmoqda...</b>\n\n"
                    f"📨 {index}/{len(users)}\n"
                    f"✅ {success}\n"
                    f"❌ {failed}\n"
                    f"🚫 {blocked}"
                )

            except TelegramBadRequest:
                pass

    await message.answer(
        "📢 <b>Reklama yakunlandi!</b>\n\n"
        f"📨 Jami: {len(users)}\n"
        f"✅ Yetkazildi: {success}\n"
        f"❌ Xato: {failed}\n"
        f"🚫 Bloklagan: {blocked}",
        reply_markup=main_menu_kb(),
    )


# =========================================================
# FORCE SUB INFO
# =========================================================

@router.callback_query(
    F.data == "admin_force_sub"
)
async def admin_force_info(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    await callback.message.answer(
        "📢 <b>Majburiy obuna</b>\n\n"
        f"🆔 Channel ID: "
        f"<code>{CHANNEL_ID}</code>\n"
        f"🔗 Username: "
        f"{safe_html(CHANNEL_USERNAME or '—')}\n\n"
        "⚠️ Bot kanal admini bo‘lishi kerak."
    )

    await callback.answer()


# =========================================================
# SETTINGS
# =========================================================

@router.callback_query(
    F.data == "admin_settings"
)
async def admin_settings(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        return

    await callback.message.answer(
        "⚙️ <b>SOZLAMALAR</b>\n\n"
        f"🤖 BOT_TOKEN: "
        f"{'✅' if BOT_TOKEN else '❌'}\n"
        f"👨‍💻 ADMIN_IDS: "
        f"<code>{ADMIN_IDS}</code>\n"
        f"📢 CHANNEL_ID: "
        f"<code>{CHANNEL_ID}</code>\n"
        f"📢 CHANNEL_USERNAME: "
        f"{safe_html(CHANNEL_USERNAME or '—')}\n"
        f"💬 SUPPORT: "
        f"{safe_html(SUPPORT_USERNAME)}\n"
        "🗄 DATABASE: Supabase ✅"
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

    current_state = await state.get_state()

    if current_state is not None:
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

    query = sanitize_query(
        message.text or ""
    )

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
            .order(
                "views",
                desc=True,
            )
            .limit(15)
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        logger.exception(
            "fallback search: %s",
            e,
        )

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

        fav = await is_favorite(
            message.from_user.id,
            movie["id"],
        )

        await message.answer(
            format_movie_card(movie),
            reply_markup=movie_actions_kb(
                movie["id"],
                fav,
            ),
        )

        return

    text = "🔍 <b>Natijalar:</b>\n\n"

    buttons = []

    for movie in rows:

        text += (
            f"🎬 {safe_html(movie['title'])}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        movie["title"]
                    )[:35],
                    callback_data=(
                        f"movie_{movie['id']}"
                    ),
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

    # -------------------------
    # ENV CHECK
    # -------------------------

    if not BOT_TOKEN:

        print(
            "ERROR: BOT_TOKEN mavjud emas!"
        )

        return

    if not ADMIN_IDS:

        print(
            "ERROR: ADMIN_IDS mavjud emas!"
        )

        return

    if (
        not SUPABASE_URL
        or not SUPABASE_KEY
    ):

        print(
            "ERROR: SUPABASE_URL yoki "
            "SUPABASE_KEY mavjud emas!"
        )

        return

    # -------------------------
    # SUPABASE
    # -------------------------

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY,
        )

        await ensure_default_genres()

        logger.info(
            "Supabase muvaffaqiyatli ulandi."
        )

    except Exception as e:

        logger.exception(
            "Supabase connection error: %s",
            e,
        )

        return

    # -------------------------
    # BOT
    # -------------------------

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher(
        storage=MemoryStorage()
    )

    dp.include_router(
        router
    )

    logger.info(
        "Kino Bot ishga tushdi."
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        await bot.session.close()


if __name__ == "__main__":

    asyncio.run(
        main()
)
