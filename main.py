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

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

logger = logging.getLogger("kino_bot")

supabase: Optional[Client] = None


def get_sb() -> Client:
    if supabase is None:
        raise RuntimeError("Supabase ulanmagan.")
    return supabase


class AddMovie(StatesGroup):
    code = State()
    title = State()
    genre = State()
    extra = State()


class SearchCode(StatesGroup):
    waiting = State()


class SearchTitle(StatesGroup):
    waiting = State()


class AdminDelete(StatesGroup):
    confirm = State()


class Broadcast(StatesGroup):
    waiting = State()


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🎬 Kino qidirish"),
                KeyboardButton(text="🔢 Kod orqali"),
            ],
            [
                KeyboardButton(text="🎭 Janrlar"),
                KeyboardButton(text="🔥 TOP"),
            ],
            [
                KeyboardButton(text="🆕 Yangilar"),
                KeyboardButton(text="❤️ Sevimlilar"),
            ],
            [
                KeyboardButton(text="ℹ️ Yordam"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Kino nomi yoki kod yozing...",
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="❌ Bekor qilish"),
            ]
        ],
        resize_keyboard=True,
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Tez qo'shish",
                    callback_data="admin_fast_help",
                ),
                InlineKeyboardButton(
                    text="🗑 O'chirish",
                    callback_data="admin_delete",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Ro'yxat",
                    callback_data="admin_list",
                ),
                InlineKeyboardButton(
                    text="📊 Statistika",
                    callback_data="admin_stats",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Foydalanuvchilar",
                    callback_data="admin_users",
                ),
                InlineKeyboardButton(
                    text="📢 Reklama",
                    callback_data="admin_broadcast",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 Majburiy obuna",
                    callback_data="admin_force_sub",
                ),
                InlineKeyboardButton(
                    text="⚙️ Sozlamalar",
                    callback_data="admin_settings",
                ),
            ],
        ]
    )


def movie_actions_kb(
    movie_id: int,
    is_fav: bool = False,
) -> InlineKeyboardMarkup:
    if is_fav:
        fav_text = "💔 Olib tashlash"
        fav_data = f"unfav_{movie_id}"
    else:
        fav_text = "❤️ Sevimli"
        fav_data = f"fav_{movie_id}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Tomosha qilish",
                    callback_data=f"watch_{movie_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=fav_text,
                    callback_data=fav_data,
                ),
                InlineKeyboardButton(
                    text="🏠 Menyuga",
                    callback_data="back_main",
                ),
            ],
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
            [
                InlineKeyboardButton(
                    text="📢 Kanalga obuna bo'lish",
                    url=link,
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Tekshirdim",
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
                    text="✅ Ha, o'chir",
                    callback_data=f"del_yes_{movie_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Yo'q",
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
                text=genre[:30],
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
                text="🏠 Menyuga",
                callback_data="back_main",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def skip_extra_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ O'tkazib yuborish",
                    callback_data="skip_extra",
                )
            ]
        ]
    )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe(value: Any) -> str:
    if value is None:
        return ""
    return escape(str(value))


def sanitize(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"[%_\\]", "", value)
    return value[:100]


def user_name(obj: Union[Message, CallbackQuery]) -> str:
    user = obj.from_user

    if not user:
        return "Do'st"

    return safe(
        user.first_name
        or user.username
        or "Do'st"
    )


def greeting(name: str) -> str:
    hour = datetime.now().hour

    if 5 <= hour < 12:
        part = "Xayrli tong"
    elif 12 <= hour < 17:
        part = "Xayrli kun"
    elif 17 <= hour < 22:
        part = "Xayrli kech"
    else:
        part = "Xayrli tun"

    return f"{part}, <b>{name}</b>! 👋"


async def register_user(message: Message) -> None:
    user = message.from_user

    if not user:
        return

    try:
        sb = get_sb()

        result = (
            sb.table("users")
            .select("id")
            .eq("telegram_id", user.id)
            .limit(1)
            .execute()
        )

        data = {
            "username": user.username,
            "first_name": user.first_name,
            "last_activity": now_iso(),
            "is_blocked": False,
        }

        if result.data:
            (
                sb.table("users")
                .update(data)
                .eq("telegram_id", user.id)
                .execute()
            )
        else:
            (
                sb.table("users")
                .insert(
                    {
                        "telegram_id": user.id,
                        **data,
                    }
                )
                .execute()
            )

    except Exception as e:
        logger.error("register_user: %s", e)


async def check_sub(bot: Bot, user_id: int) -> bool:
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

    except TelegramBadRequest as e:
        logger.warning("check_sub bad request: %s", e)

        text = str(e).lower()

        if (
            "user not found" in text
            or "chat not found" in text
        ):
            return False

        return False

    except Exception as e:
        logger.warning("check_sub error: %s", e)
        return False


async def movie_by_code(
    code: str,
) -> Optional[Dict[str, Any]]:
    try:
        result = (
            get_sb()
            .table("movies")
            .select("*")
            .eq("code", code)
            .limit(1)
            .execute()
        )

        return result.data[0] if result.data else None

    except Exception as e:
        logger.error("movie_by_code: %s", e)
        return None


async def movie_by_id(
    movie_id: int,
) -> Optional[Dict[str, Any]]:
    try:
        result = (
            get_sb()
            .table("movies")
            .select("*")
            .eq("id", movie_id)
            .limit(1)
            .execute()
        )

        return result.data[0] if result.data else None

    except Exception as e:
        logger.error("movie_by_id: %s", e)
        return None


