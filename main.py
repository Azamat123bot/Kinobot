from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from html import escape
from typing import Any, Callable, Coroutine, Dict, List, Optional, TypeVar, Union
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import Client, create_client

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
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
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

load_dotenv()

# ============================================================================
# SOZLAMALAR
# ============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

# Performans sozlamalari
SEARCH_LIMIT = 20          # qidiruv natijalari limiti
TOP_LIMIT = 10             # TOP kinolar
NEW_LIMIT = 15             # Yangi kinolar
FAV_LIMIT = 50             # Sevimlilar limiti
ADMIN_LIST_LIMIT = 35      # Admin ro'yxati limiti
CHANNEL_CACHE_TTL = 120    # majburiy kanallar cache (soniya)
GENRE_CACHE_TTL = 300      # janrlar cache (soniya)
ACTIVITY_UPDATE_INTERVAL = 60  # last_activity yangilanish oralig'i (soniya)
BROADCAST_DELAY = 0.04     # reklama orasidagi kutish (20 msg/s)
MESSAGE_PART_LIMIT = 4000  # Telegram 4096 dan xavfsiz chegara

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("kino_bot")

supabase: Optional[Client] = None
UZ_TZ = ZoneInfo("Asia/Tashkent")

T = TypeVar("T")


# ============================================================================
# FSM HOLATLAR
# ============================================================================

class AddMovie(StatesGroup):
    code = State()
    title = State()
    genre = State()
    extra = State()


class NormalAddMovie(StatesGroup):
    code = State()
    title = State()
    genre = State()
    extra = State()
    source = State()


class SearchCode(StatesGroup):
    waiting = State()


class SearchTitle(StatesGroup):
    waiting = State()


class AdminDelete(StatesGroup):
    waiting = State()


class Broadcast(StatesGroup):
    waiting = State()


class ForceChannelAdd(StatesGroup):
    waiting = State()


class ForceChannelDelete(StatesGroup):
    waiting = State()


# ============================================================================
# TTL CACHE - bazaga ortiqcha so'rov yubormaslik uchun
# ============================================================================

class TTLCache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._data: Dict[str, tuple[float, Any]] = {}

    async def get(
        self,
        key: str,
        loader: Callable[[], Coroutine[Any, Any, T]],
    ) -> T:
        now = time.monotonic()
        item = self._data.get(key)
        if item is not None and now - item[0] < self.ttl:
            return item[1]
        value = await loader()
        self._data[key] = (now, value)
        return value

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)


channel_cache = TTLCache(CHANNEL_CACHE_TTL)
genre_cache = TTLCache(GENRE_CACHE_TTL)


# ============================================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================================

def get_sb() -> Client:
    if supabase is None:
        raise RuntimeError("Supabase ulanmagan.")
    return supabase


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uz_hour() -> int:
    return datetime.now(UZ_TZ).hour


def safe(value: Any) -> str:
    if value is None:
        return ""
    return escape(str(value))


def sanitize(value: str) -> str:
    """Foydalanuvchi kiritmasini xavfsiz qiladi (SQL LIKE wildcards o'chiriladi)."""
    value = (value or "").strip()
    value = re.sub(r"[%_\\]", "", value)
    return value[:100]


def or_safe(value: str) -> str:
    """PostgREST `or_` sintaksisidagi maxsus belgilarni escape qiladi."""
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def split_text(text: str, limit: int = MESSAGE_PART_LIMIT) -> List[str]:
    """Uzun xabarni Telegram limitiga mos bo'laklarga ajratadi."""
    parts: List[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


async def send_long(
    message_or_callback: Union[Message, CallbackQuery],
    text: str,
    **kwargs: Any,
) -> None:
    parts = split_text(text)
    target = (
        message_or_callback.message
        if isinstance(message_or_callback, CallbackQuery)
        else message_or_callback
    )
    for part in parts[:-1]:
        await target.answer(part)
    await target.answer(parts[-1], **kwargs)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def user_name(obj: Union[Message, CallbackQuery]) -> str:
    user = obj.from_user
    if not user:
        return "Do'st"
    return safe(user.first_name or user.username or "Do'st")


def greeting(name: str) -> str:
    hour = uz_hour()
    if 5 <= hour < 12:
        text = "Xayrli tong"
    elif 12 <= hour < 17:
        text = "Xayrli kun"
    elif 17 <= hour < 22:
        text = "Xayrli kech"
    else:
        text = "Xayrli tun"
    return f"{text}, <b>{name}</b>! 👋"


# ============================================================================
# KLAVIATURALAR
# ============================================================================

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Kino qidirish"), KeyboardButton(text="🔢 Kod orqali")],
            [KeyboardButton(text="🎭 Janrlar"), KeyboardButton(text="🔥 TOP")],
            [KeyboardButton(text="🆕 Yangilar"), KeyboardButton(text="❤️ Sevimlilar")],
            [KeyboardButton(text="ℹ️ Yordam")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Kino nomi yoki kod yozing...",
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ Tezkor qo'shish", callback_data="admin_fast_add"),
            InlineKeyboardButton(text="➕ Oddiy qo'shish", callback_data="admin_normal_add"),
        ],
        [
            InlineKeyboardButton(text="🗑 O'chirish", callback_data="admin_delete"),
            InlineKeyboardButton(text="📋 Ro'yxat", callback_data="admin_list"),
        ],
        [
            InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats"),
            InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users"),
        ],
        [InlineKeyboardButton(text="📢 Reklama", callback_data="admin_broadcast")],
        [
            InlineKeyboardButton(text="📢 Majburiy obuna", callback_data="admin_force_sub"),
            InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="admin_settings"),
        ],
    ])


def force_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="force_add"),
            InlineKeyboardButton(text="🗑 Kanal o'chirish", callback_data="force_delete"),
        ],
        [InlineKeyboardButton(text="📋 Kanallar", callback_data="force_list")],
        [InlineKeyboardButton(text="🏠 Admin panel", callback_data="admin_home")],
    ])


def movie_actions_kb(movie_id: int, favorite: bool = False) -> InlineKeyboardMarkup:
    fav_text = "💔 Olib tashlash" if favorite else "❤️ Sevimli"
    fav_data = f"unfav_{movie_id}" if favorite else f"fav_{movie_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Tomosha qilish", callback_data=f"watch_{movie_id}")],
        [
            InlineKeyboardButton(text=fav_text, callback_data=fav_data),
            InlineKeyboardButton(text="🏠 Menyuga", callback_data="back_main"),
        ],
    ])


def confirm_delete_kb(movie_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha, o'chir", callback_data=f"del_yes_{movie_id}"),
            InlineKeyboardButton(text="❌ Yo'q", callback_data="del_no"),
        ],
    ])


def skip_extra_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data="skip_extra")],
    ])


def source_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="source_cancel")],
    ])


# ============================================================================
# MAJBURIY OBUNA
# ============================================================================

