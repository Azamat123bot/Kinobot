from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from html import escape
from typing import Any, Optional
from zoneinfo import ZoneInfo

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
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@")

TZ = ZoneInfo("Asia/Tashkent")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | kino_bot | %(message)s",
)

log = logging.getLogger("kino_bot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL yoki SUPABASE_KEY topilmadi")

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

router = Router()


class AddMovie(StatesGroup):
    source = State()
    code = State()
    title = State()
    genre = State()
    extra = State()


class NormalAdd(StatesGroup):
    source = State()
    code = State()
    title = State()
    genre = State()
    extra = State()


class SearchCode(StatesGroup):
    waiting = State()


class SearchTitle(StatesGroup):
    waiting = State()


class DeleteMovie(StatesGroup):
    waiting = State()
    confirm = State()


class Broadcast(StatesGroup):
    waiting = State()


class ForceChannel(StatesGroup):
    add_source = State()
    add_link = State()
    remove = State()


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def safe(value: Any) -> str:
    return escape(str(value or ""))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def greeting() -> str:
    h = datetime.now(TZ).hour

    if 5 <= h < 12:
        return "Xayrli tong"
    if 12 <= h < 18:
        return "Xayrli kun"
    if 18 <= h < 23:
        return "Xayrli kech"
    return "Assalomu alaykum"


async def db_execute(builder):
    return await asyncio.to_thread(builder.execute)


async def register_user(user: Any) -> None:
    try:
        data = {
            "telegram_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "updated_at": now_iso(),
        }

        result = await db_execute(
            sb.table("users")
            .upsert(data, on_conflict="telegram_id")
        )

        return result
    except Exception:
        log.exception("register_user error")


async def get_force_channels() -> list[dict]:
    try:
        result = await db_execute(
            sb.table("force_sub_channels")
            .select("*")
            .eq("enabled", True)
            .order("id")
        )

        return result.data or []
    except Exception:
        log.exception("get_force_channels error")
        return []


async def check_sub(bot: Bot, user_id: int) -> bool:
    channels = await get_force_channels()

    if not channels:
        return True

    for channel in channels:
        chat_id = channel.get("chat_id")

        if not chat_id:
            continue

        try:
            member = await bot.get_chat_member(
                chat_id=int(chat_id),
                user_id=user_id,
            )

            if member.status in {
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED,
            }:
                return False

            if member.status == ChatMemberStatus.RESTRICTED:
                if not getattr(member, "is_member", False):
                    return False

        except Exception:
            log.exception(
                "Subscription check error: chat_id=%s",
                chat_id,
            )
            return False

    return True


async def subscription_keyboard(bot: Bot) -> InlineKeyboardMarkup:
    channels = await get_force_channels()

    rows = []

    for ch in channels:
        title = ch.get("title") or ch.get("username") or "Kanal"
        username = (ch.get("username") or "").strip().lstrip("@")
        invite = (ch.get("invite_link") or "").strip()

        url = ""

        if invite:
            url = invite
        elif username:
            url = f"https://t.me/{username}"

        if url:
            rows.append([
                InlineKeyboardButton(
                    text=f"📢 {title}",
                    url=url,
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="✅ Tekshirdim",
            callback_data="check_sub",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def require_sub(
    message: Message,
) -> bool:
    if await check_sub(message.bot, message.from_user.id):
        return True

    await message.answer(
        "❗ Botdan foydalanish uchun quyidagi kanallarga "
        "obuna bo‘ling:",
        reply_markup=await subscription_keyboard(message.bot),
    )

    return False


async def require_sub_callback(
    callback: CallbackQuery,
) -> bool:
    if await check_sub(
        callback.bot,
        callback.from_user.id,
    ):
        return True

    await callback.answer(
        "❌ Avval barcha kanallarga obuna bo‘ling.",
        show_alert=True,
    )

    await callback.message.answer(
        "❗ Botdan foydalanish uchun quyidagi kanallarga "
        "obuna bo‘ling:",
        reply_markup=await subscription_keyboard(callback.bot),
    )

    return False


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Kino qidirish",
                    callback_data="search_title",
                ),
                InlineKeyboardButton(
                    text="🔢 Kod orqali",
                    callback_data="search_code",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎭 Janrlar",
                    callback_data="genres",
                ),
                InlineKeyboardButton(
                    text="🔥 TOP",
                    callback_data="popular",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🆕 Yangilar",
                    callback_data="newest",
                ),
                InlineKeyboardButton(
                    text="❤️ Sevimlilar",
                    callback_data="favorites",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Yordam",
                    callback_data="help",
                ),
            ],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Tez qo‘shish",
                    callback_data="admin_fast_add",
                ),
                InlineKeyboardButton(
                    text="➕ Oddiy qo‘shish",
                    callback_data="admin_normal_add",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 O‘chirish",
                    callback_data="admin_delete",
                ),
                InlineKeyboardButton(
                    text="📋 Kinolar",
                    callback_data="admin_movies",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Statistika",
                    callback_data="admin_stats",
                ),
                InlineKeyboardButton(
                    text="👥 Foydalanuvchilar",
                    callback_data="admin_users",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 Reklama",
                    callback_data="admin_broadcast",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 Majburiy obuna",
                    callback_data="admin_force",
                ),
            ],
        ]
    )


