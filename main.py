# Быстрый старт:
# 1) Создайте бота у @BotFather и получите токен.
# 2) Сохраните файлы main.py, requirements.txt и .env (пример ниже).
# 3) Запустите: 
#    python main.py
# 
# Примечание по времени: ежедневные подписки исполняются по времени в формате UTC (например, 09:00 UTC).
# 
# ----- Файл: main.py -----

import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.formatting import as_marked_section, Bold
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import aiosqlite

# Загружаем .env из папки с main.py; если нет .env — пробуем .env.example
load_dotenv(dotenv_path=Path(__file__).with_name(".env")) or load_dotenv(dotenv_path=Path(__file__).with_name(".env.example"))
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Укажите его в переменной окружения или .env")

DB_PATH = os.getenv("DB_PATH", "subscriptions.db")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ---- Источники ----
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT"
FX_USD_RUB = "https://api.exchangerate.host/latest?base=USD&symbols=RUB"

# ---- База данных ----
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('hourly','daily')),
                daily_time TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()

# ---- Курсы валют ----
async def fetch_ton_usd(client: httpx.AsyncClient) -> float:
    r = await client.get(BINANCE_TICKER, timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data["price"])  # USDT ~ USD

async def fetch_usd_rub(client: httpx.AsyncClient) -> float:
    r = await client.get("https://open.er-api.com/v6/latest/USD", timeout=10)
    r.raise_for_status()
    data = r.json()
    
    if "rates" in data and "RUB" in data["rates"]:
        return float(data["rates"]["RUB"])
    raise ValueError(f"Unexpected response: {data}")

async def get_rates() -> dict:
    async with httpx.AsyncClient() as client:
        ton_usd, usd_rub = await asyncio.gather(
            fetch_ton_usd(client),
            fetch_usd_rub(client),
        )
    ton_rub = ton_usd * usd_rub
    return {"ton_usd": ton_usd, "usd_rub": usd_rub, "ton_rub": ton_rub, "ts": datetime.now(timezone.utc)}

# ---- Форматирование ----

def fmt_rates(r: dict) -> str:
    ts = r["ts"].astimezone().strftime("%Y-%m-%d %H:%M:%S")
    ton_usd = f"{r['ton_usd']:.4f}"
    usd_rub = f"{r['usd_rub']:.2f}"
    ton_rub = f"{r['ton_rub']:.2f}"
    section = as_marked_section(
        Bold("Курсы сейчас"),
        f"USD → RUB: {usd_rub}",
        f"TON → USD: {ton_usd}",
        f"TON → RUB: {ton_rub}",
        marker="• ",
    )
    return "\n".join([section.as_html(), "", f"Обновлено: {ts}"])


def refresh_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh")],
        [InlineKeyboardButton(text="🔔 Подписаться", callback_data="sub:menu")],
    ])

# ---- Меню подписок пользователя ----

def subscribe_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Почасовая", callback_data="sub:hourly")],
        [InlineKeyboardButton(text="🗓 Ежедневно 09:00 UTC", callback_data="sub:daily:09:00")],
        [InlineKeyboardButton(text="🗓 Ежедневно 12:00 UTC", callback_data="sub:daily:12:00")],
        [InlineKeyboardButton(text="🗓 Ежедневно 18:00 UTC", callback_data="sub:daily:18:00")],
        [InlineKeyboardButton(text="❌ Отписаться от всех", callback_data="unsub:all")],
    ])

# ---- Все команды ----

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    text = (
        "Привет! Я бот курсов.\n\n"
        "Команды:\n"
        "• /rate — показать USD→RUB, TON→USD, TON→RUB\n"
        "• /subscribe — оформить подписку (почасовую или ежедневную)\n"
        "• /unsubscribe — отменить все подписки\n"
        "• /mysubs — показать активные подписки\n\n"
        "Источник: Binance (TONUSDT) и exchangerate.host."
    )
    await msg.answer(text)

@dp.message(Command("rate"))
async def cmd_rate(msg: Message):
    try:
        rates = await get_rates()
        await msg.answer(fmt_rates(rates), reply_markup=refresh_keyboard())
    except Exception as e:
        await msg.answer(f"Не удалось получить курсы: {e}")

@dp.callback_query(F.data == "refresh")
async def cb_refresh(call: CallbackQuery):
    try:
        rates = await get_rates()
        await call.message.edit_text(fmt_rates(rates), reply_markup=refresh_keyboard())
        await call.answer("Обновлено")
    except Exception as e:
        await call.answer("Ошибка обновления", show_alert=True)

@dp.message(Command("subscribe"))
async def cmd_subscribe(msg: Message):
    await msg.answer(
        "Выберите тип подписки или задайте время: /subscribe_daily 09:00 (UTC)",
        reply_markup=subscribe_menu(),
    )

@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(msg: Message):
    removed = await remove_all_subs(msg.from_user.id)
    await msg.answer("Все подписки удалены." if removed else "Подписок не было.")