async def _load_force_channels() -> List[Dict[str, Any]]:
    result = (
        get_sb()
        .table("force_channels")
        .select("*")
        .eq("is_active", True)
        .order("id")
        .execute()
    )
    return result.data or []


async def get_force_channels() -> List[Dict[str, Any]]:
    try:
        return await channel_cache.get("channels", _load_force_channels)
    except Exception as e:
        logger.error("get_force_channels: %s", e)
        return []


async def check_one_channel(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        ):
            return True
        if member.status == ChatMemberStatus.RESTRICTED:
            return bool(getattr(member, "is_member", False))
        return False
    except Exception as e:
        logger.warning("check_one_channel %s: %s", chat_id, e)
        return False


async def check_sub(bot: Bot, user_id: int) -> bool:
    channels = await get_force_channels()
    if not channels:
        return True
    results = await asyncio.gather(
        *[
            check_one_channel(bot, int(channel["chat_id"]), user_id)
            for channel in channels
        ],
        return_exceptions=True,
    )
    return all(result is True for result in results)


def channel_link(channel: Dict[str, Any]) -> Optional[str]:
    username = (channel.get("username") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}"
    invite = (channel.get("invite_link") or "").strip()
    if invite:
        return invite
    chat_id = str(channel.get("chat_id") or "")
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}"
    return None