def movie_keyboard(movie_id: int, favorite: bool) -> InlineKeyboardMarkup:
    fav_text = "💔 Sevimlidan olib tashlash" if favorite else "❤️ Sevimliga"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Tomosha qilish",
                    callback_data=f"watch:{movie_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=fav_text,
                    callback_data=f"fav:{movie_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Menyu",
                    callback_data="main",
                )
            ],
        ]
    )


def movie_card(movie: dict) -> str:
    title = safe(movie.get("title") or "Noma'lum")
    code = safe(movie.get("code") or "-")
    genre = safe(movie.get("genre") or "-")
    year = safe(movie.get("year") or "")
    rating = safe(movie.get("rating") or "")
    description = safe(movie.get("description") or "")

    try:
        views = int(movie.get("views") or 0)
    except Exception:
        views = 0

    text = (
        f"🎬 <b>{title}</b>\n\n"
        f"🔢 Kod: <code>{code}</code>\n"
        f"🎭 Janr: {genre}\n"
    )

    if year:
        text += f"📅 Yil: {year}\n"

    if rating:
        text += f"⭐ Reyting: {rating}\n"

    text += f"👁 Ko‘rishlar: {views}\n"

    if description:
        if len(description) > 700:
            description = description[:700] + "..."
        text += f"\n📝 {description}"

    return text


async def movie_by_id(movie_id: int) -> Optional[dict]:
    try:
        result = await db_execute(
            sb.table("movies")
            .select("*")
            .eq("id", movie_id)
            .limit(1)
        )

        if result.data:
            return result.data[0]

    except Exception:
        log.exception("movie_by_id error")

    return None


async def movie_by_code(code: str) -> Optional[dict]:
    try:
        result = await db_execute(
            sb.table("movies")
            .select("*")
            .eq("code", code)
            .limit(1)
        )

        if result.data:
            return result.data[0]

    except Exception:
        log.exception("movie_by_code error")

    return None


async def search_movies(text: str) -> list[dict]:
    text = text.strip()

    if not text:
        return []

    try:
        result = await db_execute(
            sb.table("movies")
            .select("*")
            .ilike("title", f"%{text}%")
            .order("id", desc=True)
            .limit(20)
        )

        return result.data or []

    except Exception:
        log.exception("search_movies error")
        return []


async def inc_views(movie_id: int) -> None:
    try:
        movie = await movie_by_id(movie_id)

        if not movie:
            return

        views = int(movie.get("views") or 0) + 1

        await db_execute(
            sb.table("movies")
            .update({"views": views})
            .eq("id", movie_id)
        )

    except Exception:
        log.exception("inc_views error")


async def is_favorite(user_id: int, movie_id: int) -> bool:
    try:
        result = await db_execute(
            sb.table("favorites")
            .select("id")
            .eq("telegram_id", user_id)
            .eq("movie_id", movie_id)
            .limit(1)
        )

        return bool(result.data)

    except Exception:
        log.exception("is_favorite error")
        return False


async def add_favorite(user_id: int, movie_id: int) -> bool:
    try:
        if await is_favorite(user_id, movie_id):
            return True

        await db_execute(
            sb.table("favorites")
            .insert({
                "telegram_id": user_id,
                "movie_id": movie_id,
            })
        )

        return True

    except Exception:
        log.exception("add_favorite error")
        return False


async def remove_favorite(user_id: int, movie_id: int) -> bool:
    try:
        await db_execute(
            sb.table("favorites")
            .delete()
            .eq("telegram_id", user_id)
            .eq("movie_id", movie_id)
        )

        return True

    except Exception:
        log.exception("remove_favorite error")
        return False


async def resolve_source_message(message: Message):
    origin = getattr(message, "forward_origin", None)

    if origin:
        chat = getattr(origin, "chat", None)
        message_id = getattr(origin, "message_id", None)

        if chat and message_id:
            return int(chat.id), int(message_id)

    replied = message.reply_to_message

    if replied:
        origin = getattr(replied, "forward_origin", None)

        if origin:
            chat = getattr(origin, "chat", None)
            message_id = getattr(origin, "message_id", None)

            if chat and message_id:
                return int(chat.id), int(message_id)

        if replied.chat:
            return int(replied.chat.id), int(replied.message_id)

    return None, None