async def inc_views(movie_id: int) -> None:
    try:
        get_sb().rpc(
            "increment_movie_views",
            {
                "p_movie_id": movie_id,
            },
        ).execute()

    except Exception as e:
        logger.error(
            "inc_views: %s",
            e,
        )


async def is_fav(
    telegram_id: int,
    movie_id: int,
) -> bool:
    try:
        result = (
            get_sb()
            .table("favorites")
            .select("id")
            .eq("telegram_id", telegram_id)
            .eq("movie_id", movie_id)
            .limit(1)
            .execute()
        )

        return bool(result.data)

    except Exception as e:
        logger.error("is_fav: %s", e)
        return False


def movie_card(movie: Dict[str, Any]) -> str:
    title = safe(
        movie.get("title")
        or "Noma'lum"
    )

    alternative_title = movie.get(
        "alternative_title"
    )

    year = movie.get("year") or "—"

    genre = safe(
        movie.get("genre")
        or "—"
    )

    rating = movie.get("rating")

    views = movie.get("views") or 0

    description = movie.get(
        "description"
    ) or ""

    code = safe(
        movie.get("code")
        or "—"
    )

    rating_text = (
        str(rating)
        if rating is not None
        else "—"
    )

    lines = [
        f"🎬 <b>{title}</b>",
    ]

    if alternative_title:
        lines.append(
            f"📌 <i>{safe(alternative_title)}</i>"
        )

    lines.append("")

    lines.append(
        f"🔢 Kod: <code>{code}</code>"
    )

    lines.append(
        f"📅 {year}  ·  🎭 {genre}"
    )

    lines.append(
        f"⭐ {rating_text}  ·  👁 {views:,}"
    )

    if description:
        lines.append("")
        lines.append(
            f"📝 {safe(description)}"
        )

    return "\n".join(lines)


async def ensure_genres() -> None:
    genres = [
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
        "Crime",
        "Adventure",
        "Mystery",
        "War",
        "History",
    ]

    for genre in genres:
        try:
            result = (
                get_sb()
                .table("genres")
                .select("id")
                .eq("name", genre)
                .limit(1)
                .execute()
            )

            if not result.data:
                (
                    get_sb()
                    .table("genres")
                    .insert(
                        {
                            "name": genre,
                        }
                    )
                    .execute()
                )

        except Exception as e:
            logger.warning(
                "ensure_genres %s: %s",
                genre,
                e,
            )


async def save_movie(
    data: Dict[str, Any],
) -> tuple[bool, str]:

    try:
        result = (
            get_sb()
            .table("movies")
            .insert(data)
            .execute()
        )

        if not result.data:
            return False, "Insert javobi bo'sh."

        return True, ""

    except Exception as e:
        logger.exception("save_movie")
        return False, str(e)


async def finish_add(
    message: Message,
    state: FSMContext,
    data: Dict[str, Any],
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
        await message.answer(
            "❌ Ma'lumot yetishmayapti.\n\n"
            f"<code>{safe(', '.join(missing))}</code>\n\n"
            "Qaytadan /add bilan boshlang."
        )

        await state.clear()
        return

    payload = {
        "code": str(
            data["code"]
        ).strip(),

        "title": str(
            data["title"]
        ).strip(),

        "alternative_title": data.get(
            "alternative_title"
        ),

        "description": data.get(
            "description"
        ),

        "year": data.get(
            "year"
        ),

        "genre": str(
            data["genre"]
        ).strip(),

        "rating": data.get(
            "rating"
        ),

        "channel_id": int(
            data["channel_id"]
        ),

        "message_id": int(
            data["message_id"]
        ),

        "views": 0,
    }

    existing = await movie_by_code(
        payload["code"]
    )

    if existing:
        await message.answer(
            "❌ Bu kod allaqachon mavjud.\n\n"
            f"🔢 Kod: <code>{safe(payload['code'])}</code>\n"
            f"🎬 Kino: <b>{safe(existing.get('title'))}</b>\n\n"
            "Boshqa kod yuboring."
        )
        return

    ok, error = await save_movie(
        payload
    )

    if not ok:
        await message.answer(
            "❌ Kino bazaga qo'shilmadi.\n\n"
            f"<code>{safe(error[:1500])}</code>"
        )
        return

    await state.clear()

    await message.answer(
        "✅ <b>Kino muvaffaqiyatli qo'shildi!</b>\n\n"
        f"🔢 Kod: <code>{safe(payload['code'])}</code>\n"
        f"🎬 Nomi: <b>{safe(payload['title'])}</b>\n"
        f"🎭 Janr: {safe(payload['genre'])}\n"
        f"📢 Kanal: <code>{payload['channel_id']}</code>\n"
        f"💬 Message ID: <code>{payload['message_id']}</code>",
        reply_markup=admin_menu_kb(),
    )


router = Router()


@router.message(Command("start"))
async def cmd_start(
    message: Message,
    bot: Bot,
    state: FSMContext,
) -> None:

    await state.clear()
    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            f"{greeting(user_name(message))}\n\n"
            "📢 Botdan foydalanish uchun "
            "kanalimizga obuna bo'ling.\n\n"
            "Obuna bo'lgach "
            "<b>✅ Tekshirdim</b> tugmasini bosing.",
            reply_markup=force_sub_kb(),
        )
        return

    await message.answer(
        f"{greeting(user_name(message))}\n\n"
        "🎬 <b>Kino Bot</b>ga xush kelibsiz!\n\n"
        "Kinolarni kod yoki nom orqali topishingiz mumkin.\n\n"
        "Menyudan foydalaning yoki kino nomini "
        "to'g'ridan-to'g'ri yozing.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Yordam")