def force_sub_kb(channels: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for index, channel in enumerate(channels, 1):
        link = channel_link(channel)
        title = channel.get("title") or channel.get("username") or f"Kanal {index}"
        if link:
            buttons.append([
                InlineKeyboardButton(text=f"📢 {str(title)[:45]}", url=link),
            ])
    buttons.append([
        InlineKeyboardButton(text="✅ Tekshirdim", callback_data="check_sub"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_force_subscription(message: Message) -> None:
    channels = await get_force_channels()
    if not channels:
        return
    await message.answer(
        "📢 <b>Botdan foydalanish uchun</b>\n\n"
        "Quyidagi kanallarga obuna bo'ling:\n\n"
        "1. Kanallarga obuna bo'ling\n"
        "2. Keyin <b>✅ Tekshirdim</b> tugmasini bosing.",
        reply_markup=force_sub_kb(channels),
    )


# ============================================================================
# FOYDALANUVCHI RO'YXATDAN O'TKAZISH (upsert - race condition'siz)
# ============================================================================

_last_activity: Dict[int, float] = {}


async def register_user(message: Message) -> None:
    user = message.from_user
    if not user:
        return

    now = time.monotonic()
    if now - _last_activity.get(user.id, 0) < ACTIVITY_UPDATE_INTERVAL:
        return
    _last_activity[user.id] = now

    try:
        sb = get_sb()
        data = {
            "telegram_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_activity": now_iso(),
            "is_blocked": False,
        }
        # select-then-insert o'rniga bitta upsert: parallel startlarda ham xatosiz
        sb.table("users").upsert(data, on_conflict="telegram_id").execute()
    except Exception as e:
        logger.warning("register_user: %s", e)


async def mark_blocked(user_id: int) -> None:
    try:
        (
            get_sb()
            .table("users")
            .update({"is_blocked": True})
            .eq("telegram_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.warning("mark_blocked: %s", e)


# ============================================================================
# KINO SO'ROVLARI
# ============================================================================

async def movie_by_code(code: str) -> Optional[Dict[str, Any]]:
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


async def movie_by_id(movie_id: int) -> Optional[Dict[str, Any]]:
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
    """ATOMIK increment - RPC orqali (read-write race condition'siz)."""
    try:
        get_sb().rpc("increment_views", {"p_movie_id": movie_id}).execute()
    except Exception as e:
        logger.warning("inc_views: %s", e)


async def is_fav(telegram_id: int, movie_id: int) -> bool:
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
        logger.warning("is_fav: %s", e)
        return False


def movie_card(movie: Dict[str, Any]) -> str:
    title = safe(movie.get("title") or "Noma'lum")
    alternative_title = movie.get("alternative_title")
    year = movie.get("year") or "—"
    genre = safe(movie.get("genre") or "—")
    rating = movie.get("rating")
    views = int(movie.get("views") or 0)
    description = movie.get("description") or ""
    code = safe(movie.get("code") or "—")
    rating_text = str(rating) if rating is not None else "—"

    lines = [f"🎬 <b>{title}</b>"]
    if alternative_title:
        lines.append(f"📌 <i>{safe(alternative_title)}</i>")
    lines.extend([
        "",
        f"🔢 Kod: <code>{code}</code>",
        f"📅 {year}  ·  🎭 {genre}",
        f"⭐ {rating_text}  ·  👁 {views:,}",
    ])
    if description:
        lines.extend(["", f"📝 {safe(description)}"])
    return "\n".join(lines)


async def _load_genres() -> List[Dict[str, Any]]:
    result = (
        get_sb()
        .table("genres")
        .select("id,name")
        .order("name")
        .execute()
    )
    return result.data or []


async def get_genres() -> List[Dict[str, Any]]:
    try:
        return await genre_cache.get("genres", _load_genres)
    except Exception as e:
        logger.error("get_genres: %s", e)
        return []


async def ensure_genres() -> None:
    genres = [
        "Action", "Comedy", "Horror", "Romance", "Sci-Fi", "Fantasy",
        "Drama", "Thriller", "Family", "Animation", "Crime", "Adventure",
        "Mystery", "War", "History",
    ]
    for genre in genres:
        try:
            get_sb().table("genres").upsert({"name": genre}, on_conflict="name").execute()
        except Exception as e:
            logger.warning("genre %s: %s", genre, e)
    genre_cache.invalidate("genres")


async def ensure_custom_genres(genre_text: str) -> None:
    names = [x.strip() for x in genre_text.split(",") if x.strip()]
    for name in names:
        try:
            get_sb().table("genres").upsert(
                {"name": name[:100]}, on_conflict="name"
            ).execute()
        except Exception as e:
            logger.warning("custom genre %s: %s", name, e)
    genre_cache.invalidate("genres")


async def save_movie(data: Dict[str, Any]) -> tuple[bool, str]:
    try:
        result = get_sb().table("movies").insert(data).execute()
        if not result.data:
            return False, "Bazaga qo'shish javobi bo'sh."
        return True, ""
    except Exception as e:
        logger.exception("save_movie")
        return False, str(e)


def get_source_message_info(message: Message) -> Optional[tuple[int, int]]:
    replied = message.reply_to_message
    if not replied:
        return None
    origin = getattr(replied, "forward_origin", None)
    if origin:
        origin_chat = getattr(origin, "chat", None)
        origin_message_id = getattr(origin, "message_id", None)
        if origin_chat is not None and origin_message_id:
            return int(origin_chat.id), int(origin_message_id)
    return int(replied.chat.id), int(replied.message_id)


async def finish_add(
    message: Message,
    state: FSMContext,
    data: Dict[str, Any],
) -> None:
    required = ["code", "title", "genre", "channel_id", "message_id"]
    missing = [x for x in required if data.get(x) in (None, "")]
    if missing:
        await message.answer(
            "❌ Ma'lumot yetishmayapti:\n\n"
            f"<code>{safe(', '.join(missing))}</code>"
        )
        await state.clear()
        return

    code = str(data["code"]).strip()
    existing = await movie_by_code(code)
    if existing:
        await message.answer(
            "❌ Bu kod allaqachon mavjud.\n\n"
            f"🔢 <code>{safe(code)}</code>\n"
            f"🎬 <b>{safe(existing.get('title'))}</b>\n\n"
            "Boshqa kod yuboring."
        )
        return

    payload = {
        "code": code,
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

    ok, error = await save_movie(payload)
    if not ok:
        await message.answer(
            "❌ Kino bazaga qo'shilmadi.\n\n"
            f"<code>{safe(error[:1500])}</code>"
        )
        return

    await ensure_custom_genres(payload["genre"])
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


async def parse_extra(
    raw: str,
) -> tuple[Optional[int], Optional[float], Optional[str], Optional[str]]:
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return None, None, None, None

    parts = [x.strip() for x in raw.split("|")]
    year: Optional[int] = None
    rating: Optional[float] = None
    description: Optional[str] = None

    if len(parts) >= 1 and parts[0] and parts[0] != "-":
        try:
            year = int(parts[0])
        except ValueError:
            return None, None, None, "Yil noto'g'ri."
        if year < 1800 or year > datetime.now().year + 2:
            return None, None, None, "Yil noto'g'ri."

    if len(parts) >= 2 and parts[1] and parts[1] != "-":
        try:
            rating = float(parts[1].replace(",", "."))
        except ValueError:
            return None, None, None, "Reyting noto'g'ri."
        if not 0 <= rating <= 10:
            return None, None, None, "Reyting 0 dan 10 gacha bo'lishi kerak."

    if len(parts) >= 3 and parts[2] and parts[2] != "-":
        description = parts[2][:1000]

    return year, rating, description, None


async def search_movies(query: str) -> List[Dict[str, Any]]:
    query = sanitize(query)
    if len(query) < 2:
        return []
    q = or_safe(query)
    try:
        result = (
            get_sb()
            .table("movies")
            .select("*")
            .or_(f"title.ilike.%{q}%,alternative_title.ilike.%{q}%,code.eq.{q}")
            .order("views", desc=True)
            .limit(SEARCH_LIMIT)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("search_movies: %s", e)
        return []


def movie_list_kb(rows: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=str(row.get("title") or "Noma'lum")[:32],
                              callback_data=f"movie_{row['id']}")]
        for row in rows
    ]
    buttons.append([InlineKeyboardButton(text="🏠 Menyuga", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_movie_result(
    target: Message,
    user_id: int,
    movie: Dict[str, Any],
) -> None:
    fav = await is_fav(user_id, int(movie["id"]))
    await target.answer(
        movie_card(movie),
        reply_markup=movie_actions_kb(int(movie["id"]), fav),
    )


# ============================================================================
# MIDDLEWARELAR
# ============================================================================

class ThrottlingMiddleware(BaseMiddleware):
    """Oddiy foydalanuvchilar uchun anti-flood (adminlar cheklanmaydi)."""

    def __init__(self, rate_limit: float = 1.0) -> None:
        self.rate_limit = rate_limit
        self._last: Dict[int, float] = {}

    async def __call__(self, handler, event, data):  # type: ignore[override]
        user = data.get("event_from_user")
        if user is not None and not is_admin(user.id):
            now = time.monotonic()
            last = self._last.get(user.id, 0.0)
            if now - last < self.rate_limit:
                if isinstance(event, Message):
                    try:
                        await event.answer("⏳ Iltimos, biroz kuting...")
                    except Exception:
                        pass
                return None
            self._last[user.id] = now
        return await handler(event, data)


# ============================================================================
# HANDLERLAR
# ============================================================================

router = Router()


# ---------- START / HELP ----------

@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    await register_user(message)

    if not message.from_user:
        return

    if not await check_sub(bot, message.from_user.id):
        await message.answer(
            f"{greeting(user_name(message))}\n\n"
            "📢 Botdan foydalanish uchun majburiy kanallarga obuna bo'ling."
        )
        await send_force_subscription(message)
        return

    # Deep-link: /start k_KOD
    payload = (message.text or "").split(maxsplit=1)
    if len(payload) == 2 and payload[1].startswith("k_"):
        code = payload[1][2:]
        movie = await movie_by_code(code)
        if movie:
            await show_movie_result(message, message.from_user.id, movie)
            return

    await message.answer(
        f"{greeting(user_name(message))}\n\n"
        "🎬 <b>Kino Bot</b>ga xush kelibsiz!\n\n"
        "Kino nomi yoki kodini yozing.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Yordam")
async def cmd_help(message: Message) -> None:
    await register_user(message)
    await message.answer(
        f"{greeting(user_name(message))}\n\n"
        "ℹ️ <b>Botdan foydalanish</b>\n\n"
        "🔢 Kod orqali — kino kodini yozing\n"
        "🎬 Kino qidirish — nomini yozing\n"
        "🎭 Janrlar — janr bo'yicha qidiring\n"
        "🔥 TOP — mashhur kinolar\n"
        "🆕 Yangilar — yangi kinolar\n"
        "❤️ Sevimlilar — saqlangan kinolar\n\n"
        f"💬 Yordam: {safe(SUPPORT_USERNAME)}",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "check_sub")
async def on_check_sub(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user:
        await callback.answer()
        return

    if await check_sub(bot, callback.from_user.id):
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            f"{greeting(user_name(callback))}\n\n"
            "✅ <b>Obuna tasdiqlandi!</b>\n\n"
            "Endi botdan foydalanishingiz mumkin.",
            reply_markup=main_menu_kb(),
        )
        await callback.answer("✅ Obuna tasdiqlandi!")
    else:
        await callback.answer(
            "❌ Hali barcha kanallarga obuna bo'lmagansiz.",
            show_alert=True,
        )


@router.callback_query(F.data == "back_main")
async def on_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(
        f"🏠 <b>Asosiy menyu</b>\n\n{greeting(user_name(callback))}",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


# ---------- KOD BO'YICHA QIDIRUV ----------

@router.message(F.text == "🔢 Kod orqali")
async def search_code_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await register_user(message)
    if not message.from_user:
        return
    if not await check_sub(bot, message.from_user.id):
        await send_force_subscription(message)
        return

    await state.clear()
    await state.set_state(SearchCode.waiting)
    await message.answer("🔢 Kino kodini yuboring:", reply_markup=cancel_kb())


@router.message(StateFilter(SearchCode.waiting), F.text)
async def search_code_process(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    movie = await movie_by_code(message.text.strip())
    if not movie:
        await message.answer("❌ Bunday kodli kino topilmadi.")
        return

    await state.clear()
    await show_movie_result(message, message.from_user.id, movie)


# ---------- NOM BO'YICHA QIDIRUV ----------

@router.message(F.text == "🎬 Kino qidirish")
async def search_title_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await register_user(message)
    if not message.from_user:
        return
    if not await check_sub(bot, message.from_user.id):
        await send_force_subscription(message)
        return

    await state.clear()
    await state.set_state(SearchTitle.waiting)
    await message.answer("🎬 Kino nomini yozing:", reply_markup=cancel_kb())


@router.message(StateFilter(SearchTitle.waiting), F.text)
async def search_title_process(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return

    query = sanitize(message.text or "")
    if len(query) < 2:
        await message.answer("❌ Kamida 2 ta belgi yozing.")
        return

    rows = await search_movies(query)
    if not rows:
        await message.answer("❌ Hech narsa topilmadi.")
        return

    await state.clear()

    if len(rows) == 1:
        await show_movie_result(message, message.from_user.id, rows[0])
        return

    text = f"🔍 <b>{len(rows)} ta natija:</b>\n\n"
    for row in rows:
        text += f"• {safe(row.get('title'))} ({row.get('year') or '—'})\n"

    await message.answer(text, reply_markup=movie_list_kb(rows))


@router.callback_query(F.data.startswith("movie_"))
async def show_movie(callback: CallbackQuery, bot: Bot) -> None:
    if not await check_sub(bot, callback.from_user.id):
        await callback.answer(
            "❌ Avval majburiy kanallarga obuna bo'ling.", show_alert=True
        )
        return

    try:
        movie_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await callback.answer("❌ Noto'g'ri ID.", show_alert=True)
        return

    movie = await movie_by_id(movie_id)
    if not movie:
        await callback.answer("❌ Kino topilmadi.", show_alert=True)
        return

    await show_movie_result(callback.message, callback.from_user.id, movie)
    await callback.answer()


# ---------- TOMOSHA QILISH ----------

@router.callback_query(F.data.startswith("watch_"))
async def watch_movie(callback: CallbackQuery, bot: Bot) -> None:
    if not await check_sub(bot, callback.from_user.id):
        await callback.answer(
            "❌ Avval barcha majburiy kanallarga obuna bo'ling.", show_alert=True
        )
        return

    try:
        movie_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await callback.answer("❌ Noto'g'ri ID.", show_alert=True)
        return

    movie = await movie_by_id(movie_id)
    if not movie:
        await callback.answer("❌ Kino topilmadi.", show_alert=True)
        return

    try:
        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=int(movie["channel_id"]),
            message_id=int(movie["message_id"]),
        )
        await inc_views(movie_id)
        await callback.answer("🎬 Kino yuborildi!")
    except TelegramForbiddenError:
        await callback.answer("❌ Botga yozish imkoniyati yo'q.", show_alert=True)
    except TelegramBadRequest:
        await callback.answer(
            "❌ Kino xabari mavjud emas yoki bot manba kanalga kira olmayapti.",
            show_alert=True,
        )
    except Exception:
        logger.exception("watch_movie")
        await callback.answer("❌ Kino yuborishda xatolik.", show_alert=True)


# ---------- SEVIMLILAR ----------

@router.callback_query(F.data.startswith("fav_"))
async def add_fav(callback: CallbackQuery) -> None:
    try:
        movie_id = int(callback.data.split("_", 1)[1])
        if not await is_fav(callback.from_user.id, movie_id):
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
            reply_markup=movie_actions_kb(movie_id, True)
        )
        await callback.answer("❤️ Sevimlilarga qo'shildi!")
    except TelegramBadRequest:
        await callback.answer()
    except Exception as e:
        logger.error("add_fav: %s", e)
        await callback.answer("❌ Sevimliga qo'shishda xatolik.", show_alert=True)


@router.callback_query(F.data.startswith("unfav_"))
async def remove_fav(callback: CallbackQuery) -> None:
    try:
        movie_id = int(callback.data.split("_", 1)[1])
        (
            get_sb()
            .table("favorites")
            .delete()
            .eq("telegram_id", callback.from_user.id)
            .eq("movie_id", movie_id)
            .execute()
        )
        await callback.message.edit_reply_markup(
            reply_markup=movie_actions_kb(movie_id, False)
        )
        await callback.answer("💔 Sevimlilardan olib tashlandi.")
    except TelegramBadRequest:
        await callback.answer()
    except Exception as e:
        logger.error("remove_fav: %s", e)
        await callback.answer("❌ Sevimlidan olib tashlashda xatolik.", show_alert=True)


@router.message(F.text == "❤️ Sevimlilar")
async def show_favs(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not message.from_user:
        return
    if not await check_sub(bot, message.from_user.id):
        await send_force_subscription(message)
        return

    try:
        # Bitta JOIN so'rov - N+1 muammosiz
        result = (
            get_sb()
            .table("favorites")
            .select("movies(*)")
            .eq("telegram_id", message.from_user.id)
            .order("id", desc=True)
            .limit(FAV_LIMIT)
            .execute()
        )
        movies = [row["movies"] for row in (result.data or []) if row.get("movies")]
    except Exception as e:
        logger.exception("show_favs: %s", e)
        await message.answer(
            "❌ Sevimlilarni olishda xatolik.", reply_markup=main_menu_kb()
        )
        return

    if not movies:
        await message.answer(
            "❤️ <b>Sevimlilar</b>\n\nHozircha sevimli kino yo'q.",
            reply_markup=main_menu_kb(),
        )
        return

    text = f"❤️ <b>{user_name(message)}, sevimli kinolaringiz:</b>\n\n"
    for movie in movies:
        text += f"• {safe(movie.get('title'))}\n"

    await message.answer(text, reply_markup=movie_list_kb(movies))


# ---------- JANRLAR ----------

@router.message(F.text == "🎭 Janrlar")
async def show_genres(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not message.from_user:
        return
    if not await check_sub(bot, message.from_user.id):
        await send_force_subscription(message)
        return

    rows = await get_genres()
    if not rows:
        await message.answer("Janrlar mavjud emas.")
        return

    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for genre in rows:
        row.append(
            InlineKeyboardButton(
                text=str(genre["name"])[:30],
                callback_data=f"genreid_{genre['id']}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🏠 Menyuga", callback_data="back_main")])

    await message.answer(
        "🎭 <b>Janrni tanlang:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("genreid_"))
async def genre_list(callback: CallbackQuery) -> None:
    try:
        genre_id = int(callback.data.split("_", 1)[1])
        genre_result = (
            get_sb()
            .table("genres")
            .select("name")
            .eq("id", genre_id)
            .limit(1)
            .execute()
        )
        if not genre_result.data:
            await callback.answer("❌ Janr topilmadi.", show_alert=True)
            return

        genre = genre_result.data[0]["name"]
        result = (
            get_sb()
            .table("movies")
            .select("*")
            .ilike("genre", f"%{sanitize(genre)}%")
            .order("views", desc=True)
            .limit(30)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error("genre_list: %s", e)
        await callback.answer("❌ Xatolik.", show_alert=True)
        return

    if not rows:
        await callback.answer("Bu janrda kino yo'q.", show_alert=True)
        return

    text = f"🎭 <b>{safe(genre)}</b>\n\n"
    for row in rows:
        text += f"• {safe(row.get('title'))} ({row.get('year') or '—'})\n"

    await send_long(callback.message, text, reply_markup=movie_list_kb(rows))
    await callback.answer()


# ---------- TOP ----------

@router.message(F.text == "🔥 TOP")
async def popular(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not message.from_user:
        return
    if not await check_sub(bot, message.from_user.id):
        await send_force_subscription(message)
        return

    try:
        result = (
            get_sb()
            .table("movies")
            .select("*")
            .order("views", desc=True)
            .limit(TOP_LIMIT)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error("popular: %s", e)
        await message.answer("❌ TOP olishda xatolik.")
        return

    if not rows:
        await message.answer("Hali kino qo'shilmagan.")
        return

    medals = ["🥇", "🥈", "🥉"]
    text = f"🔥 <b>TOP {len(rows)}</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []

    for index, row in enumerate(rows):
        number = medals[index] if index < 3 else f"{index + 1}."
        views = int(row.get("views") or 0)
        title = str(row.get("title") or "Noma'lum")
        text += f"{number} {safe(title)} — {views:,}\n"
        buttons.append([
            InlineKeyboardButton(
                text=f"{number} {title[:28]}",
                callback_data=f"movie_{row['id']}",
            )
        ])

    buttons.append([InlineKeyboardButton(text="🏠 Menyuga", callback_data="back_main")])

    await message.answer(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# ---------- YANGILAR ----------

@router.message(F.text == "🆕 Yangilar")
async def newest(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not message.from_user:
        return
    if not await check_sub(bot, message.from_user.id):
        await send_force_subscription(message)
        return

    try:
        result = (
            get_sb()
            .table("movies")
            .select("*")
            .order("created_at", desc=True)
            .limit(NEW_LIMIT)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error("newest: %s", e)
        await message.answer("❌ Yangi kinolarni olishda xatolik.")
        return

    if not rows:
        await message.answer("Hali kino qo'shilmagan.")
        return

    text = "🆕 <b>Yangi kinolar</b>\n\n"
    for row in rows:
        text += f"• {safe(row.get('title'))} ({row.get('year') or '—'})\n"

    await message.answer(text, reply_markup=movie_list_kb(rows))


# ============================================================================
# ADMIN PANEL
# ============================================================================

@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    await message.answer(
        "👨‍💻 <b>Admin panel</b>\n\n"
        "⚡ Tezkor qo'shish — kanal postiga Reply qilib tez qo'shish.\n"
        "➕ Oddiy qo'shish — ma'lumotlarni bosqichma-bosqich kiritish.\n"
        "📢 Majburiy obuna — bir nechta kanalni boshqarish.\n"
        "📢 Reklama — barcha foydalanuvchilarga yuborish.",
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(F.data == "admin_home")
async def admin_home(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.answer(
        "👨‍💻 <b>Admin panel</b>", reply_markup=admin_menu_kb()
    )
    await callback.answer()


# ---------- TEZKOR QO'SHISH ----------

@router.callback_query(F.data == "admin_fast_add")
async def fast_add_help(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.message.answer(
        "⚡ <b>Tezkor kino qo'shish</b>\n\n"
        "1️⃣ Kino postini kanalga yuboring.\n"
        "2️⃣ O'sha postga Reply qiling.\n"
        "3️⃣ Reply ichiga <code>/add</code> yuboring.\n"
        "4️⃣ Kodni kiriting.\n"
        "5️⃣ Kino nomini kiriting.\n"
        "6️⃣ Janrni kiriting.\n"
        "7️⃣ Yil | Reyting | Tavsifni kiriting yoki o'tkazib yuboring.\n\n"
        "📌 Kanal ID va Message ID avtomatik olinadi."
    )
    await callback.answer()


@router.message(Command("add"))
async def fast_add_start(message: Message, state: FSMContext) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return

    await state.clear()
    source = get_source_message_info(message)
    if not source:
        await message.answer(
            "⚡ <b>Tezkor qo'shish</b>\n\n"
            "Avval kanal postiga Reply qiling.\n\n"
            "Masalan:\n"
            "kanaldagi kino → Reply → <code>/add</code>"
        )
        return

    channel_id, message_id = source
    await state.update_data(channel_id=channel_id, message_id=message_id)
    await state.set_state(AddMovie.code)
    await message.answer(
        "✅ Kino xabari topildi.\n\n"
        f"📢 Kanal ID: <code>{channel_id}</code>\n"
        f"💬 Message ID: <code>{message_id}</code>\n\n"
        "1️⃣ Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.callback_query(F.data == "admin_normal_add")
async def normal_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await state.set_state(NormalAddMovie.code)
    await callback.message.answer(
        "➕ <b>Oddiy kino qo'shish</b>\n\n"
        "Bu usulda ma'lumotlarni birma-bir kiritasiz.\n\n"
        "1️⃣ Kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


async def _handle_add_cancel(
    message: Message,
    state: FSMContext,
    admin: bool = True,
) -> bool:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer(
            "Bekor qilindi.",
            reply_markup=admin_menu_kb() if admin else main_menu_kb(),
        )
        return True
    return False


@router.message(StateFilter(AddMovie.code), F.text)
async def add_code(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    code = message.text.strip()
    if not code:
        await message.answer("❌ Kod bo'sh bo'lmasin.")
        return
    if len(code) > 80:
        await message.answer("❌ Kod 80 belgidan oshmasin.")
        return
    if await movie_by_code(code):
        await message.answer("❌ Bu kod band.\nBoshqa kod yuboring.")
        return

    await state.update_data(code=code)
    await state.set_state(AddMovie.title)
    await message.answer("2️⃣ Kino nomini yuboring:")


@router.message(StateFilter(AddMovie.title), F.text)
async def add_title(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    title = message.text.strip()
    if not title:
        await message.answer("❌ Kino nomi bo'sh bo'lmasin.")
        return

    await state.update_data(title=title[:200])
    await state.set_state(AddMovie.genre)
    await message.answer(
        "3️⃣ Janrni yuboring.\n\n"
        "Masalan:\n"
        "<code>Action</code>\n"
        "<code>Action, Drama</code>"
    )


@router.message(StateFilter(AddMovie.genre), F.text)
async def add_genre(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    genre = message.text.strip()
    if not genre:
        await message.answer("❌ Janr bo'sh bo'lmasin.")
        return

    await state.update_data(genre=genre[:150])
    await state.set_state(AddMovie.extra)
    await message.answer(
        "4️⃣ Ixtiyoriy ma'lumot.\n\n"
        "Format:\n"
        "<code>2024 | 8.5 | Ajoyib film</code>\n\n"
        "Yoki o'tkazib yuboring.",
        reply_markup=skip_extra_kb(),
    )


@router.callback_query(F.data == "skip_extra", StateFilter(AddMovie.extra))
async def skip_extra(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    data = await state.get_data()
    await finish_add(callback.message, state, data)
    await callback.answer()


@router.message(StateFilter(AddMovie.extra), F.text)
async def add_extra(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    year, rating, description, error = await parse_extra(message.text)
    if error:
        await message.answer(
            f"❌ {error}\n\nFormat: <code>2024 | 8.5 | Tavsif</code>"
        )
        return

    await state.update_data(year=year, rating=rating, description=description)
    data = await state.get_data()
    await finish_add(message, state, data)


# ---------- ODDIY QO'SHISH ----------

@router.message(StateFilter(NormalAddMovie.code), F.text)
async def normal_code(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    code = message.text.strip()
    if len(code) > 80:
        await message.answer("❌ Kod juda uzun.")
        return
    if await movie_by_code(code):
        await message.answer("❌ Bu kod band.")
        return

    await state.update_data(code=code)
    await state.set_state(NormalAddMovie.title)
    await message.answer("2️⃣ Kino nomini yuboring:")


@router.message(StateFilter(NormalAddMovie.title), F.text)
async def normal_title(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    title = message.text.strip()
    if not title:
        await message.answer("❌ Nom bo'sh bo'lmasin.")
        return

    await state.update_data(title=title[:200])
    await state.set_state(NormalAddMovie.genre)
    await message.answer("3️⃣ Janrni yuboring:")


@router.message(StateFilter(NormalAddMovie.genre), F.text)
async def normal_genre(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    genre = message.text.strip()
    if not genre:
        await message.answer("❌ Janr bo'sh bo'lmasin.")
        return

    await state.update_data(genre=genre[:150])
    await state.set_state(NormalAddMovie.extra)
    await message.answer(
        "4️⃣ Yil | Reyting | Tavsif\n\n"
        "Masalan:\n"
        "<code>2024 | 8.5 | Ajoyib film</code>\n\n"
        "Yoki o'tkazib yuboring.",
        reply_markup=skip_extra_kb(),
    )


@router.callback_query(F.data == "skip_extra", StateFilter(NormalAddMovie.extra))
async def normal_skip_extra(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.update_data(year=None, rating=None, description=None)
    await state.set_state(NormalAddMovie.source)
    await callback.message.answer(
        "5️⃣ Endi kino joylashgan kanal postini yuboring yoki Forward qiling.\n\n"
        "Bot asl kanal ID va Message ID ni avtomatik oladi.",
        reply_markup=source_cancel_kb(),
    )
    await callback.answer()


@router.message(StateFilter(NormalAddMovie.extra), F.text)
async def normal_extra(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    year, rating, description, error = await parse_extra(message.text)
    if error:
        await message.answer(f"❌ {error}")
        return

    await state.update_data(year=year, rating=rating, description=description)
    await state.set_state(NormalAddMovie.source)
    await message.answer(
        "5️⃣ Endi kino joylashgan kanal postiga Reply qiling "
        "yoki postni Forward qiling.\n\n"
        "Bot kanal ID va Message ID ni avtomatik oladi.",
        reply_markup=cancel_kb(),
    )


@router.message(StateFilter(NormalAddMovie.source))
async def normal_source(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    source = get_source_message_info(message)
    if not source:
        await message.answer(
            "❌ Kino manbasi topilmadi.\n\n"
            "Kanal postiga Reply qiling yoki kanal postini Forward qiling."
        )
        return

    channel_id, message_id = source
    data = await state.get_data()
    data["channel_id"] = channel_id
    data["message_id"] = message_id
    await finish_add(message, state, data)


@router.callback_query(F.data == "source_cancel")
async def source_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("❌ Bekor qilindi.", reply_markup=admin_menu_kb())
    await callback.answer()


# ---------- O'CHIRISH ----------

@router.callback_query(F.data == "admin_delete")
async def delete_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await state.set_state(AdminDelete.waiting)
    await callback.message.answer(
        "🗑 O'chirmoqchi bo'lgan kino kodini yuboring:", reply_markup=cancel_kb()
    )
    await callback.answer()


@router.message(StateFilter(AdminDelete.waiting), F.text)
async def delete_confirm(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    movie = await movie_by_code(message.text.strip())
    if not movie:
        await message.answer("❌ Kino topilmadi.")
        return

    await state.update_data(movie_id=int(movie["id"]))
    await message.answer(
        "⚠️ <b>Rostdan o'chirasizmi?</b>\n\n"
        f"🎬 {safe(movie.get('title'))}\n"
        f"🔢 <code>{safe(movie.get('code'))}</code>",
        reply_markup=confirm_delete_kb(int(movie["id"])),
    )


@router.callback_query(F.data.startswith("del_yes_"))
async def del_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    try:
        movie_id = int(callback.data.split("_")[2])
        # favorites FK cascade bilan avtomatik o'chadi
        result = (
            get_sb()
            .table("movies")
            .delete()
            .eq("id", movie_id)
            .execute()
        )
        if not result.data:
            await callback.answer("❌ Kino topilmadi.", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text("✅ Kino o'chirildi.")
        await callback.answer("O'chirildi.")
    except Exception as e:
        logger.error("del_yes: %s", e)
        await callback.answer("❌ O'chirishda xatolik.", show_alert=True)


@router.callback_query(F.data == "del_no")
async def del_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ O'chirish bekor qilindi.")
    await callback.answer()


# ---------- RO'YXAT / STATISTIKA / FOYDALANUVCHILAR ----------

@router.callback_query(F.data == "admin_list")
@router.message(Command("movies"))
async def admin_list(event: Union[Message, CallbackQuery]) -> None:
    if not is_admin(event.from_user.id):
        return
    try:
        result = (
            get_sb()
            .table("movies")
            .select("id,code,title,channel_id,message_id,views,year,genre,rating")
            .order("id", desc=True)
            .limit(ADMIN_LIST_LIMIT)
            .execute()
        )
        rows = result.data or []
    except Exception:
        rows = []

    if not rows:
        text = "📋 Bazada kino yo'q."
    else:
        parts = [f"📋 <b>Kinolar</b> ({len(rows)})"]
        for row in rows:
            views = int(row.get("views") or 0)
            parts.append(
                "━━━━━━━━━━━━━━\n"
                f"🆔 ID: <code>{row.get('id')}</code>\n"
                f"🔢 Kod: <code>{safe(row.get('code'))}</code>\n"
                f"🎬 <b>{safe(row.get('title'))}</b>\n"
                f"📢 Kanal: <code>{row.get('channel_id')}</code>\n"
                f"💬 Message: <code>{row.get('message_id')}</code>\n"
                f"📅 Yil: {row.get('year') or '—'}\n"
                f"🎭 Janr: {safe(row.get('genre') or '—')}\n"
                f"⭐ Reyting: {row.get('rating') if row.get('rating') is not None else '—'}\n"
                f"👁 Ko'rish: {views:,}"
            )
        text = "\n".join(parts)

    await send_long(event, text)


@router.callback_query(F.data == "admin_stats")
@router.message(Command("stats"))
async def admin_stats(event: Union[Message, CallbackQuery]) -> None:
    if not is_admin(event.from_user.id):
        return
    try:
        users_result = (
            get_sb().table("users").select("id", count="exact").execute()
        )
        movies_result = (
            get_sb().table("movies").select("id", count="exact").execute()
        )
        views_result = get_sb().table("movies").select("views").execute()
        total_views = sum(
            int(x.get("views") or 0) for x in (views_result.data or [])
        )

        today = datetime.now(timezone.utc).date().isoformat()
        today_result = (
            get_sb()
            .table("users")
            .select("id", count="exact")
            .gte("last_activity", f"{today}T00:00:00+00:00")
            .execute()
        )

        text = (
            "📊 <b>Statistika</b>\n\n"
            f"👥 Foydalanuvchilar: <b>{users_result.count or 0}</b>\n"
            f"🎬 Kinolar: <b>{movies_result.count or 0}</b>\n"
            f"👁 Ko'rishlar: <b>{total_views:,}</b>\n"
            f"🟢 Bugun faol: <b>{today_result.count or 0}</b>"
        )
    except Exception as e:
        logger.error("stats: %s", e)
        text = "❌ Statistikani olishda xatolik."

    await send_long(event, text)


@router.callback_query(F.data == "admin_users")
@router.message(Command("users"))
async def admin_users(event: Union[Message, CallbackQuery]) -> None:
    if not is_admin(event.from_user.id):
        return
    try:
        total = get_sb().table("users").select("id", count="exact").execute()
        blocked = (
            get_sb()
            .table("users")
            .select("id", count="exact")
            .eq("is_blocked", True)
            .execute()
        )
        text = (
            "👥 <b>Foydalanuvchilar</b>\n\n"
            f"👤 Jami: <b>{total.count or 0}</b>\n"
            f"🚫 Bloklagan: <b>{blocked.count or 0}</b>"
        )
    except Exception:
        text = "❌ Xatolik."

    await send_long(event, text)


# ---------- MAJBURIY OBUNA ADMIN ----------

@router.callback_query(F.data == "admin_force_sub")
async def force_sub_info(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    channels = await get_force_channels()
    if channels:
        text = f"📢 <b>Majburiy obuna</b>\n\nJami kanal: <b>{len(channels)}</b>\n\n"
        for index, channel in enumerate(channels, 1):
            title = channel.get("title") or channel.get("username") or "Noma'lum"
            text += (
                f"{index}. <b>{safe(title)}</b>\n"
                f"🆔 <code>{channel.get('chat_id')}</code>\n\n"
            )
    else:
        text = "📢 <b>Majburiy obuna</b>\n\nHozircha kanal qo'shilmagan."

    await callback.message.answer(text, reply_markup=force_admin_kb())
    await callback.answer()


@router.callback_query(F.data == "force_list")
async def force_list(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    channels = await get_force_channels()
    if not channels:
        await callback.message.answer(
            "📢 Majburiy obuna kanallari yo'q.", reply_markup=force_admin_kb()
        )
        await callback.answer()
        return

    text = "📋 <b>Majburiy obuna kanallari</b>\n\n"
    for index, channel in enumerate(channels, 1):
        title = channel.get("title") or channel.get("username") or "Noma'lum"
        text += (
            f"{index}. <b>{safe(title)}</b>\n"
            f"🆔 ID: <code>{channel.get('chat_id')}</code>\n"
            f"🔗 {safe(channel.get('username') or 'private')}\n\n"
        )

    await callback.message.answer(text, reply_markup=force_admin_kb())
    await callback.answer()


@router.callback_query(F.data == "force_add")
async def force_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await state.set_state(ForceChannelAdd.waiting)
    await callback.message.answer(
        "➕ <b>Majburiy kanal qo'shish</b>\n\n"
        "Kanal username yoki ID yuboring.\n\n"
        "Masalan:\n"
        "<code>@mychannel</code>\n"
        "<code>-1001234567890</code>\n\n"
        "Private kanal bo'lsa, bot kanalga admin bo'lishi shart.",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(StateFilter(ForceChannelAdd.waiting), F.text)
async def force_add_process(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    raw = message.text.strip()
    parts = [x.strip() for x in raw.split("|", 1)]
    reference = parts[0]
    custom_invite = parts[1] if len(parts) > 1 else ""

    if not reference:
        await message.answer("❌ Kanal username yoki ID kiriting.")
        return

    try:
        chat = await bot.get_chat(reference)
        chat_id = int(chat.id)

        invite_link = custom_invite or getattr(chat, "invite_link", None) or ""
        username = getattr(chat, "username", None) or ""
        title = getattr(chat, "title", None) or username or str(chat_id)

        existing = (
            get_sb()
            .table("force_channels")
            .select("id")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        )

        payload = {
            "chat_id": chat_id,
            "username": username,
            "title": title,
            "invite_link": invite_link,
            "is_active": True,
        }

        if existing.data:
            (
                get_sb()
                .table("force_channels")
                .update(payload)
                .eq("chat_id", chat_id)
                .execute()
            )
            text = (
                "♻️ <b>Kanal yangilandi!</b>\n\n"
                f"📢 {safe(title)}\n🆔 <code>{chat_id}</code>"
            )
        else:
            get_sb().table("force_channels").insert(payload).execute()
            text = (
                "✅ <b>Kanal qo'shildi!</b>\n\n"
                f"📢 {safe(title)}\n🆔 <code>{chat_id}</code>"
            )

        channel_cache.invalidate("channels")
        await state.clear()
        await message.answer(text, reply_markup=force_admin_kb())

    except TelegramBadRequest:
        await message.answer(
            "❌ Kanal topilmadi.\n\n"
            "Username/ID ni tekshiring va bot "
            "kanalga administrator ekanini tekshiring."
        )
    except Exception as e:
        logger.exception("force add: %s", e)
        await message.answer(
            "❌ Kanal qo'shishda xatolik.\n\n"
            f"<code>{safe(str(e)[:1000])}</code>"
        )


@router.callback_query(F.data == "force_delete")
async def force_delete_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await state.set_state(ForceChannelDelete.waiting)
    await callback.message.answer(
        "🗑 <b>Kanal o'chirish</b>\n\n"
        "Kanal ID sini yuboring.\n\n"
        "Masalan:\n<code>-1001234567890</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(StateFilter(ForceChannelDelete.waiting), F.text)
async def force_delete_process(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if await _handle_add_cancel(message, state):
        return

    try:
        chat_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Kanal ID noto'g'ri.")
        return

    try:
        result = (
            get_sb()
            .table("force_channels")
            .delete()
            .eq("chat_id", chat_id)
            .execute()
        )
        channel_cache.invalidate("channels")
        await state.clear()
        text = (
            "✅ Majburiy obunadan kanal o'chirildi."
            if result.data
            else "❌ Bunday kanal topilmadi."
        )
        await message.answer(text, reply_markup=force_admin_kb())
    except Exception as e:
        logger.error("force delete: %s", e)
        await message.answer("❌ O'chirishda xatolik.")


# ---------- REKLAMA / BROADCAST ----------

@router.callback_query(F.data == "admin_broadcast")
@router.message(Command("broadcast"))
async def broadcast_start(
    event: Union[Message, CallbackQuery], state: FSMContext
) -> None:
    if not is_admin(event.from_user.id):
        return
    await state.clear()
    await state.set_state(Broadcast.waiting)
    text = (
        "📢 <b>Reklama / Broadcast</b>\n\n"
        "Endi bitta xabar yuboring:\n"
        "• matn\n• rasm\n• video\n• audio\n• document\n• animation\n• voice\n\n"
        "Bot aynan shu xabarni foydalanuvchilarga nusxalaydi.\n\n"
        "Bekor qilish: /cancel"
    )
    await send_long(event, text)


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb = admin_menu_kb() if is_admin(message.from_user.id) else main_menu_kb()
    await message.answer("❌ Bekor qilindi.", reply_markup=kb)


async def send_broadcast_message(bot: Bot, user_id: int, source: Message) -> None:
    for attempt in range(3):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source.chat.id,
                message_id=source.message_id,
            )
            return
        except TelegramRetryAfter as e:
            if attempt >= 2:
                raise
            await asyncio.sleep(float(e.retry_after) + 1)


@router.message(StateFilter(Broadcast.waiting))
async def broadcast_run(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()

    try:
        result = (
            get_sb()
            .table("users")
            .select("telegram_id")
            .eq("is_blocked", False)
            .execute()
        )
        users = [
            int(x["telegram_id"])
            for x in (result.data or [])
            if x.get("telegram_id")
        ]
    except Exception as e:
        logger.error("broadcast users: %s", e)
        await message.answer("❌ Foydalanuvchilar olinmadi.")
        return

    if not users:
        await message.answer("❌ Foydalanuvchilar yo'q.")
        return

    sent = 0
    failed = 0
    blocked = 0

    status = await message.answer(f"📢 Yuborilmoqda...\n\n0/{len(users)}")

    for index, user_id in enumerate(users, 1):
        try:
            await send_broadcast_message(bot, user_id, message)
            sent += 1
            await asyncio.sleep(BROADCAST_DELAY)
        except TelegramForbiddenError:
            blocked += 1
            await mark_blocked(user_id)
        except TelegramBadRequest as e:
            text = str(e).lower()
            if (
                "blocked" in text
                or "deactivated" in text
                or "chat not found" in text
            ):
                blocked += 1
                await mark_blocked(user_id)
            else:
                failed += 1
            logger.warning("broadcast bad request %s: %s", user_id, e)
        except Exception as e:
            failed += 1
            logger.warning("broadcast %s: %s", user_id, e)

        if index % 25 == 0 or index == len(users):
            try:
                await status.edit_text(
                    "📢 <b>Reklama yuborilmoqda...</b>\n\n"
                    f"📊 {index}/{len(users)}\n"
                    f"✅ {sent}\n"
                    f"❌ {failed}\n"
                    f"🚫 {blocked}"
                )
            except TelegramBadRequest:
                pass

    await message.answer(
        "📢 <b>Reklama tugadi</b>\n\n"
        f"👥 Jami: {len(users)}\n"
        f"✅ Yuborildi: {sent}\n"
        f"❌ Xato: {failed}\n"
        f"🚫 Bloklagan: {blocked}",
        reply_markup=admin_menu_kb(),
    )


# ---------- SOZLAMALAR ----------

@router.callback_query(F.data == "admin_settings")
async def settings(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    channels = await get_force_channels()
    await callback.message.answer(
        "⚙️ <b>Sozlamalar</b>\n\n"
        f"🤖 Bot token: {'✅' if BOT_TOKEN else '❌'}\n"
        f"👨‍💻 Adminlar: <code>{safe(ADMIN_IDS)}</code>\n"
        f"📢 Majburiy kanallar: <b>{len(channels)}</b>\n"
        f"💬 Support: {safe(SUPPORT_USERNAME)}\n"
        f"🗄 Supabase: {'✅' if SUPABASE_URL and SUPABASE_KEY else '❌'}",
        reply_markup=admin_menu_kb(),
    )
    await callback.answer()


# ---------- FALLBACK ----------

@router.message(F.text)
async def fallback(message: Message, state: FSMContext, bot: Bot) -> None:
    current_state = await state.get_state()
    if current_state is not None:
        return

    await register_user(message)
    if not message.from_user:
        return

    if not await check_sub(bot, message.from_user.id):
        await send_force_subscription(message)
        return

    query = sanitize(message.text or "")
    if len(query) < 2:
        return

    rows = await search_movies(query)
    if not rows:
        await message.answer(
            f"❌ <b>{user_name(message)}</b>, hech narsa topilmadi.",
            reply_markup=main_menu_kb(),
        )
        return

    if len(rows) == 1:
        await show_movie_result(message, message.from_user.id, rows[0])
        return

    text = f"🔍 <b>{len(rows)} ta natija:</b>\n\n"
    for row in rows:
        text += f"• {safe(row.get('title'))}\n"

    await message.answer(text, reply_markup=movie_list_kb(rows))


# ============================================================================
# GLOBAL ERROR HANDLER
# ============================================================================

@router.error()
async def error_handler(event: ErrorEvent) -> None:
    logger.exception("Handler xatosi: %s", event.exception)

    update = event.update
    try:
        if update.message is not None:
            await update.message.answer(
                "❌ Xatolik yuz berdi. Qayta urinib ko'ring.",
                reply_markup=main_menu_kb(),
            )
        elif update.callback_query is not None:
            await update.callback_query.answer(
                "❌ Xatolik yuz berdi.", show_alert=True
            )
    except Exception:
        pass


# ============================================================================
# ASOSIY FUNKSIYA
# ============================================================================

async def main() -> None:
    global supabase

    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_IDS")
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_KEY:
        missing.append("SUPABASE_KEY")
    if missing:
        print("ERROR .env:", ", ".join(missing))
        return

    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase ulandi.")
    except Exception:
        logger.exception("Supabase xatosi")
        return

    try:
        await ensure_genres()
    except Exception as e:
        logger.warning("ensure_genres: %s", e)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Anti-flood middleware (handlerlardan oldin)
    router.message.middleware(ThrottlingMiddleware(rate_limit=0.8))

    logger.info("Kino Bot ishga tushdi.")

    try:
        await dp.start_polling(
            bot,
            skip_updates=True,   # restartdan keyin eski update'lar kelmaydi
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