@dp.message(Command("mysubs"))
async def cmd_mysubs(msg: Message):
    subs = await list_subs(msg.from_user.id)
    if not subs:
        await msg.answer("Активных подписок нет.")
        return
    lines = ["Ваши подписки:"]
    for s in subs:
        if s["kind"] == "hourly":
            lines.append("• Почасовая")
        else:
            lines.append(f"• Ежедневно в {s['daily_time']} UTC")
    lines.append("Отменить: /unsubscribe")
    await msg.answer("\n".join(lines))

@dp.message(Command("subscribe_daily"))
async def cmd_subscribe_daily(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Укажите время в формате HH:MM, например: /subscribe_daily 09:00 (UTC)")
        return
    time_str = parts[1]
    if time_str.count(":") != 1:
        await msg.answer("Нужно HH:MM (UTC)")
        return
    hh_str, mm_str = time_str.split(":")
    if not (hh_str.isdigit() and mm_str.isdigit()):
        await msg.answer("Часы и минуты должны быть числами")
        return
    hh = int(hh_str)
    mm = int(mm_str)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        await msg.answer("Допустимо 00:00–23:59 (UTC)")
        return
    await add_sub(user_id=msg.from_user.id, chat_id=msg.chat.id, kind="daily", daily_time=f"{hh:02d}:{mm:02d}")
    await msg.answer(f"Готово! Ежедневная подписка в {hh:02d}:{mm:02d} UTC оформлена.")

@dp.callback_query(F.data == "sub:menu")
async def cb_sub_menu(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=subscribe_menu())
    await call.answer()

@dp.callback_query(F.data == "sub:hourly")
async def cb_sub_hourly(call: CallbackQuery):
    await add_sub(user_id=call.from_user.id, chat_id=call.message.chat.id, kind="hourly")
    await call.answer("Почасовая подписка оформлена.", show_alert=True)

@dp.callback_query(F.data.startswith("sub:daily:"))
async def cb_sub_daily(call: CallbackQuery):
    time_str = call.data.split(":", 2)[2]
    await add_sub(user_id=call.from_user.id, chat_id=call.message.chat.id, kind="daily", daily_time=time_str)
    await call.answer(f"Ежедневно в {time_str} UTC.", show_alert=True)

@dp.callback_query(F.data == "unsub:all")
async def cb_unsub_all(call: CallbackQuery):
    await remove_all_subs(call.from_user.id)
    await call.answer("Подписки удалены.", show_alert=True)

# ---- Работы с Базой Данных  ----

async def add_sub(user_id: int, chat_id: int, kind: str, daily_time: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if kind == "daily" and not daily_time:
            raise ValueError("daily_time обязателен для daily")
        if kind == "hourly":
            await db.execute("DELETE FROM subs WHERE user_id=? AND kind='hourly'", (user_id,))
        else:
            await db.execute("DELETE FROM subs WHERE user_id=? AND kind='daily'", (user_id,))
        await db.execute(
            "INSERT INTO subs(user_id, chat_id, kind, daily_time, created_at) VALUES(?,?,?,?,?)",
            (user_id, chat_id, kind, daily_time, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

async def remove_all_subs(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM subs WHERE user_id=?", (user_id,))
        await db.commit()
        return cur.rowcount

async def list_subs(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT kind, daily_time FROM subs WHERE user_id=? ORDER BY kind", (user_id,))
        rows = await cur.fetchall()
        return [{"kind": r[0], "daily_time": r[1]} for r in rows]

# ---- Планировщик уведомлений ----

async def notifier_loop():
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            minute = now_utc.minute
            time_hhmm = now_utc.strftime("%H:%M")
            rates = await get_rates()
            async with aiosqlite.connect(DB_PATH) as db:
                if minute == 0:
                    async with db.execute("SELECT DISTINCT chat_id FROM subs WHERE kind='hourly'") as cur:
                        for row in await cur.fetchall():
                            await bot.send_message(row[0], fmt_rates(rates))
                async with db.execute("SELECT DISTINCT chat_id FROM subs WHERE kind='daily' AND daily_time=?", (time_hhmm,)) as cur:
                    for row in await cur.fetchall():
                        await bot.send_message(row[0], fmt_rates(rates))
        except Exception as e:
            print("Notifier error:", e)
        finally:
            await asyncio.sleep(60)

# ---- Entrypoint ----

async def main():
    print("Bot starting…")
    await init_db()
    asyncio.create_task(notifier_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped")

# ----- Файл: requirements.txt -----
# aiogram>=3.7
# httpx>=0.27.0
# python-dotenv>=1.0.1
# aiosqlite>=0.20.0

# ----- Файл: .env.example -----
# BOT_TOKEN=1234567890:ABCDEF-your-telegram-bot-token

# ----- Файл: Dockerfile -----
# Используйте следующий Dockerfile в отдельном файле с именем Dockerfile:
#
# FROM python:3.11-slim
# WORKDIR /app
# COPY requirements.txt ./
# RUN pip install --no-cache-dir -r requirements.txt
# COPY main.py ./
# ENV PYTHONUNBUFFERED=1
# CMD ["python", "main.py"]