async def cmd_help(
    message: Message,
) -> None:

    await register_user(message)

    await message.answer(
        f"{greeting(user_name(message))}\n\n"
        "ℹ️ <b>Botdan foydalanish</b>\n\n"
        "🔢 <b>Kod orqali</b> — kino kodini yuboring\n"
        "🎬 <b>Kino qidirish</b> — kino nomini yozing\n"
        "🎭 <b>Janrlar</b> — janr bo'yicha qidiring\n"
        "🔥 <b>TOP</b> — eng ko'p ko'rilganlar\n"
        "🆕 <b>Yangilar</b> — yangi kinolar\n"
        "❤️ <b>Sevimlilar</b> — saqlangan kinolar\n\n"
        f"💬 Yordam: {safe(SUPPORT_USERNAME)}",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "check_sub")
async def on_check_sub(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    if not callback.from_user:
        await callback.answer()
        return

    if await check_sub(
        bot,
        callback.from_user.id,
    ):

        try:
            await callback.message.edit_text(
                "✅ Obuna tasdiqlandi!"
            )
        except TelegramBadRequest:
            pass

        await callback.message.answer(
            f"{greeting(user_name(callback))}\n\n"
            "🎬 Endi botdan foydalanishingiz mumkin!",
            reply_markup=main_menu_kb(),
        )

        await callback.answer(
            "✅ Tasdiqlandi!"
        )

    else:
        await callback.answer(
            "❌ Hali kanalga obuna bo'lmagansiz.",
            show_alert=True,
        )


@router.callback_query(F.data == "back_main")
async def on_back(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await state.clear()

    await callback.message.answer(
        f"🏠 <b>Asosiy menyu</b>\n\n"
        f"{greeting(user_name(callback))}",
        reply_markup=main_menu_kb(),
    )

    await callback.answer()


@router.message(F.text == "🔢 Kod orqali")
async def search_code_start(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:

    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Avval kanalga obuna bo'ling.",
            reply_markup=force_sub_kb(),
        )
        return

    await state.clear()
    await state.set_state(
        SearchCode.waiting
    )

    await message.answer(
        f"🔢 <b>{user_name(message)}</b>, "
        "kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(
    StateFilter(SearchCode.waiting),
    F.text,
)
async def search_code_process(
    message: Message,
    state: FSMContext,
) -> None:

    if message.text == "❌ Bekor qilish":
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
            "❌ Kod bo'sh bo'lmasin."
        )
        return

    movie = await movie_by_code(code)

    if not movie:
        await message.answer(
            "❌ Bunday kodli kino topilmadi.\n\n"
            "Boshqa kod yuboring."
        )
        return

    await state.clear()

    fav = await is_fav(
        message.from_user.id,
        movie["id"],
    )

    await message.answer(
        movie_card(movie),
        reply_markup=movie_actions_kb(
            movie["id"],
            fav,
        ),
    )


@router.message(F.text == "🎬 Kino qidirish")
async def search_title_start(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:

    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Avval kanalga obuna bo'ling.",
            reply_markup=force_sub_kb(),
        )
        return

    await state.clear()
    await state.set_state(
        SearchTitle.waiting
    )

    await message.answer(
        f"🎬 <b>{user_name(message)}</b>, "
        "kino nomini yozing:",
        reply_markup=cancel_kb(),
    )


async def search_movies(
    query: str,
) -> List[Dict[str, Any]]:

    query = sanitize(query)

    if len(query) < 2:
        return []

    try:
        result = (
            get_sb()
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
            .limit(20)
            .execute()
        )

        return result.data or []

    except Exception as e:
        logger.error(
            "search_movies: %s",
            e,
        )
        return []


@router.message(
    StateFilter(SearchTitle.waiting),
    F.text,
)
async def search_title_process(
    message: Message,
    state: FSMContext,
) -> None:

    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )
        return

    query = sanitize(
        message.text or ""
    )

    if len(query) < 2:
        await message.answer(
            "❌ Kamida 2 ta belgi yozing."
        )
        return

    rows = await search_movies(
        query
    )

    if not rows:
        await message.answer(
            "❌ Hech narsa topilmadi.\n"
            "Boshqa nom yozing."
        )
        return

    await state.clear()

    if len(rows) == 1:
        movie = rows[0]

        fav = await is_fav(
            message.from_user.id,
            movie["id"],
        )

        await message.answer(
            movie_card(movie),
            reply_markup=movie_actions_kb(
                movie["id"],
                fav,
            ),
        )
        return

    text = (
        f"🔍 <b>{len(rows)} ta natija:</b>\n\n"
    )

    buttons = []

    for row in rows:
        text += (
            f"• {safe(row.get('title'))} "
            f"({row.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        row.get("title")
                        or "Noma'lum"
                    )[:32],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 Menyuga",
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


@router.callback_query(
    F.data.startswith("movie_")
)
async def show_movie(
    callback: CallbackQuery,
) -> None:

    try:
        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )
    except (
        ValueError,
        IndexError,
    ):
        await callback.answer(
            "❌ Noto'g'ri ID.",
            show_alert=True,
        )
        return

    movie = await movie_by_id(
        movie_id
    )

    if not movie:
        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True,
        )
        return

    fav = await is_fav(
        callback.from_user.id,
        movie_id,
    )

    await callback.message.answer(
        movie_card(movie),
        reply_markup=movie_actions_kb(
            movie_id,
            fav,
        ),
    )

    await callback.answer()