async def save_movie(
    source_chat_id: int,
    source_message_id: int,
    code: str,
    title: str,
    genre: str,
    extra: str = "",
) -> Optional[dict]:

    year = None
    rating = None
    description = extra.strip()

    parts = [
        x.strip()
        for x in extra.split("|")
    ]

    if len(parts) >= 1 and parts[0]:
        year = parts[0]

    if len(parts) >= 2 and parts[1]:
        rating = parts[1]

    if len(parts) >= 3:
        description = " | ".join(parts[2:]).strip()

    data = {
        "source_chat_id": source_chat_id,
        "source_message_id": source_message_id,
        "code": code,
        "title": title,
        "genre": genre,
        "year": year,
        "rating": rating,
        "description": description,
        "views": 0,
        "created_at": now_iso(),
    }

    try:
        result = await db_execute(
            sb.table("movies").insert(data)
        )

        if result.data:
            return result.data[0]

    except Exception:
        log.exception("save_movie error")

    return None


async def finish_add(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    code = data.get("code")
    title = data.get("title")
    genre = data.get("genre")
    extra = data.get("extra", "")

    if not source_chat_id or not source_message_id:
        await message.answer(
            "❌ Kanal xabari manbasi topilmadi."
        )
        await state.clear()
        return

    old = await movie_by_code(str(code))

    if old:
        await message.answer(
            f"❌ <code>{safe(code)}</code> kodi allaqachon mavjud."
        )
        await state.clear()
        return

    movie = await save_movie(
        source_chat_id=int(source_chat_id),
        source_message_id=int(source_message_id),
        code=str(code),
        title=str(title),
        genre=str(genre),
        extra=str(extra),
    )

    if not movie:
        await message.answer(
            "❌ Kino saqlanmadi. Database xatosi."
        )
        await state.clear()
        return

    await state.clear()

    await message.answer(
        "✅ <b>Kino muvaffaqiyatli qo‘shildi!</b>\n\n"
        f"🎬 {safe(title)}\n"
        f"🔢 Kod: <code>{safe(code)}</code>\n"
        f"🎭 {safe(genre)}"
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await register_user(message.from_user)

    if not await require_sub(message):
        return

    await message.answer(
        f"{greeting()}, <b>{safe(message.from_user.first_name)}</b>!\n\n"
        "🎬 Kino botiga xush kelibsiz.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("help"))
async def help_command(message: Message):
    if not await require_sub(message):
        return

    text = (
        "ℹ️ <b>Botdan foydalanish</b>\n\n"
        "🔢 Kod orqali — kino kodini kiriting.\n"
        "🎬 Kino qidirish — nomi bo‘yicha qidiring.\n"
        "🎭 Janrlar — janr bo‘yicha tanlang.\n"
        "🔥 TOP — eng ko‘p ko‘rilganlar.\n"
        "🆕 Yangilar — yangi qo‘shilganlar.\n"
        "❤️ Sevimlilar — saqlagan kinolaringiz."
    )

    await message.answer(text)


@router.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Siz admin emassiz.")
        return

    await message.answer(
        "⚙️ <b>Admin panel</b>",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "main")
async def back_main(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    await callback.message.edit_text(
        f"{greeting()}!\n\n🎬 Kino botiga xush kelibsiz.",
        reply_markup=main_keyboard(),
    )

    await callback.answer()


@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    await callback.message.edit_text(
        "ℹ️ <b>Yordam</b>\n\n"
        "🔢 Kod orqali — kodni kiriting.\n"
        "🎬 Kino qidirish — nomini yozing.\n"
        "🎭 Janrlar — janr tanlang.\n"
        "🔥 TOP — mashhur kinolar.\n"
        "🆕 Yangilar — yangi kinolar.\n"
        "❤️ Sevimlilar — saqlangan kinolar.",
        reply_markup=main_keyboard(),
    )

    await callback.answer()


@router.callback_query(F.data == "check_sub")
async def check_subscription(callback: CallbackQuery):
    if await check_sub(
        callback.bot,
        callback.from_user.id,
    ):
        await callback.answer(
            "✅ Obuna tasdiqlandi!",
            show_alert=True,
        )

        await callback.message.answer(
            "🎬 Endi botdan foydalanishingiz mumkin.",
            reply_markup=main_keyboard(),
        )

    else:
        await callback.answer(
            "❌ Hali barcha kanallarga obuna bo‘lmagansiz.",
            show_alert=True,
        )


@router.callback_query(F.data == "search_code")
async def search_code_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_sub_callback(callback):
        return

    await state.set_state(SearchCode.waiting)

    await callback.message.answer(
        "🔢 Kino kodini yuboring:"
    )

    await callback.answer()


@router.message(SearchCode.waiting)
async def search_code(message: Message, state: FSMContext):
    if not await require_sub(message):
        return

    code = message.text.strip()

    movie = await movie_by_code(code)

    await state.clear()

    if not movie:
        await message.answer(
            "❌ Bunday kodli kino topilmadi."
        )
        return

    fav = await is_favorite(
        message.from_user.id,
        int(movie["id"]),
    )

    await message.answer(
        movie_card(movie),
        reply_markup=movie_keyboard(
            int(movie["id"]),
            fav,
        ),
    )


@router.callback_query(F.data == "search_title")
async def search_title_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_sub_callback(callback):
        return

    await state.set_state(SearchTitle.waiting)

    await callback.message.answer(
        "🎬 Kino nomini yuboring:"
    )

    await callback.answer()


@router.message(SearchTitle.waiting)
async def search_title(
    message: Message,
    state: FSMContext,
):
    if not await require_sub(message):
        return

    movies = await search_movies(message.text)

    await state.clear()

    if not movies:
        await message.answer(
            "❌ Kino topilmadi."
        )
        return

    for movie in movies[:10]:
        fav = await is_favorite(
            message.from_user.id,
            int(movie["id"]),
        )

        await message.answer(
            movie_card(movie),
            reply_markup=movie_keyboard(
                int(movie["id"]),
                fav,
            ),
        )


@router.callback_query(F.data.startswith("watch:"))
async def watch_movie(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    try:
        movie_id = int(
            callback.data.split(":", 1)[1]
        )
    except Exception:
        await callback.answer(
            "❌ Noto‘g‘ri kino.",
            show_alert=True,
        )
        return

    movie = await movie_by_id(movie_id)

    if not movie:
        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True,
        )
        return

    source_chat_id = movie.get("source_chat_id")
    source_message_id = movie.get("source_message_id")

    if not source_chat_id or not source_message_id:
        await callback.answer(
            "❌ Kino manbasi topilmadi.",
            show_alert=True,
        )
        return

    try:
        await callback.answer("⏳ Kino yuborilmoqda...")

        await callback.bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=int(source_chat_id),
            message_id=int(source_message_id),
        )

        await inc_views(movie_id)

    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)

        try:
            await callback.bot.copy_message(
                chat_id=callback.from_user.id,
                from_chat_id=int(source_chat_id),
                message_id=int(source_message_id),
            )

            await inc_views(movie_id)

        except Exception:
            log.exception("watch retry error")

            await callback.message.answer(
                "❌ Videoni yuborishda xatolik yuz berdi."
            )

    except TelegramBadRequest as e:
        error_text = str(e).lower()

        log.error(
            "watch copy error: %s | movie=%s | source_chat=%s | source_message=%s",
            e,
            movie_id,
            source_chat_id,
            source_message_id,
        )

        if "message to copy not found" in error_text:
            await callback.message.answer(
                "❌ Ushbu kino kanalidagi asl post "
                "topilmadi.\n\n"
                "Post o‘chirilgan yoki kino noto‘g‘ri "
                "qo‘shilgan."
            )
        else:
            await callback.message.answer(
                "❌ Videoni yuborib bo‘lmadi."
            )

    except TelegramForbiddenError:
        await callback.message.answer(
            "❌ Bot bu kanal/xabarga kira olmayapti.\n"
            "Bot kanal administratori ekanini tekshiring."
        )

    except Exception:
        log.exception(
            "watch copy unexpected error"
        )

        await callback.message.answer(
            "❌ Videoni yuborishda kutilmagan xatolik."
        )


@router.callback_query(F.data.startswith("fav:"))
async def favorite_callback(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    movie_id = int(
        callback.data.split(":", 1)[1]
    )

    movie = await movie_by_id(movie_id)

    if not movie:
        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    if await is_favorite(user_id, movie_id):
        ok = await remove_favorite(
            user_id,
            movie_id,
        )

        if ok:
            await callback.answer(
                "💔 Sevimlilardan olib tashlandi."
            )
    else:
        ok = await add_favorite(
            user_id,
            movie_id,
        )

        if ok:
            await callback.answer(
                "❤️ Sevimlilarga qo‘shildi."
            )

    fav = await is_favorite(
        user_id,
        movie_id,
    )

    try:
        await callback.message.edit_reply_markup(
            reply_markup=movie_keyboard(
                movie_id,
                fav,
            )
        )
    except Exception:
        pass


@router.callback_query(F.data == "popular")
async def popular(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    try:
        result = await db_execute(
            sb.table("movies")
            .select("*")
            .order("views", desc=True)
            .limit(10)
        )

        movies = result.data or []

        if not movies:
            await callback.message.answer(
                "❌ Hozircha kinolar yo‘q."
            )
            await callback.answer()
            return

        text = "🔥 <b>TOP kinolar</b>\n\n"

        for i, movie in enumerate(movies, 1):
            title = safe(movie.get("title") or "Noma'lum")

            try:
                views = int(movie.get("views") or 0)
            except Exception:
                views = 0

            text += (
                f"{i}. {title} — "
                f"👁 {views}\n"
                f"🔢 <code>{safe(movie.get('code'))}</code>\n\n"
            )

        await callback.message.answer(text)

    except Exception:
        log.exception("popular error")

        await callback.message.answer(
            "❌ TOP ro‘yxatini olishda xatolik."
        )

    await callback.answer()


@router.callback_query(F.data == "newest")
async def newest(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    try:
        result = await db_execute(
            sb.table("movies")
            .select("*")
            .order("id", desc=True)
            .limit(10)
        )

        movies = result.data or []

        if not movies:
            await callback.message.answer(
                "❌ Hozircha kinolar yo‘q."
            )
            await callback.answer()
            return

        for movie in movies:
            fav = await is_favorite(
                callback.from_user.id,
                int(movie["id"]),
            )

            await callback.message.answer(
                movie_card(movie),
                reply_markup=movie_keyboard(
                    int(movie["id"]),
                    fav,
                ),
            )

    except Exception:
        log.exception("newest error")

        await callback.message.answer(
            "❌ Kinolarni olishda xatolik."
        )

    await callback.answer()


@router.callback_query(F.data == "favorites")
async def favorites(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    try:
        result = await db_execute(
            sb.table("favorites")
            .select("movie_id")
            .eq(
                "telegram_id",
                callback.from_user.id,
            )
            .order("id", desc=True)
            .limit(50)
        )

        rows = result.data or []

        if not rows:
            await callback.message.answer(
                "❤️ Sevimlilar ro‘yxati bo‘sh."
            )
            await callback.answer()
            return

        for row in rows:
            movie = await movie_by_id(
                int(row["movie_id"])
            )

            if not movie:
                continue

            await callback.message.answer(
                movie_card(movie),
                reply_markup=movie_keyboard(
                    int(movie["id"]),
                    True,
                ),
            )

    except Exception:
        log.exception("favorites error")

        await callback.message.answer(
            "❌ Sevimlilarni olishda xatolik."
        )

    await callback.answer()


@router.callback_query(F.data == "genres")
async def genres(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    try:
        result = await db_execute(
            sb.table("movies")
            .select("genre")
        )

        genres_set = set()

        for row in result.data or []:
            genre = (row.get("genre") or "").strip()

            if genre:
                genres_set.add(genre)

        if not genres_set:
            await callback.message.answer(
                "❌ Janrlar mavjud emas."
            )
            await callback.answer()
            return

        rows = []

        for genre in sorted(genres_set):
            rows.append([
                InlineKeyboardButton(
                    text=f"🎭 {genre}",
                    callback_data=f"genre:{genre[:35]}",
                )
            ])

        await callback.message.answer(
            "🎭 <b>Janrni tanlang:</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=rows
            ),
        )

    except Exception:
        log.exception("genres error")

        await callback.message.answer(
            "❌ Janrlarni olishda xatolik."
        )

    await callback.answer()


@router.callback_query(F.data.startswith("genre:"))
async def genre_movies(callback: CallbackQuery):
    if not await require_sub_callback(callback):
        return

    genre = callback.data.split(":", 1)[1]

    try:
        result = await db_execute(
            sb.table("movies")
            .select("*")
            .ilike("genre", genre)
            .order("id", desc=True)
            .limit(20)
        )

        movies = result.data or []

        if not movies:
            await callback.message.answer(
                "❌ Bu janrda kino topilmadi."
            )
        else:
            for movie in movies:
                fav = await is_favorite(
                    callback.from_user.id,
                    int(movie["id"]),
                )

                await callback.message.answer(
                    movie_card(movie),
                    reply_markup=movie_keyboard(
                        int(movie["id"]),
                        fav,
                    ),
                )

    except Exception:
        log.exception("genre_movies error")

        await callback.message.answer(
            "❌ Janr kinolarini olishda xatolik."
        )

    await callback.answer()


@router.callback_query(F.data == "admin_fast_add")
async def admin_fast_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Siz admin emassiz.",
            show_alert=True,
        )
        return

    await state.set_state(AddMovie.source)

    await callback.message.answer(
        "⚡ <b>Tezkor kino qo‘shish</b>\n\n"
        "Kanal postiga reply qilib shu yerga "
        "yuboring yoki kanal postini forward qiling."
    )

    await callback.answer()


@router.message(AddMovie.source)
async def fast_add_source(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    chat_id, message_id = await resolve_source_message(
        message
    )

    if not chat_id or not message_id:
        await message.answer(
            "❌ Kanal postini topib bo‘lmadi.\n\n"
            "Kanal postiga reply qiling yoki "
            "postni forward qiling."
        )
        return

    await state.update_data(
        source_chat_id=chat_id,
        source_message_id=message_id,
    )

    await state.set_state(AddMovie.code)

    await message.answer(
        "🔢 Kino kodini yuboring:"
    )


@router.message(AddMovie.code)
async def fast_add_code(
    message: Message,
    state: FSMContext,
):
    code = message.text.strip()

    if not code:
        await message.answer("❌ Kod bo‘sh bo‘lmasin.")
        return

    old = await movie_by_code(code)

    if old:
        await message.answer(
            "❌ Bu kod allaqachon ishlatilgan."
        )
        return

    await state.update_data(code=code)
    await state.set_state(AddMovie.title)

    await message.answer(
        "🎬 Kino nomini yuboring:"
    )


@router.message(AddMovie.title)
async def fast_add_title(
    message: Message,
    state: FSMContext,
):
    title = message.text.strip()

    if not title:
        await message.answer(
            "❌ Kino nomi bo‘sh bo‘lmasin."
        )
        return

    await state.update_data(title=title)
    await state.set_state(AddMovie.genre)

    await message.answer(
        "🎭 Janrni yuboring:\n"
        "Masalan: Jangari, Komediya, Fantastika"
    )


@router.message(AddMovie.genre)
async def fast_add_genre(
    message: Message,
    state: FSMContext,
):
    genre = message.text.strip()

    if not genre:
        await message.answer(
            "❌ Janr bo‘sh bo‘lmasin."
        )
        return

    await state.update_data(genre=genre)
    await state.set_state(AddMovie.extra)

    await message.answer(
        "📝 Qo‘shimcha ma’lumot yuboring.\n\n"
        "Format:\n"
        "<code>YIL | REYTING | TAVSIF</code>\n\n"
        "Masalan:\n"
        "<code>2026 | 8.5 | Juda qiziqarli film</code>\n\n"
        "Kerak bo‘lmasa <b>skip</b> yozing."
    )


@router.message(AddMovie.extra)
async def fast_add_extra(
    message: Message,
    state: FSMContext,
):
    extra = message.text.strip()

    if extra.lower() == "skip":
        extra = ""

    await state.update_data(extra=extra)

    await finish_add(
        message,
        state,
    )


@router.callback_query(F.data == "admin_normal_add")
async def admin_normal_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Siz admin emassiz.",
            show_alert=True,
        )
        return

    await state.set_state(NormalAdd.source)

    await callback.message.answer(
        "➕ <b>Oddiy kino qo‘shish</b>\n\n"
        "Kanal postini shu yerga forward qiling "
        "yoki kanal postiga reply qilib yuboring."
    )

    await callback.answer()


@router.message(NormalAdd.source)
async def normal_add_source(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    chat_id, message_id = await resolve_source_message(
        message
    )

    if not chat_id or not message_id:
        await message.answer(
            "❌ Kanal postini aniqlab bo‘lmadi."
        )
        return

    await state.update_data(
        source_chat_id=chat_id,
        source_message_id=message_id,
    )

    await state.set_state(NormalAdd.code)

    await message.answer(
        "🔢 Kino kodini yuboring:"
    )


@router.message(NormalAdd.code)
async def normal_add_code(
    message: Message,
    state: FSMContext,
):
    code = message.text.strip()

    if await movie_by_code(code):
        await message.answer(
            "❌ Bu kod allaqachon mavjud."
        )
        return

    await state.update_data(code=code)
    await state.set_state(NormalAdd.title)

    await message.answer(
        "🎬 Kino nomini yuboring:"
    )


@router.message(NormalAdd.title)
async def normal_add_title(
    message: Message,
    state: FSMContext,
):
    await state.update_data(
        title=message.text.strip()
    )

    await state.set_state(NormalAdd.genre)

    await message.answer(
        "🎭 Janrni yuboring:"
    )


@router.message(NormalAdd.genre)
async def normal_add_genre(
    message: Message,
    state: FSMContext,
):
    await state.update_data(
        genre=message.text.strip()
    )

    await state.set_state(NormalAdd.extra)

    await message.answer(
        "📝 Qo‘shimcha ma’lumot:\n"
        "<code>YIL | REYTING | TAVSIF</code>\n\n"
        "Kerak bo‘lmasa <b>skip</b>."
    )


@router.message(NormalAdd.extra)
async def normal_add_extra(
    message: Message,
    state: FSMContext,
):
    extra = message.text.strip()

    if extra.lower() == "skip":
        extra = ""

    await state.update_data(extra=extra)

    await finish_add(
        message,
        state,
    )


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Siz admin emassiz.",
            show_alert=True,
        )
        return

    try:
        movies = await db_execute(
            sb.table("movies")
            .select("id", count="exact")
        )

        users = await db_execute(
            sb.table("users")
            .select("id", count="exact")
        )

        favorites = await db_execute(
            sb.table("favorites")
            .select("id", count="exact")
        )

        await callback.message.answer(
            "📊 <b>Statistika</b>\n\n"
            f"🎬 Kinolar: {movies.count or 0}\n"
            f"👥 Foydalanuvchilar: {users.count or 0}\n"
            f"❤️ Sevimlilar: {favorites.count or 0}"
        )

    except Exception:
        log.exception("admin_stats error")

        await callback.message.answer(
            "❌ Statistikani olishda xatolik."
        )

    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Siz admin emassiz.",
            show_alert=True,
        )
        return

    try:
        result = await db_execute(
            sb.table("users")
            .select("id", count="exact")
        )

        await callback.message.answer(
            f"👥 Foydalanuvchilar soni: "
            f"<b>{result.count or 0}</b>"
        )

    except Exception:
        await callback.message.answer(
            "❌ Xatolik."
        )

    await callback.answer()


@router.callback_query(F.data == "admin_movies")
async def admin_movies(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Siz admin emassiz.",
            show_alert=True,
        )
        return

    try:
        result = await db_execute(
            sb.table("movies")
            .select("id,code,title,views")
            .order("id", desc=True)
            .limit(30)
        )

        movies = result.data or []

        if not movies:
            await callback.message.answer(
                "📋 Kinolar yo‘q."
            )
            await callback.answer()
            return

        text = "📋 <b>Oxirgi kinolar</b>\n\n"

        for movie in movies:
            text += (
                f"🆔 {movie.get('id')}\n"
                f"🎬 {safe(movie.get('title'))}\n"
                f"🔢 <code>{safe(movie.get('code'))}</code>\n"
                f"👁 {movie.get('views') or 0}\n\n"
            )

        await callback.message.answer(text)

    except Exception:
        log.exception("admin_movies error")

        await callback.message.answer(
            "❌ Kinolarni olishda xatolik."
        )

    await callback.answer()


@router.callback_query(F.data == "admin_delete")
async def admin_delete(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Siz admin emassiz.",
            show_alert=True,
        )
        return

    await state.set_state(DeleteMovie.waiting)

    await callback.message.answer(
        "🗑 O‘chiriladigan kino kodini yuboring:"
    )

    await callback.answer()


@router.message(DeleteMovie.waiting)
async def delete_waiting(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    code = message.text.strip()

    movie = await movie_by_code(code)

    if not movie:
        await message.answer(
            "❌ Kino topilmadi."
        )
        await state.clear()
        return

    await state.update_data(
        movie_id=int(movie["id"])
    )

    await state.set_state(DeleteMovie.confirm)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha, o‘chirish",
                    callback_data="delete_yes",
                ),
                InlineKeyboardButton(
                    text="❌ Yo‘q",
                    callback_data="delete_no",
                ),
            ]
        ]
    )

    await message.answer(
        f"🗑 <b>{safe(movie.get('title'))}</b>\n\n"
        "Haqiqatan o‘chirasizmi?",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "delete_yes")
async def delete_yes(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    data = await state.get_data()
    movie_id = data.get("movie_id")

    if not movie_id:
        await state.clear()
        await callback.answer(
            "❌ Kino topilmadi.",
            show_alert=True,
        )
        return

    try:
        await db_execute(
            sb.table("favorites")
            .delete()
            .eq("movie_id", movie_id)
        )

        await db_execute(
            sb.table("movies")
            .delete()
            .eq("id", movie_id)
        )

        await callback.message.edit_text(
            "✅ Kino o‘chirildi."
        )

    except Exception:
        log.exception("delete movie error")

        await callback.message.answer(
            "❌ O‘chirishda xatolik."
        )

    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "delete_no")
async def delete_no(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.edit_text(
        "❌ O‘chirish bekor qilindi."
    )

    await callback.answer()


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    await state.set_state(Broadcast.waiting)

    await callback.message.answer(
        "📢 Reklama uchun xabarni shu yerga yuboring.\n\n"
        "Bot xabarni foydalanuvchilarga <b>copy</b> qilib "
        "yuboradi."
    )

    await callback.answer()


@router.message(Broadcast.waiting)
async def broadcast_message(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        result = await db_execute(
            sb.table("users")
            .select("telegram_id")
        )

        users = result.data or []

        sent = 0
        failed = 0
        blocked = 0

        await message.answer(
            f"📢 Reklama boshlandi.\n"
            f"👥 Foydalanuvchilar: {len(users)}"
        )

        for row in users:
            user_id = row.get("telegram_id")

            if not user_id:
                continue

            try:
                await message.bot.copy_message(
                    chat_id=int(user_id),
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )

                sent += 1

                await asyncio.sleep(0.06)

            except TelegramRetryAfter as e:
                await asyncio.sleep(
                    e.retry_after + 1
                )

                try:
                    await message.bot.copy_message(
                        chat_id=int(user_id),
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                    )

                    sent += 1

                except Exception:
                    failed += 1

            except TelegramForbiddenError:
                blocked += 1

            except Exception:
                failed += 1

        await message.answer(
            "📢 <b>Reklama tugadi</b>\n\n"
            f"✅ Yuborildi: {sent}\n"
            f"❌ Xato: {failed}\n"
            f"🚫 Bloklagan: {blocked}"
        )

    except Exception:
        log.exception("broadcast error")

        await message.answer(
            "❌ Reklamada xatolik yuz berdi."
        )

    await state.clear()


@router.callback_query(F.data == "admin_force")
async def admin_force(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Kanal qo‘shish",
                    callback_data="force_add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➖ Kanal o‘chirish",
                    callback_data="force_remove",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Kanallar",
                    callback_data="force_list",
                )
            ],
        ]
    )

    await callback.message.answer(
        "📢 <b>Majburiy obuna</b>",
        reply_markup=keyboard,
    )

    await callback.answer()


