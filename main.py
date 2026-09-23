"""
Kino Bot â€” Production (aiogram 3 + Supabase)
â€¢ FSM to'liq tuzatilgan (StateFilter)
â€¢ Shaxsiy salomlashuv
â€¢ Tez kino qo'shish (Reply + /add â†’ 3â€“4 bosqich)
â€¢ Chiroyli kartochkalar va muloqot
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


def get_sb() -> Client:
    if supabase is None:
        raise RuntimeError("Supabase hali ishga tushirilmagan.")
    return supabase


# =========================================================
# FSM
# =========================================================

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


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="ðŸŽ¬ Kino qidirish"),
                KeyboardButton(text="ðŸ”¢ Kod orqali"),
            ],
            [
                KeyboardButton(text="ðŸŽ­ Janrlar"),
                KeyboardButton(text="ðŸ”¥ TOP"),
            ],
            [
                KeyboardButton(text="ðŸ†• Yangilar"),
                KeyboardButton(text="â¤ï¸ Sevimlilar"),
            ],
            [KeyboardButton(text="â„¹ï¸ Yordam")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Kino nomi yoki kod yozing...",
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="âŒ Bekor qilish")]],
        resize_keyboard=True,
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="âš¡ Tez qo'shish", callback_data="admin_fast_help"),
                InlineKeyboardButton(text="ðŸ—‘ O'chirish", callback_data="admin_delete"),
            ],
            [
                InlineKeyboardButton(text="ðŸ“‹ Ro'yxat", callback_data="admin_list"),
                InlineKeyboardButton(text="ðŸ“Š Statistika", callback_data="admin_stats"),
            ],
            [
                InlineKeyboardButton(text="ðŸ‘¥ Foydalanuvchilar", callback_data="admin_users"),
                InlineKeyboardButton(text="ðŸ“¢ Reklama", callback_data="admin_broadcast"),
            ],
            [
                InlineKeyboardButton(text="ðŸ“¢ Majburiy obuna", callback_data="admin_force_sub"),
                InlineKeyboardButton(text="âš™ï¸ Sozlamalar", callback_data="admin_settings"),
            ],
        ]
    )


def movie_actions_kb(movie_id: int, is_fav: bool = False) -> InlineKeyboardMarkup:
    fav_text = "ðŸ’” Olib tashlash" if is_fav else "â¤ï¸ Sevimli"
    fav_data = f"unfav_{movie_id}" if is_fav else f"fav_{movie_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="â–¶ï¸ Tomosha qilish", callback_data=f"watch_{movie_id}")],
            [
                InlineKeyboardButton(text=fav_text, callback_data=fav_data),
                InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main"),
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
            [InlineKeyboardButton(text="ðŸ“¢ Kanalga obuna bo'lish", url=link)],
            [InlineKeyboardButton(text="âœ… Tekshirdim", callback_data="check_sub")],
        ]
    )


def confirm_delete_kb(movie_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="âœ… Ha, o'chir", callback_data=f"del_yes_{movie_id}"),
                InlineKeyboardButton(text="âŒ Yo'q", callback_data="del_no"),
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
    buttons.append([InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def skip_extra_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="â­ O'tkazib yuborish", callback_data="skip_extra")],
        ]
    )


# =========================================================
# HELPERS
# =========================================================

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe(text: Any) -> str:
    if text is None:
        return ""
    return escape(str(text))


def sanitize(q: str) -> str:
    return re.sub(r"[%_\\]", "", (q or "").strip())[:100]


def user_name(obj: Message | CallbackQuery) -> str:
    u = obj.from_user
    if not u:
        return "Do'st"
    return safe(u.first_name or u.username or "Do'st")


def greeting(name: str) -> str:
    h = datetime.now().hour
    if 5 <= h < 12:
        part = "Xayrli tong"
    elif 12 <= h < 17:
        part = "Xayrli kun"
    elif 17 <= h < 22:
        part = "Xayrli kech"
    else:
        part = "Xayrli tun"
    return f"{part}, <b>{name}</b>! ðŸ‘‹"


async def register_user(message: Message) -> None:
    u = message.from_user
    if not u:
        return
    sb = get_sb()
    try:
        ex = sb.table("users").select("id").eq("telegram_id", u.id).limit(1).execute()
        data = {
            "username": u.username,
            "first_name": u.first_name,
            "last_activity": now_iso(),
            "is_blocked": False,
        }
        if ex.data:
            sb.table("users").update(data).eq("telegram_id", u.id).execute()
        else:
            sb.table("users").insert({"telegram_id": u.id, **data}).execute()
    except Exception as e:
        logger.error("register_user: %s", e)


async def check_sub(bot: Bot, uid: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.RESTRICTED,
        )
    except Exception as e:
        logger.warning("sub check %s: %s", uid, e)
        return True


async def movie_by_code(code: str) -> Optional[Dict]:
    try:
        r = get_sb().table("movies").select("*").eq("code", code).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        logger.error("movie_by_code: %s", e)
        return None


async def movie_by_id(mid: int) -> Optional[Dict]:
    try:
        r = get_sb().table("movies").select("*").eq("id", mid).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        logger.error("movie_by_id: %s", e)
        return None


async def inc_views(mid: int) -> None:
    try:
        get_sb().rpc("increment_movie_views", {"movie_id": mid}).execute()
    except Exception as e:
        logger.error("inc_views: %s", e)


async def is_fav(tid: int, mid: int) -> bool:
    try:
        r = (
            get_sb()
            .table("favorites")
            .select("id")
            .eq("telegram_id", tid)
            .eq("movie_id", mid)
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception as e:
        logger.error("is_fav: %s", e)
        return False


def movie_card(m: Dict) -> str:
    title = safe(m.get("title") or "Noma'lum")
    alt = m.get("alternative_title")
    year = m.get("year") or "â€”"
    genre = safe(m.get("genre") or "â€”")
    rating = m.get("rating")
    views = m.get("views") or 0
    desc = m.get("description") or ""
    code = safe(m.get("code") or "â€”")
    rating_txt = f"{rating}" if rating is not None else "â€”"

    lines = [f"ðŸŽ¬ <b>{title}</b>"]
    if alt:
        lines.append(f"ðŸ“Œ <i>{safe(alt)}</i>")
    lines.append("")
    lines.append(f"ðŸ”¢ Kod: <code>{code}</code>")
    lines.append(f"ðŸ“… {year}  Â·  ðŸŽ­ {genre}")
    lines.append(f"â­ {rating_txt}  Â·  ðŸ‘€ {views:,}")
    if desc:
        lines.append("")
        lines.append(f"ðŸ“ {safe(desc)}")
    return "\n".join(lines)


async def ensure_genres() -> None:
    defaults = [
        "Action", "Comedy", "Horror", "Romance", "Sci-Fi",
        "Fantasy", "Drama", "Thriller", "Family", "Animation",
        "Crime", "Adventure", "Mystery", "War", "History",
    ]
    sb = get_sb()
    for g in defaults:
        try:
            ex = sb.table("genres").select("id").eq("name", g).limit(1).execute()
            if not ex.data:
                sb.table("genres").insert({"name": g}).execute()
        except Exception as e:
            logger.warning("genre %s: %s", g, e)


async def save_movie(data: Dict) -> tuple[bool, str]:
    try:
        r = get_sb().table("movies").insert(data).execute()
        if not r.data:
            return False, "Insert javobi bo'sh"
        return True, ""
    except Exception as e:
        logger.exception("save_movie")
        return False, str(e)


async def finish_add(message: Message, state: FSMContext, data: Dict) -> None:
    required = ("code", "title", "genre", "channel_id", "message_id")
    missing = [k for k in required if data.get(k) in (None, "")]
    if missing:
        await message.answer(
            f"âŒ Yetishmayapti: <code>{', '.join(missing)}</code>\n"
            "Qaytadan Reply + /add bilan boshlang."
        )
        await state.clear()
        return

    payload = {
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

    if await movie_by_code(payload["code"]):
        await message.answer(
            f"âŒ <code>{safe(payload['code'])}</code> kodi band.\n"
            "Boshqa kod bilan qayta urinib ko'ring."
        )
        return

    ok, err = await save_movie(payload)
    if not ok:
        await message.answer(
            "âŒ Bazaga yozilmadi.\n\n"
            f"<code>{safe(err[:1800])}</code>"
        )
        return

    await state.clear()
    await message.answer(
        "âœ… <b>Kino qo'shildi!</b>\n\n"
        f"ðŸ”¢ <code>{safe(payload['code'])}</code>\n"
        f"ðŸŽ¬ <b>{safe(payload['title'])}</b>\n"
        f"ðŸŽ­ {safe(payload['genre'])}\n"
        f"ðŸ“¢ <code>{payload['channel_id']}</code>\n"
        f"ðŸ’¬ Message: <code>{payload['message_id']}</code>",
        reply_markup=main_menu_kb(),
    )


# =========================================================
# ROUTER
# =========================================================

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    await register_user(message)
    name = user_name(message)

    if not await check_sub(bot, message.from_user.id):
        await message.answer(
            f"{greeting(name)}\n\n"
            "Botdan foydalanish uchun kanalimizga obuna bo'ling.\n"
            "Obuna bo'lgach <b>Â«âœ… TekshirdimÂ»</b> tugmasini bosing.",
            reply_markup=force_sub_kb(),
        )
        return

    await message.answer(
        f"{greeting(name)}\n\n"
        "ðŸŽ¬ <b>Kino Bot</b>ga xush kelibsiz!\n\n"
        "Kinolarni kod yoki nom orqali tez toping.\n"
        "Menyudan tanlang yoki to'g'ridan-to'g'ri yozing.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
@router.message(F.text == "â„¹ï¸ Yordam")
async def cmd_help(message: Message) -> None:
    await register_user(message)
    name = user_name(message)
    await message.answer(
        f"{greeting(name)}\n\n"
        "â„¹ï¸ <b>Qanday foydalaniladi?</b>\n\n"
        "ðŸ”¢ <b>Kod orqali</b> â€” kino kodini yuboring\n"
        "ðŸŽ¬ <b>Qidirish</b> â€” nomini yozing\n"
        "ðŸŽ­ <b>Janrlar</b> â€” janr bo'yicha\n"
        "ðŸ”¥ <b>TOP</b> â€” eng ko'p ko'rilganlar\n"
        "ðŸ†• <b>Yangilar</b> â€” so'nggi qo'shilganlar\n"
        "â¤ï¸ <b>Sevimlilar</b> â€” o'zingiz tanlaganlar\n\n"
        f"ðŸ’¬ Savol-javob: {SUPPORT_USERNAME}",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "check_sub")
async def on_check_sub(callback: CallbackQuery, bot: Bot) -> None:
    name = user_name(callback)
    if await check_sub(bot, callback.from_user.id):
        try:
            await callback.message.edit_text("âœ… Obuna tasdiqlandi!")
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            f"{greeting(name)}\n\n"
            "ðŸŽ¬ Endi bemalol foydalanishingiz mumkin!",
            reply_markup=main_menu_kb(),
        )
        await callback.answer()
    else:
        await callback.answer("âŒ Hali obuna bo'lmagansiz.", show_alert=True)


@router.callback_query(F.data == "back_main")
async def on_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    name = user_name(callback)
    await callback.message.answer(
        f"ðŸ  Asosiy menyu\n\n{greeting(name)}",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@router.message(F.text == "ðŸ”¢ Kod orqali")
async def search_code_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await register_user(message)
    if not await check_sub(bot, message.from_user.id):
        await message.answer("ðŸ“¢ Avval kanalga obuna bo'ling.", reply_markup=force_sub_kb())
        return
    await state.clear()
    await state.set_state(SearchCode.waiting)
    await message.answer(
        f"ðŸ”¢ <b>{user_name(message)}</b>, kino kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(StateFilter(SearchCode.waiting), F.text)
async def search_code_process(message: Message, state: FSMContext) -> None:
    if message.text == "âŒ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    code = (message.text or "").strip()
    movie = await movie_by_code(code)
    if not movie:
        await message.answer(
            "âŒ Bunday kodli kino topilmadi.\n"
            "Boshqa kod yuboring yoki Â«âŒ Bekor qilishÂ»."
        )
        return
    await state.clear()
    fav = await is_fav(message.from_user.id, movie["id"])
    await message.answer(movie_card(movie), reply_markup=movie_actions_kb(movie["id"], fav))


@router.message(F.text == "ðŸŽ¬ Kino qidirish")
async def search_title_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await register_user(message)
    if not await check_sub(bot, message.from_user.id):
        await message.answer("ðŸ“¢ Avval kanalga obuna bo'ling.", reply_markup=force_sub_kb())
        return
    await state.clear()
    await state.set_state(SearchTitle.waiting)
    await message.answer(
        f"ðŸŽ¬ <b>{user_name(message)}</b>, kino nomini yozing:",
        reply_markup=cancel_kb(),
    )


@router.message(StateFilter(SearchTitle.waiting), F.text)
async def search_title_process(message: Message, state: FSMContext) -> None:
    if message.text == "âŒ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    query = sanitize(message.text or "")
    if len(query) < 2:
        await message.answer("âŒ Kamida 2 ta belgi yozing.")
        return
    try:
        r = (
            get_sb()
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
        await message.answer("âŒ Qidirishda xatolik.")
        return
    if not rows:
        await message.answer("âŒ Hech narsa topilmadi. Boshqa nom yozing.")
        return
    await state.clear()
    if len(rows) == 1:
        m = rows[0]
        fav = await is_fav(message.from_user.id, m["id"])
        await message.answer(movie_card(m), reply_markup=movie_actions_kb(m["id"], fav))
        return
    text = f"ðŸ” <b>{len(rows)} ta natija:</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"â€¢ {safe(row['title'])} ({row.get('year') or 'â€”'})\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:32], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("movie_"))
async def show_movie(callback: CallbackQuery) -> None:
    try:
        mid = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ID", show_alert=True)
        return
    movie = await movie_by_id(mid)
    if not movie:
        await callback.answer("Kino topilmadi", show_alert=True)
        return
    fav = await is_fav(callback.from_user.id, mid)
    await callback.message.answer(movie_card(movie), reply_markup=movie_actions_kb(mid, fav))
    await callback.answer()


@router.callback_query(F.data.startswith("watch_"))
async def watch_movie(callback: CallbackQuery, bot: Bot) -> None:
    try:
        mid = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ID", show_alert=True)
        return
    movie = await movie_by_id(mid)
    if not movie:
        await callback.answer("Kino topilmadi", show_alert=True)
        return
    try:
        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=movie["channel_id"],
            message_id=movie["message_id"],
        )
        await inc_views(mid)
        await callback.answer("ðŸŽ¬ Yuborildi!")
    except TelegramForbiddenError:
        await callback.answer("âŒ Botni bloklagansiz", show_alert=True)
    except TelegramBadRequest as e:
        logger.error("copy: %s", e)
        await callback.answer("âŒ Xabar topilmadi yoki o'chirilgan", show_alert=True)
    except Exception as e:
        logger.error("watch: %s", e)
        await callback.answer("âŒ Xatolik", show_alert=True)


@router.callback_query(F.data.startswith("fav_"))
async def add_fav(callback: CallbackQuery) -> None:
    try:
        mid = int(callback.data.split("_", 1)[1])
        get_sb().table("favorites").upsert(
            {"telegram_id": callback.from_user.id, "movie_id": mid},
            on_conflict="telegram_id,movie_id",
        ).execute()
        await callback.answer("â¤ï¸ Sevimlilarga qo'shildi")
        await callback.message.edit_reply_markup(reply_markup=movie_actions_kb(mid, True))
    except Exception as e:
        logger.error("fav: %s", e)
        await callback.answer("âŒ Xatolik", show_alert=True)


@router.callback_query(F.data.startswith("unfav_"))
async def remove_fav(callback: CallbackQuery) -> None:
    try:
        mid = int(callback.data.split("_", 1)[1])
        get_sb().table("favorites").delete().eq(
            "telegram_id", callback.from_user.id
        ).eq("movie_id", mid).execute()
        await callback.answer("ðŸ’” Olib tashlandi")
        await callback.message.edit_reply_markup(reply_markup=movie_actions_kb(mid, False))
    except Exception as e:
        logger.error("unfav: %s", e)
        await callback.answer("âŒ Xatolik", show_alert=True)


@router.message(F.text == "â¤ï¸ Sevimlilar")
async def show_favs(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_sub(bot, message.from_user.id):
        await message.answer("ðŸ“¢ Avval kanalga obuna bo'ling.", reply_markup=force_sub_kb())
        return
    try:
        r = (
            get_sb()
            .table("favorites")
            .select("movie_id, movies(*)")
            .eq("telegram_id", message.from_user.id)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("favs: %s", e)
        await message.answer("âŒ Xatolik")
        return
    movies = []
    for row in rows:
        m = row.get("movies")
        if isinstance(m, list):
            m = m[0] if m else None
        if m:
            movies.append(m)
    if not movies:
        await message.answer(
            f"â¤ï¸ <b>{user_name(message)}</b>, sevimlilaringiz bo'sh.\n"
            "Kinoni ochib Â«â¤ï¸ SevimliÂ» tugmasini bosing.",
            reply_markup=main_menu_kb(),
        )
        return
    text = f"â¤ï¸ <b>{user_name(message)}</b>, sevimlilaringiz:\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for m in movies:
        text += f"â€¢ {safe(m['title'])}\n"
        buttons.append(
            [InlineKeyboardButton(text=m["title"][:36], callback_data=f"movie_{m['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(F.text == "ðŸŽ­ Janrlar")
async def show_genres(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_sub(bot, message.from_user.id):
        await message.answer("ðŸ“¢ Avval kanalga obuna bo'ling.", reply_markup=force_sub_kb())
        return
    try:
        r = get_sb().table("genres").select("name").order("name").execute()
        genres = [x["name"] for x in (r.data or []) if x.get("name")]
    except Exception as e:
        logger.error("genres: %s", e)
        await message.answer("âŒ Xatolik")
        return
    if not genres:
        await message.answer("Janrlar yo'q.")
        return
    await message.answer(
        f"ðŸŽ­ <b>{user_name(message)}</b>, janrni tanlang:",
        reply_markup=genres_kb(genres),
    )


@router.callback_query(F.data.startswith("genre_"))
async def genre_list(callback: CallbackQuery) -> None:
    genre = callback.data[6:]
    try:
        r = (
            get_sb()
            .table("movies")
            .select("*")
            .ilike("genre", f"%{genre}%")
            .order("views", desc=True)
            .limit(30)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("genre: %s", e)
        await callback.answer("âŒ Xatolik", show_alert=True)
        return
    if not rows:
        await callback.answer("Bu janrda kino yo'q", show_alert=True)
        return
    text = f"ðŸŽ­ <b>{safe(genre)}</b> â€” {len(rows)} ta\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"â€¢ {safe(row['title'])} ({row.get('year') or 'â€”'})\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:32], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main")])
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.message(F.text == "ðŸ”¥ TOP")
async def popular(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_sub(bot, message.from_user.id):
        await message.answer("ðŸ“¢ Avval kanalga obuna bo'ling.", reply_markup=force_sub_kb())
        return
    try:
        r = (
            get_sb()
            .table("movies")
            .select("*")
            .order("views", desc=True)
            .limit(10)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("popular: %s", e)
        await message.answer("âŒ Xatolik")
        return
    if not rows:
        await message.answer("Hali kino yo'q.")
        return
    text = "ðŸ”¥ <b>TOP 10</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    medals = ["ðŸ¥‡", "ðŸ¥ˆ", "ðŸ¥‰"] + [f"{i}." for i in range(4, 11)]
    for i, row in enumerate(rows):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        text += f"{medal} {safe(row['title'])} â€” {row.get('views', 0):,}\n"
        buttons.append(
            [InlineKeyboardButton(
                text=f"{medal} {row['title'][:28]}",
                callback_data=f"movie_{row['id']}",
            )]
        )
    buttons.append([InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(F.text == "ðŸ†• Yangilar")
async def newest(message: Message, bot: Bot) -> None:
    await register_user(message)
    if not await check_sub(bot, message.from_user.id):
        await message.answer("ðŸ“¢ Avval kanalga obuna bo'ling.", reply_markup=force_sub_kb())
        return
    try:
        r = (
            get_sb()
            .table("movies")
            .select("*")
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("newest: %s", e)
        await message.answer("âŒ Xatolik")
        return
    if not rows:
        await message.answer("Hali kino yo'q.")
        return
    text = "ðŸ†• <b>Yangi kinolar</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"â€¢ {safe(row['title'])} ({row.get('year') or 'â€”'})\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:32], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        f"ðŸ‘¨â€ðŸ’» <b>Admin panel</b>\n\n"
        f"Salom, {user_name(message)}!\n\n"
        "âš¡ Tez qo'shish: kanaldagi xabarga <b>Reply</b> â†’ <code>/add</code>",
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(F.data == "admin_fast_help")
async def fast_help(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        "âš¡ <b>Tez kino qo'shish</b>\n\n"
        "1ï¸âƒ£ Kanaldagi kino <b>xabariga Reply</b> qiling\n"
        "2ï¸âƒ£ Reply ustiga yozing: <code>/add</code>\n"
        "3ï¸âƒ£ Kod â†’ Nom â†’ Janr (3 ta savol)\n"
        "4ï¸âƒ£ Ixtiyoriy: yil, reyting, tavsif yoki Â«o'tkazib yuborishÂ»\n\n"
        "Message ID va kanal ID <b>avtomatik</b> olinadi."
    )
    await callback.answer()


@router.message(Command("add"))
async def fast_add_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    replied = message.reply_to_message
    if replied is None:
        await message.answer(
            "âš¡ <b>Tez qo'shish</b>\n\n"
            "1. Kanaldagi kino xabariga <b>Reply</b> qiling\n"
            "2. Shu reply ustiga <code>/add</code> yuboring\n\n"
            "Kanal ID va Message ID avtomatik saqlanadi."
        )
        return
    channel_id = replied.chat.id
    message_id = replied.message_id
    await state.update_data(channel_id=channel_id, message_id=message_id)
    await state.set_state(AddMovie.code)
    await message.answer(
        "âœ… Xabar ushlandi!\n\n"
        f"ðŸ“¢ Kanal: <code>{channel_id}</code>\n"
        f"ðŸ’¬ Message ID: <code>{message_id}</code>\n\n"
        "1ï¸âƒ£ <b>Kino kodini</b> yuboring:",
        reply_markup=cancel_kb(),
    )


@router.message(StateFilter(AddMovie.code), F.text)
async def add_code(message: Message, state: FSMContext) -> None:
    if message.text == "âŒ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    code = (message.text or "").strip()
    if not code:
        await message.answer("âŒ Kod bo'sh bo'lmasin.")
        return
    if len(code) > 80:
        await message.answer("âŒ Kod juda uzun.")
        return
    if await movie_by_code(code):
        await message.answer("âŒ Bu kod band. Boshqa kod yuboring.")
        return
    await state.update_data(code=code)
    await state.set_state(AddMovie.title)
    await message.answer("2ï¸âƒ£ <b>Kino nomini</b> yuboring:")


@router.message(StateFilter(AddMovie.title), F.text)
async def add_title(message: Message, state: FSMContext) -> None:
    if message.text == "âŒ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("âŒ Nom bo'sh bo'lmasin.")
        return
    await state.update_data(title=title)
    await state.set_state(AddMovie.genre)
    await message.answer("3ï¸âƒ£ <b>Janr</b> (masalan: Action, Drama, Comedy):")


@router.message(StateFilter(AddMovie.genre), F.text)
async def add_genre(message: Message, state: FSMContext) -> None:
    if message.text == "âŒ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    genre = (message.text or "").strip()
    if not genre:
        await message.answer("âŒ Janr bo'sh bo'lmasin.")
        return
    await state.update_data(genre=genre)
    await state.set_state(AddMovie.extra)
    await message.answer(
        "4ï¸âƒ£ <b>Ixtiyoriy ma'lumot</b> (yoki o'tkazib yuboring)\n\n"
        "Format:\n"
        "<code>YIL | REYTING | TAVSIF</code>\n\n"
        "Misollar:\n"
        "<code>2024 | 8.5 | Ajoyib film</code>\n"
        "<code>2023 | - | -</code>\n"
        "<code>- | - | -</code>",
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
    if message.text == "âŒ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    raw = (message.text or "").strip()
    year = rating = description = None
    if raw and raw != "-":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) >= 1 and parts[0] and parts[0] != "-":
            try:
                year = int(parts[0])
                if year < 1800 or year > datetime.now().year + 2:
                    await message.answer("âŒ Yil noto'g'ri. Qayta yuboring.")
                    return
            except ValueError:
                await message.answer("âŒ Format: YIL | REYTING | TAVSIF")
                return
        if len(parts) >= 2 and parts[1] and parts[1] != "-":
            try:
                rating = float(parts[1].replace(",", "."))
                if not 0 <= rating <= 10:
                    await message.answer("âŒ Reyting 0â€“10 oralig'ida.")
                    return
            except ValueError:
                await message.answer("âŒ Reyting noto'g'ri.")
                return
        if len(parts) >= 3 and parts[2] and parts[2] != "-":
            description = parts[2]
    await state.update_data(year=year, rating=rating, description=description)
    data = await state.get_data()
    await finish_add(message, state, data)


@router.callback_query(F.data == "admin_list")
@router.message(Command("movies"))
async def admin_list(event: Union[Message, CallbackQuery]) -> None:
    if not is_admin(event.from_user.id):
        return
    try:
        r = (
            get_sb()
            .table("movies")
            .select("id,code,title,channel_id,message_id,views,year,genre,rating")
            .order("id", desc=True)
            .limit(35)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.error("list: %s", e)
        text = "âŒ Xatolik"
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return
    if not rows:
        text = "ðŸ“‹ Bazada kino yo'q."
    else:
        parts = [f"ðŸ“‹ <b>Kinolar</b> ({len(rows)})\n"]
        for row in rows:
            parts.append(
                f"â”â”â”â”â”â”â”â”â”â”â”â”\n"
                f"ðŸ†” <code>{row['id']}</code> Â· ðŸ”¢ <code>{safe(row['code'])}</code>\n"
                f"ðŸŽ¬ <b>{safe(row['title'])}</b>\n"
                f"ðŸ“¢ <code>{row['channel_id']}</code> Â· ðŸ’¬ <code>{row['message_id']}</code>\n"
                f"ðŸ“… {row.get('year') or 'â€”'} Â· ðŸŽ­ {safe(row.get('genre') or 'â€”')} Â· "
                f"â­ {row.get('rating') if row.get('rating') is not None else 'â€”'} Â· "
                f"ðŸ‘€ {row.get('views', 0)}"
            )
        text = "\n".join(parts)
    if len(text) > 4000:
        text = text[:3900] + "\n\nâ€¦"
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
        users_r = get_sb().table("users").select("id", count="exact").execute()
        movies_r = get_sb().table("movies").select("id", count="exact").execute()
        views_r = get_sb().table("movies").select("views").execute()
        total_views = sum((x.get("views") or 0) for x in (views_r.data or []))
        today = datetime.now(timezone.utc).date().isoformat()
        today_r = (
            get_sb()
            .table("users")
            .select("id", count="exact")
            .gte("last_activity", f"{today}T00:00:00+00:00")
            .execute()
        )
        top_r = (
            get_sb()
            .table("movies")
            .select("title,views")
            .order("views", desc=True)
            .limit(1)
            .execute()
        )
        top = top_r.data[0] if top_r.data else None
        top_txt = f"{safe(top['title'])} ({top.get('views', 0)})" if top else "â€”"
        text = (
            "ðŸ“Š <b>Statistika</b>\n\n"
            f"ðŸ‘¥ Foydalanuvchilar: <b>{users_r.count or 0}</b>\n"
            f"ðŸŽ¬ Kinolar: <b>{movies_r.count or 0}</b>\n"
            f"ðŸ‘€ Jami ko'rishlar: <b>{total_views:,}</b>\n"
            f"ðŸŸ¢ Bugun faol: <b>{today_r.count or 0}</b>\n"
            f"ðŸ”¥ Top: {top_txt}"
        )
    except Exception as e:
        logger.error("stats: %s", e)
        text = "âŒ Statistikani olishda xatolik"
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
        total = get_sb().table("users").select("id", count="exact").execute()
        blocked = (
            get_sb()
            .table("users")
            .select("id", count="exact")
            .eq("is_blocked", True)
            .execute()
        )
        text = (
            "ðŸ‘¥ <b>Foydalanuvchilar</b>\n\n"
            f"ðŸ‘¤ Jami: <b>{total.count or 0}</b>\n"
            f"ðŸš« Bloklagan: <b>{blocked.count or 0}</b>"
        )
    except Exception as e:
        logger.error("users: %s", e)
        text = "âŒ Xatolik"
    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


@router.callback_query(F.data == "admin_delete")
async def delete_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await state.set_state(AdminDelete.confirm)
    await callback.message.answer(
        "ðŸ—‘ O'chirmoqchi bo'lgan kino <b>kodini</b> yuboring:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(StateFilter(AdminDelete.confirm), F.text)
async def delete_confirm(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if message.text == "âŒ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    code = (message.text or "").strip()
    movie = await movie_by_code(code)
    if not movie:
        await message.answer("âŒ Kino topilmadi.")
        await state.clear()
        return
    await state.update_data(movie_id=movie["id"])
    await message.answer(
        "âš ï¸ <b>Rostdan o'chirasizmi?</b>\n\n"
        f"ðŸŽ¬ {safe(movie['title'])}\n"
        f"ðŸ”¢ <code>{safe(movie['code'])}</code>",
        reply_markup=confirm_delete_kb(movie["id"]),
    )


@router.callback_query(F.data.startswith("del_yes_"))
async def del_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    try:
        mid = int(callback.data.split("_")[2])
        get_sb().table("favorites").delete().eq("movie_id", mid).execute()
        get_sb().table("movies").delete().eq("id", mid).execute()
        await state.clear()
        await callback.message.edit_text("âœ… Kino o'chirildi.")
        await callback.answer()
    except Exception as e:
        logger.error("delete: %s", e)
        await callback.answer("âŒ Xatolik", show_alert=True)


@router.callback_query(F.data == "del_no")
async def del_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("âŒ Bekor qilindi.")
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast")
@router.message(Command("broadcast"))
async def broadcast_start(event: Union[Message, CallbackQuery], state: FSMContext) -> None:
    if not is_admin(event.from_user.id):
        return
    await state.clear()
    await state.set_state(Broadcast.waiting)
    text = (
        "ðŸ“¢ <b>Reklama</b>\n\n"
        "Matn, rasm, video yoki document yuboring.\n"
        "Bekor: /cancel"
    )
    if isinstance(event, CallbackQuery):
        await event.message.answer(text)
        await event.answer()
    else:
        await event.answer(text)


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())


@router.message(StateFilter(Broadcast.waiting))
async def broadcast_run(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    try:
        r = (
            get_sb()
            .table("users")
            .select("telegram_id")
            .eq("is_blocked", False)
            .execute()
        )
        users = [x["telegram_id"] for x in (r.data or [])]
    except Exception as e:
        logger.error("broadcast users: %s", e)
        await message.answer("âŒ Foydalanuvchilar olinmadi.")
        return
    ok = fail = blocked = 0
    status = await message.answer(f"ðŸ“¢ Yuborilmoqda... 0/{len(users)}")
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
            ok += 1
            await asyncio.sleep(0.06)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, message.text or message.caption or "")
                ok += 1
            except Exception:
                fail += 1
        except TelegramForbiddenError:
            blocked += 1
            try:
                get_sb().table("users").update({"is_blocked": True}).eq(
                    "telegram_id", uid
                ).execute()
            except Exception:
                pass
        except Exception as e:
            fail += 1
            logger.warning("bc %s: %s", uid, e)
        if i % 40 == 0:
            try:
                await status.edit_text(
                    f"ðŸ“¢ {i}/{len(users)}\nâœ… {ok} Â· âŒ {fail} Â· ðŸš« {blocked}"
                )
            except TelegramBadRequest:
                pass
    await message.answer(
        "ðŸ“¢ <b>Tugadi</b>\n\n"
        f"âœ… Yuborildi: {ok}\n"
        f"âŒ Xato: {fail}\n"
        f"ðŸš« Blok: {blocked}"
    )


@router.callback_query(F.data == "admin_force_sub")
async def force_sub_info(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        f"ðŸ“¢ <b>Majburiy obuna</b>\n\n"
        f"Kanal ID: <code>{CHANNEL_ID}</code>\n"
        f"Username: {CHANNEL_USERNAME or 'â€”'}\n\n"
        "Bot kanal administratori bo'lishi kerak."
    )
    await callback.answer()


@router.callback_query(F.data == "admin_settings")
async def settings(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        "âš™ï¸ <b>Sozlamalar</b>\n\n"
        f"ðŸ¤– Token: {'âœ…' if BOT_TOKEN else 'âŒ'}\n"
        f"ðŸ‘¨â€ðŸ’» Adminlar: {ADMIN_IDS}\n"
        f"ðŸ“¢ CHANNEL_ID: <code>{CHANNEL_ID}</code>\n"
        f"ðŸ“¢ Username: {CHANNEL_USERNAME or 'â€”'}\n"
        f"ðŸ’¬ Support: {SUPPORT_USERNAME}\n"
        f"ðŸ—„ Supabase: âœ…"
    )
    await callback.answer()


@router.message(F.text)
async def fallback(message: Message, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() is not None:
        return
    await register_user(message)
    if not await check_sub(bot, message.from_user.id):
        await message.answer("ðŸ“¢ Avval kanalga obuna bo'ling.", reply_markup=force_sub_kb())
        return
    query = sanitize(message.text or "")
    if len(query) < 2:
        return
    try:
        r = (
            get_sb()
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
        await message.answer("âŒ Qidirishda xatolik.")
        return
    if not rows:
        await message.answer(
            f"âŒ <b>{user_name(message)}</b>, hech narsa topilmadi.",
            reply_markup=main_menu_kb(),
        )
        return
    if len(rows) == 1:
        m = rows[0]
        fav = await is_fav(message.from_user.id, m["id"])
        await message.answer(movie_card(m), reply_markup=movie_actions_kb(m["id"], fav))
        return
    text = f"ðŸ” <b>{len(rows)} ta natija:</b>\n\n"
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows:
        text += f"â€¢ {safe(row['title'])}\n"
        buttons.append(
            [InlineKeyboardButton(text=row["title"][:32], callback_data=f"movie_{row['id']}")]
        )
    buttons.append([InlineKeyboardButton(text="ðŸ  Menyuga", callback_data="back_main")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


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
        print("ERROR: .env da yo'q:", ", ".join(missing))
        return
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    await ensure_genres()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Kino Bot production ishga tushmoqda...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