@router.callback_query(
    F.data.startswith("watch_")
)
async def watch_movie(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    try:
        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )
    except (
        ValueError,
        IndexError,
    ):
        await callback.answer(
            "❌ Noto'g'ri ID.",
            show_alert=True,
        )
        return

    movie = await movie_by_id(
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
            from_chat_id=int(
                movie["channel_id"]
            ),
            message_id=int(
                movie["message_id"]
            ),
        )

        await inc_views(
            movie_id
        )

        await callback.answer(
            "🎬 Kino yuborildi!"
        )

    except TelegramForbiddenError:
        await callback.answer(
            "❌ Bot bloklangan.",
            show_alert=True,
        )

    except TelegramBadRequest as e:
        logger.error(
            "copy_message: %s",
            e,
        )

        await callback.answer(
            "❌ Kino xabari topilmadi yoki "
            "bot kanalga kira olmayapti.",
            show_alert=True,
        )

    except Exception as e:
        logger.error(
            "watch_movie: %s",
            e,
        )

        await callback.answer(
            "❌ Xatolik yuz berdi.",
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith("fav_")
)
async def add_fav(
    callback: CallbackQuery,
) -> None:

    try:
        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

        (
            get_sb()
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

        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(
                movie_id,
                True,
            )
        )

        await callback.answer(
            "❤️ Sevimlilarga qo'shildi!"
        )

    except Exception as e:
        logger.error(
            "add_fav: %s",
            e,
        )

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )


@router.callback_query(
    F.data.startswith("unfav_")
)
async def remove_fav(
    callback: CallbackQuery,
) -> None:

    try:
        movie_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

        (
            get_sb()
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
        logger.error(
            "remove_fav: %s",
            e,
        )

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )


@router.message(F.text == "❤️ Sevimlilar")
async def show_favs(
    message: Message,
    bot: Bot,
) -> None:

    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Avval kanalga obuna bo'ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_sb()
            .table("favorites")
            .select(
                "movie_id,movies(*)"
            )
            .eq(
                "telegram_id",
                message.from_user.id,
            )
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(
            "show_favs: %s",
            e,
        )

        await message.answer(
            "❌ Sevimlilarni olishda xatolik."
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
            "❤️ <b>Sevimlilar</b>\n\n"
            "Hozircha sevimli kino yo'q.",
            reply_markup=main_menu_kb(),
        )
        return

    text = (
        f"❤️ <b>{user_name(message)}, "
        "sevimli kinolaringiz:</b>\n\n"
    )

    buttons = []

    for movie in movies:
        title = str(
            movie.get("title")
            or "Noma'lum"
        )

        text += (
            f"• {safe(title)}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=title[:36],
                    callback_data=f"movie_{movie['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 Menyuga",
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


@router.message(F.text == "🎭 Janrlar")
async def show_genres(
    message: Message,
    bot: Bot,
) -> None:

    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Avval kanalga obuna bo'ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_sb()
            .table("genres")
            .select("name")
            .order("name")
            .execute()
        )

        genres = [
            x["name"]
            for x in (
                result.data or []
            )
            if x.get("name")
        ]

    except Exception as e:
        logger.error(
            "genres: %s",
            e,
        )

        await message.answer(
            "❌ Janrlarni olishda xatolik."
        )
        return

    if not genres:
        await message.answer(
            "Janrlar mavjud emas."
        )
        return

    await message.answer(
        f"🎭 <b>{user_name(message)}</b>, "
        "janrni tanlang:",
        reply_markup=genres_kb(
            genres
        ),
    )