@router.callback_query(F.data == "force_list")
async def force_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    channels = await get_force_channels()

    if not channels:
        await callback.message.answer(
            "📋 Majburiy obuna kanallari yo‘q."
        )
        await callback.answer()
        return

    text = "📋 <b>Majburiy obuna kanallari</b>\n\n"

    for i, ch in enumerate(channels, 1):
        text += (
            f"{i}. {safe(ch.get('title') or 'Kanal')}\n"
            f"🆔 <code>{ch.get('chat_id')}</code>\n"
            f"🔗 {safe(ch.get('username') or '-')}\n\n"
        )

    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "force_add")
async def force_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    await state.set_state(
        ForceChannel.add_source
    )

    await callback.message.answer(
        "➕ Kanal postini shu yerga forward qiling.\n\n"
        "Bot kanalning haqiqiy chat ID'sini avtomatik oladi."
    )

    await callback.answer()


@router.message(ForceChannel.add_source)
async def force_add_source(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    chat_id, _ = await resolve_source_message(
        message
    )

    if not chat_id:
        await message.answer(
            "❌ Kanal aniqlanmadi."
        )
        return

    try:
        chat = await message.bot.get_chat(chat_id)

        username = chat.username or ""
        title = chat.title or "Kanal"

        await state.update_data(
            chat_id=int(chat_id),
            username=username,
            title=title,
        )

        await state.set_state(
            ForceChannel.add_link
        )

        if username:
            await message.answer(
                f"✅ Kanal topildi:\n\n"
                f"📢 {safe(title)}\n"
                f"🔗 @{safe(username)}\n\n"
                "Invite link kerak bo‘lmasa <b>skip</b> yozing."
            )
        else:
            await message.answer(
                f"✅ <b>{safe(title)}</b> topildi.\n\n"
                "🔗 Private kanal uchun invite link yuboring."
            )

    except Exception:
        log.exception("force add source error")

        await message.answer(
            "❌ Kanalni aniqlashda xatolik."
        )


@router.message(ForceChannel.add_link)
async def force_add_link(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    invite_link = message.text.strip()

    if invite_link.lower() == "skip":
        invite_link = ""

    try:
        await db_execute(
            sb.table("force_sub_channels")
            .upsert(
                {
                    "chat_id": int(data["chat_id"]),
                    "username": data.get("username") or None,
                    "title": data.get("title") or "Kanal",
                    "invite_link": invite_link or None,
                    "enabled": True,
                },
                on_conflict="chat_id",
            )
        )

        await message.answer(
            "✅ Majburiy obuna kanali qo‘shildi."
        )

    except Exception:
        log.exception("force add db error")

        await message.answer(
            "❌ Kanalni database'ga qo‘shib bo‘lmadi."
        )

    await state.clear()


@router.callback_query(F.data == "force_remove")
async def force_remove(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )
        return

    await state.set_state(
        ForceChannel.remove
    )

    await callback.message.answer(
        "➖ O‘chiriladigan kanalning chat ID'sini yuboring:"
    )

    await callback.answer()


@router.message(ForceChannel.remove)
async def force_remove_message(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        chat_id = int(message.text.strip())

        result = await db_execute(
            sb.table("force_sub_channels")
            .delete()
            .eq("chat_id", chat_id)
        )

        await message.answer(
            "✅ Kanal o‘chirildi."
        )

    except Exception:
        log.exception("force remove error")

        await message.answer(
            "❌ Kanalni o‘chirishda xatolik."
        )

    await state.clear()


@router.message(Command("cancel"))
async def cancel_command(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "❌ Joriy amal bekor qilindi.",
        reply_markup=main_keyboard(),
    )


@router.message()
async def fallback(message: Message):
    await register_user(message.from_user)

    if not await require_sub(message):
        return

    if message.text:
        movies = await search_movies(
            message.text.strip()
        )

        if movies:
            for movie in movies[:5]:
                fav = await is_favorite(
                    message.from_user.id,
                    int(movie["id"]),
                )

                await message.answer(
                    movie_card(movie),
                    reply_markup=movie_keyboard(
                        int(movie["id"]),
                        fav,
                    ),
                )

            return

    await message.answer(
        "👇 Menyudan kerakli bo‘limni tanlang.",
        reply_markup=main_keyboard(),
    )


async def main():
    log.info("Bot ishga tushmoqda...")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher()
    dp.include_router(router)

    try:
        me = await bot.get_me()

        log.info(
            "Bot: @%s | id=%s",
            me.username,
            me.id,
        )

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