@router.callback_query(
    F.data.startswith("genre_")
)
async def genre_list(
    callback: CallbackQuery,
) -> None:

    genre = callback.data[
        len("genre_"):
    ]

    try:
        result = (
            get_sb()
            .table("movies")
            .select("*")
            .ilike(
                "genre",
                f"%{genre}%",
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
        logger.error(
            "genre_list: %s",
            e,
        )

        await callback.answer(
            "❌ Xatolik.",
            show_alert=True,
        )
        return

    if not rows:
        await callback.answer(
            "Bu janrda kino yo'q.",
            show_alert=True,
        )
        return

    text = (
        f"🎭 <b>{safe(genre)}</b> — "
        f"{len(rows)} ta kino\n\n"
    )

    buttons = []

    for row in rows:
        text += (
            f"• {safe(row.get('title'))} "
            f"({row.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        row.get("title")
                        or "Noma'lum"
                    )[:32],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 Menyuga",
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


@router.message(F.text == "🔥 TOP")
async def popular(
    message: Message,
    bot: Bot,
) -> None:

    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Avval kanalga obuna bo'ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_sb()
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
        logger.error(
            "popular: %s",
            e,
        )

        await message.answer(
            "❌ TOP olishda xatolik."
        )
        return

    if not rows:
        await message.answer(
            "Hali kino qo'shilmagan."
        )
        return

    medals = [
        "🥇",
        "🥈",
        "🥉",
    ]

    text = "🔥 <b>TOP 10</b>\n\n"

    buttons = []

    for index, row in enumerate(rows):
        number = (
            medals[index]
            if index < 3
            else f"{index + 1}."
        )

        text += (
            f"{number} "
            f"{safe(row.get('title'))} "
            f"— {row.get('views', 0):,}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{number} "
                        f"{str(row.get('title') or 'Noma\'lum')[:28]}"
                    ),
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 Menyuga",
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


@router.message(F.text == "🆕 Yangilar")
async def newest(
    message: Message,
    bot: Bot,
) -> None:

    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Avval kanalga obuna bo'ling.",
            reply_markup=force_sub_kb(),
        )
        return

    try:
        result = (
            get_sb()
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
        logger.error(
            "newest: %s",
            e,
        )

        await message.answer(
            "❌ Yangi kinolarni olishda xatolik."
        )
        return

    if not rows:
        await message.answer(
            "Hali kino qo'shilmagan."
        )
        return

    text = (
        "🆕 <b>Yangi kinolar</b>\n\n"
    )

    buttons = []

    for row in rows:
        text += (
            f"• {safe(row.get('title'))} "
            f"({row.get('year') or '—'})\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        row.get("title")
                        or "Noma'lum"
                    )[:32],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 Menyuga",
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


@router.message(Command("admin"))
async def admin_panel(
    message: Message,
) -> None:

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):
        return

    await message.answer(
        "👨‍💻 <b>Admin panel</b>\n\n"
        f"Salom, {user_name(message)}!\n\n"
        "⚡ <b>Tez qo'shish:</b>\n"
        "Kanalda kino xabariga Reply qiling "
        "va shu Reply ustiga /add yuboring.",
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(
    F.data == "admin_fast_help"
)
async def fast_help(
    callback: CallbackQuery,
) -> None:

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer()
        return

    await callback.message.answer(
        "⚡ <b>Tez kino qo'shish</b>\n\n"
        "1️⃣ Kanalga kino yuboring.\n"
        "2️⃣ Kino xabariga Reply qiling.\n"
        "3️⃣ Reply ichiga /add yozing.\n"
        "4️⃣ Kodni yuboring.\n"
        "5️⃣ Nomini yuboring.\n"
        "6️⃣ Janrini yuboring.\n"
        "7️⃣ Yil | Reyting | Tavsif yuboring "
        "yoki o'tkazib yuboring.\n\n"
        "📢 Kanal ID va Message ID avtomatik olinadi."
    )

    await callback.answer()


@router.message(Command("add"))
async def fast_add_start(
    message: Message,
    state: FSMContext,
) -> None:

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):
        return

    await state.clear()

    replied = message.reply_to_message

    if replied is None:
        await message.answer(
            "⚡ <b>Tez kino qo'shish</b>\n\n"
            "Avval kanaldagi kino xabariga "
            "<b>Reply</b> qiling.\n\n"
            "Keyin Reply ichiga:\n"
            "<code>/add</code>\n"
            "yuboring."
        )
        return

    channel_id = replied.chat.id
    message_id = replied.message_id

    await state.update_data(
        channel_id=channel_id,
        message_id=message_id,
    )

    await state.set_state(
        AddMovie.code
    )

    await message.answer(
        "✅ Kino xabari qabul qilindi!\n\n"
        f"📢 Kanal ID: <code>{channel_id}</code>\n"
        f"💬 Message ID: <code>{message_id}</code>\n\n"
        "1️⃣ <b>Kino kodini</b> yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(
    StateFilter(AddMovie.code),
    F.text,
)
async def add_code(
    message: Message,
    state: FSMContext,
) -> None:

    if not is_admin(
        message.from_user.id
    ):
        return

    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=admin_menu_kb(),
        )
        return

    code = (
        message.text or ""
    ).strip()

    if not code:
        await message.answer(
            "❌ Kod bo'sh bo'lmasin."
        )
        return

    if len(code) > 80:
        await message.answer(
            "❌ Kod 80 belgidan oshmasin."
        )
        return

    if await movie_by_code(code):
        await message.answer(
            "❌ Bu kod band.\n"
            "Boshqa kod yuboring."
        )
        return

    await state.update_data(
        code=code
    )

    await state.set_state(
        AddMovie.title
    )

    await message.answer(
        "2️⃣ <b>Kino nomini</b> yuboring:"
    )


@router.message(
    StateFilter(AddMovie.title),
    F.text,
)
async def add_title(
    message: Message,
    state: FSMContext,
) -> None:

    if not is_admin(
        message.from_user.id
    ):
        return

    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=admin_menu_kb(),
        )
        return

    title = (
        message.text or ""
    ).strip()

    if not title:
        await message.answer(
            "❌ Kino nomi bo'sh bo'lmasin."
        )
        return

    if len(title) > 200:
        await message.answer(
            "❌ Kino nomi juda uzun."
        )
        return

    await state.update_data(
        title=title
    )

    await state.set_state(
        AddMovie.genre
    )

    await message.answer(
        "3️⃣ <b>Janrni</b> yuboring.\n\n"
        "Masalan:\n"
        "<code>Action</code>\n"
        "<code>Drama</code>\n"
        "<code>Action, Drama</code>"
    )


@router.message(
    StateFilter(AddMovie.genre),
    F.text,
)
async def add_genre(
    message: Message,
    state: FSMContext,
) -> None:

    if not is_admin(
        message.from_user.id
    ):
        return

    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=admin_menu_kb(),
        )
        return

    genre = (
        message.text or ""
    ).strip()

    if not genre:
        await message.answer(
            "❌ Janr bo'sh bo'lmasin."
        )
        return

    if len(genre) > 150:
        await message.answer(
            "❌ Janr juda uzun."
        )
        return

    await state.update_data(
        genre=genre
    )

    await state.set_state(
        AddMovie.extra
    )

    await message.answer(
        "4️⃣ <b>Ixtiyoriy ma'lumot</b>\n\n"
        "Format:\n"
        "<code>YIL | REYTING | TAVSIF</code>\n\n"
        "Misollar:\n"
        "<code>2024 | 8.5 | Ajoyib film</code>\n"
        "<code>2023 | - | -</code>\n"
        "<code>- | - | -</code>\n\n"
        "Yoki tugmani bosing:",
        reply_markup=skip_extra_kb(),
    )


@router.callback_query(
    F.data == "skip_extra",
    StateFilter(AddMovie.extra),
)
async def skip_extra(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer()
        return

    data = await state.get_data()

    await finish_add(
        callback.message,
        state,
        data,
    )

    await callback.answer()


@router.message(
    StateFilter(AddMovie.extra),
    F.text,
)
async def add_extra(
    message: Message,
    state: FSMContext,
) -> None:

    if not is_admin(
        message.from_user.id
    ):
        return

    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=admin_menu_kb(),
        )
        return

    raw = (
        message.text or ""
    ).strip()

    year = None
    rating = None
    description = None

    if raw and raw != "-":
        parts = [
            x.strip()
            for x in raw.split("|")
        ]

        if (
            len(parts) >= 1
            and parts[0]
            and parts[0] != "-"
        ):
            try:
                year = int(
                    parts[0]
                )

                current_year = (
                    datetime.now().year
                )

                if (
                    year < 1800
                    or year > current_year + 2
                ):
                    await message.answer(
                        "❌ Yil noto'g'ri."
                    )
                    return

            except ValueError:
                await message.answer(
                    "❌ Format noto'g'ri.\n\n"
                    "<code>2024 | 8.5 | Tavsif</code>"
                )
                return

        if (
            len(parts) >= 2
            and parts[1]
            and parts[1] != "-"
        ):
            try:
                rating = float(
                    parts[1].replace(
                        ",",
                        ".",
                    )
                )

                if not 0 <= rating <= 10:
                    await message.answer(
                        "❌ Reyting 0 dan 10 gacha bo'lishi kerak."
                    )
                    return

            except ValueError:
                await message.answer(
                    "❌ Reyting noto'g'ri."
                )
                return

        if (
            len(parts) >= 3
            and parts[2]
            and parts[2] != "-"
        ):
            description = parts[2][:1000]

    await state.update_data(
        year=year,
        rating=rating,
        description=description,
    )

    data = await state.get_data()

    await finish_add(
        message,
        state,
        data,
    )


@router.callback_query(
    F.data == "admin_list"
)
@router.message(Command("movies"))
async def admin_list(
    event: Union[Message, CallbackQuery],
) -> None:

    if not is_admin(
        event.from_user.id
    ):
        return

    try:
        result = (
            get_sb()
            .table("movies")
            .select(
                "id,code,title,channel_id,"
                "message_id,views,year,genre,rating"
            )
            .order(
                "id",
                desc=True,
            )
            .limit(35)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        logger.error(
            "admin_list: %s",
            e,
        )

        text = "❌ Xatolik."

        if isinstance(
            event,
            CallbackQuery,
        ):
            await event.answer(
                text,
                show_alert=True,
            )
        else:
            await event.answer(text)

        return

    if not rows:
        text = "📋 Bazada kino yo'q."

    else:
        parts = [
            f"📋 <b>Kinolar</b> ({len(rows)})\n"
        ]

        for row in rows:
            parts.append(
                "━━━━━━━━━━━━━━\n"
                f"🆔 ID: <code>{row['id']}</code>\n"
                f"🔢 Kod: <code>{safe(row.get('code'))}</code>\n"
                f"🎬 <b>{safe(row.get('title'))}</b>\n"
                f"📢 Kanal: <code>{row.get('channel_id')}</code>\n"
                f"💬 Message: <code>{row.get('message_id')}</code>\n"
                f"📅 Yil: {row.get('year') or '—'}\n"
                f"🎭 Janr: {safe(row.get('genre') or '—')}\n"
                f"⭐ Reyting: "
                f"{row.get('rating') if row.get('rating') is not None else '—'}\n"
                f"👁 Ko'rish: {row.get('views', 0)}"
            )

        text = "\n".join(parts)

    if len(text) > 4000:
        text = text[:3900] + "\n\n…"

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


@router.callback_query(
    F.data == "admin_stats"
)
@router.message(Command("stats"))
async def admin_stats(
    event: Union[Message, CallbackQuery],
) -> None:

    if not is_admin(
        event.from_user.id
    ):
        return

    try:
        users_result = (
            get_sb()
            .table("users")
            .select(
                "id",
                count="exact",
            )
            .execute()
        )

        movies_result = (
            get_sb()
            .table("movies")
            .select(
                "id",
                count="exact",
            )
            .execute()
        )

        views_result = (
            get_sb()
            .table("movies")
            .select("views")
            .execute()
        )

        total_views = sum(
            int(
                x.get("views") or 0
            )
            for x in (
                views_result.data or []
            )
        )

        today = (
            datetime.now(
                timezone.utc
            )
            .date()
            .isoformat()
        )

        today_result = (
            get_sb()
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

        top_result = (
            get_sb()
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

        top = (
            top_result.data[0]
            if top_result.data
            else None
        )

        top_text = (
            f"{safe(top.get('title'))} "
            f"({top.get('views', 0)})"
            if top
            else "—"
        )

        text = (
            "📊 <b>Statistika</b>\n\n"
            f"👥 Foydalanuvchilar: "
            f"<b>{users_result.count or 0}</b>\n"
            f"🎬 Kinolar: "
            f"<b>{movies_result.count or 0}</b>\n"
            f"👁 Jami ko'rishlar: "
            f"<b>{total_views:,}</b>\n"
            f"🟢 Bugun faol: "
            f"<b>{today_result.count or 0}</b>\n"
            f"🔥 TOP: {top_text}"
        )

    except Exception as e:
        logger.error(
            "admin_stats: %s",
            e,
        )

        text = (
            "❌ Statistikani olishda xatolik."
        )

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


@router.callback_query(
    F.data == "admin_users"
)
@router.message(Command("users"))
async def admin_users(
    event: Union[Message, CallbackQuery],
) -> None:

    if not is_admin(
        event.from_user.id
    ):
        return

    try:
        total = (
            get_sb()
            .table("users")
            .select(
                "id",
                count="exact",
            )
            .execute()
        )

        blocked = (
            get_sb()
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
            "👥 <b>Foydalanuvchilar</b>\n\n"
            f"👤 Jami: <b>{total.count or 0}</b>\n"
            f"🚫 Bloklagan: "
            f"<b>{blocked.count or 0}</b>"
        )

    except Exception as e:
        logger.error(
            "admin_users: %s",
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


@router.callback_query(
    F.data == "admin_delete"
)
async def delete_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer()
        return

    await state.clear()

    await state.set_state(
        AdminDelete.confirm
    )

    await callback.message.answer(
        "🗑 <b>Kino o'chirish</b>\n\n"
        "O'chirmoqchi bo'lgan kino "
        "<b>kodini</b> yuboring:",
        reply_markup=cancel_kb(),
    )

    await callback.answer()


@router.message(
    StateFilter(AdminDelete.confirm),
    F.text,
)
async def delete_confirm(
    message: Message,
    state: FSMContext,
) -> None:

    if not is_admin(
        message.from_user.id
    ):
        return

    if message.text == "❌ Bekor qilish":
        await state.clear()

        await message.answer(
            "Bekor qilindi.",
            reply_markup=admin_menu_kb(),
        )
        return

    code = (
        message.text or ""
    ).strip()

    movie = await movie_by_code(
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
        "⚠️ <b>Rostdan o'chirasizmi?</b>\n\n"
        f"🎬 {safe(movie.get('title'))}\n"
        f"🔢 <code>{safe(movie.get('code'))}</code>",
        reply_markup=confirm_delete_kb(
            movie["id"]
        ),
    )


@router.callback_query(
    F.data.startswith("del_yes_")
)
async def del_yes(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer()
        return

    try:
        movie_id = int(
            callback.data.split(
                "_"
            )[2]
        )

        (
            get_sb()
            .table("favorites")
            .delete()
            .eq(
                "movie_id",
                movie_id,
            )
            .execute()
        )

        (
            get_sb()
            .table("movies")
            .delete()
            .eq(
                "id",
                movie_id,
            )
            .execute()
        )

        await state.clear()

        await callback.message.edit_text(
            "✅ Kino o'chirildi."
        )

        await callback.answer()

    except Exception as e:
        logger.error(
            "del_yes: %s",
            e,
        )

        await callback.answer(
            "❌ O'chirishda xatolik.",
            show_alert=True,
        )


@router.callback_query(
    F.data == "del_no"
)
async def del_no(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await state.clear()

    await callback.message.edit_text(
        "❌ O'chirish bekor qilindi."
    )

    await callback.answer()


@router.callback_query(
    F.data == "admin_broadcast"
)
@router.message(Command("broadcast"))
async def broadcast_start(
    event: Union[Message, CallbackQuery],
    state: FSMContext,
) -> None:

    if not is_admin(
        event.from_user.id
    ):
        return

    await state.clear()

    await state.set_state(
        Broadcast.waiting
    )

    text = (
        "📢 <b>Reklama / Broadcast</b>\n\n"
        "Matn, rasm, video, audio, animation "
        "yoki document yuboring.\n\n"
        "Bekor qilish: /cancel"
    )

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


@router.message(Command("cancel"))
async def cancel_cmd(
    message: Message,
    state: FSMContext,
) -> None:

    await state.clear()

    if is_admin(
        message.from_user.id
    ):
        await message.answer(
            "Bekor qilindi.",
            reply_markup=admin_menu_kb(),
        )
    else:
        await message.answer(
            "Bekor qilindi.",
            reply_markup=main_menu_kb(),
        )


async def send_broadcast_message(
    bot: Bot,
    user_id: int,
    message: Message,
) -> None:

    if message.photo:
        await bot.send_photo(
            user_id,
            message.photo[-1].file_id,
            caption=message.caption,
            parse_mode=ParseMode.HTML,
        )

    elif message.video:
        await bot.send_video(
            user_id,
            message.video.file_id,
            caption=message.caption,
            parse_mode=ParseMode.HTML,
        )

    elif message.document:
        await bot.send_document(
            user_id,
            message.document.file_id,
            caption=message.caption,
            parse_mode=ParseMode.HTML,
        )

    elif message.audio:
        await bot.send_audio(
            user_id,
            message.audio.file_id,
            caption=message.caption,
            parse_mode=ParseMode.HTML,
        )

    elif message.animation:
        await bot.send_animation(
            user_id,
            message.animation.file_id,
            caption=message.caption,
            parse_mode=ParseMode.HTML,
        )

    elif message.voice:
        await bot.send_voice(
            user_id,
            message.voice.file_id,
            caption=message.caption,
            parse_mode=ParseMode.HTML,
        )

    elif message.text:
        await bot.send_message(
            user_id,
            message.text,
            parse_mode=ParseMode.HTML,
        )

    else:
        raise ValueError(
            "Qo'llab-quvvatlanmaydigan xabar turi."
        )


@router.message(
    StateFilter(Broadcast.waiting)
)
async def broadcast_run(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:

    if not is_admin(
        message.from_user.id
    ):
        return

    await state.clear()

    try:
        result = (
            get_sb()
            .table("users")
            .select("telegram_id")
            .eq(
                "is_blocked",
                False,
            )
            .execute()
        )

        users = [
            x["telegram_id"]
            for x in (
                result.data or []
            )
            if x.get("telegram_id")
        ]

    except Exception as e:
        logger.error(
            "broadcast users: %s",
            e,
        )

        await message.answer(
            "❌ Foydalanuvchilar olinmadi."
        )
        return

    if not users:
        await message.answer(
            "❌ Foydalanuvchilar yo'q."
        )
        return

    sent = 0
    failed = 0
    blocked = 0

    status = await message.answer(
        f"📢 Yuborilmoqda...\n"
        f"0/{len(users)}"
    )

    for index, user_id in enumerate(
        users,
        1,
    ):

        try:
            await send_broadcast_message(
                bot,
                int(user_id),
                message,
            )

            sent += 1

            await asyncio.sleep(
                0.05
            )

        except TelegramRetryAfter as e:

            await asyncio.sleep(
                e.retry_after + 1
            )

            try:
                await send_broadcast_message(
                    bot,
                    int(user_id),
                    message,
                )

                sent += 1

            except TelegramForbiddenError:
                blocked += 1

                try:
                    (
                        get_sb()
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
                except Exception:
                    pass

            except Exception:
                failed += 1

        except TelegramForbiddenError:
            blocked += 1

            try:
                (
                    get_sb()
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
            except Exception:
                pass

        except Exception as e:
            failed += 1

            logger.warning(
                "broadcast %s: %s",
                user_id,
                e,
            )

        if (
            index % 40 == 0
            or index == len(users)
        ):
            try:
                await status.edit_text(
                    f"📢 <b>Yuborilmoqda...</b>\n\n"
                    f"📊 {index}/{len(users)}\n"
                    f"✅ {sent}\n"
                    f"❌ {failed}\n"
                    f"🚫 {blocked}"
                )
            except TelegramBadRequest:
                pass

    await message.answer(
        "📢 <b>Broadcast tugadi</b>\n\n"
        f"✅ Yuborildi: {sent}\n"
        f"❌ Xato: {failed}\n"
        f"🚫 Bloklagan: {blocked}",
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(
    F.data == "admin_force_sub"
)
async def force_sub_info(
    callback: CallbackQuery,
) -> None:

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer()
        return

    await callback.message.answer(
        "📢 <b>Majburiy obuna</b>\n\n"
        f"📢 Kanal ID: <code>{CHANNEL_ID}</code>\n"
        f"🔗 Username: "
        f"{safe(CHANNEL_USERNAME or '—')}\n\n"
        "Bot kanalga administrator qilib "
        "qo'shilgan bo'lishi kerak."
    )

    await callback.answer()


@router.callback_query(
    F.data == "admin_settings"
)
async def settings(
    callback: CallbackQuery,
) -> None:

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer()
        return

    await callback.message.answer(
        "⚙️ <b>Sozlamalar</b>\n\n"
        f"🤖 Token: "
        f"{'✅' if BOT_TOKEN else '❌'}\n"
        f"👨‍💻 Adminlar: "
        f"<code>{safe(ADMIN_IDS)}</code>\n"
        f"📢 CHANNEL_ID: "
        f"<code>{CHANNEL_ID}</code>\n"
        f"🔗 Username: "
        f"{safe(CHANNEL_USERNAME or '—')}\n"
        f"💬 Support: "
        f"{safe(SUPPORT_USERNAME)}\n"
        f"🗄 Supabase: "
        f"{'✅' if SUPABASE_URL and SUPABASE_KEY else '❌'}"
    )

    await callback.answer()


@router.message(F.text)
async def fallback(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:

    current_state = await state.get_state()

    if current_state is not None:
        return

    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(
        bot,
        message.from_user.id,
    ):
        await message.answer(
            "📢 Avval kanalga obuna bo'ling.",
            reply_markup=force_sub_kb(),
        )
        return

    query = sanitize(
        message.text or ""
    )

    if len(query) < 2:
        return

    rows = await search_movies(
        query
    )

    if not rows:
        await message.answer(
            f"❌ <b>{user_name(message)}</b>, "
            "hech narsa topilmadi.",
            reply_markup=main_menu_kb(),
        )
        return

    if len(rows) == 1:
        movie = rows[0]

        fav = await is_fav(
            message.from_user.id,
            movie["id"],
        )

        await message.answer(
            movie_card(movie),
            reply_markup=movie_actions_kb(
                movie["id"],
                fav,
            ),
        )

        return

    text = (
        f"🔍 <b>{len(rows)} ta natija:</b>\n\n"
    )

    buttons = []

    for row in rows:
        text += (
            f"• {safe(row.get('title'))}\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=str(
                        row.get("title")
                        or "Noma'lum"
                    )[:32],
                    callback_data=f"movie_{row['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 Menyuga",
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


async def main() -> None:
    global supabase

    missing = []

    if not BOT_TOKEN:
        missing.append(
            "BOT_TOKEN"
        )

    if not ADMIN_IDS:
        missing.append(
            "ADMIN_IDS"
        )

    if not SUPABASE_URL:
        missing.append(
            "SUPABASE_URL"
        )

    if not SUPABASE_KEY:
        missing.append(
            "SUPABASE_KEY"
        )

    if missing:
        print(
            "ERROR: .env da quyidagilar yo'q:"
        )

        print(
            ", ".join(missing)
        )

        return

    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY,
        )

        logger.info(
            "Supabase ulandi."
        )

    except Exception as e:
        logger.exception(
            "Supabase ulanish xatosi: %s",
            e,
        )
        return

    await ensure_genres()

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
        "Kino Bot ishga tushmoqda..."
    )

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
