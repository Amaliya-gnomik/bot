# bot.py - логика Telegram-бота (aiogram 3).
# Сотрудник: /start, /my
# Начальник: /avail (кто доступен и постановка в график), /staff, /fine, /bonus

import os
import datetime
from html import escape

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db

load_dotenv()  # читает .env (на Render значения берутся из настроек сервиса)
BOT_TOKEN = os.environ["BOT_TOKEN"]
BOSS_ID = int(os.environ["BOSS_ID"])      # Telegram ID начальника
WEBAPP_URL = os.environ["WEBAPP_URL"]     # адрес Mini App на GitHub Pages

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

PLATFORM_RU = {"Ozon": "Ozon", "WB": "WB", "Yandex": "Яндекс"}


def is_boss(user_id: int) -> bool:
    return user_id == BOSS_ID


def pretty(date_str: str) -> str:
    """'2026-12-05' -> '05.12'"""
    _, m, d = date_str.split("-")
    return f"{d}.{m}"


async def notify_boss(text: str):
    try:
        await bot.send_message(BOSS_ID, text)
    except Exception:
        pass  # начальник мог ещё не нажать /start


# ---------------- /start и /my ----------------

@dp.message(CommandStart())
async def cmd_start(m: Message):
    role = "boss" if is_boss(m.from_user.id) else "employee"
    db.add_employee(m.from_user.id, m.from_user.full_name, role)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 Открыть календарь", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    text = "Привет! Нажмите кнопку и отметьте дни, когда можете выйти.\nМои смены и штрафы/бонусы: /my"
    if role == "boss":
        text += ("\n\nКоманды начальника:\n/avail - кто доступен, поставить в график\n"
                 "/staff - список сотрудников с ID\n"
                 "/fine ID сумма причина\n/bonus ID сумма причина")
    await m.answer(text, reply_markup=kb)


@dp.message(Command("my"))
async def cmd_my(m: Message):
    today = datetime.date.today().isoformat()
    shifts = [r for r in db.get_availability(date_from=today, employee_id=m.from_user.id) if r["is_assigned"]]
    lines = ["Ваши смены:"]
    lines += [f"• {pretty(r['date'])} - {PLATFORM_RU[r['platform']]}" for r in shifts] or ["пока нет"]
    adj = db.get_adjustments(m.from_user.id)[:10]
    if adj:
        lines.append("\nПоследние штрафы/бонусы:")
        for a in adj:
            sign = "➖ штраф" if a["type"] == "fine" else "➕ бонус"
            lines.append(f"{sign} {a['amount']:g} ₽ {a['comment'] or ''}")
    await m.answer("\n".join(lines))


# ---------------- Начальник: график ----------------

def dates_view():
    today = datetime.date.today()
    rows = db.get_availability(date_from=today.isoformat(),
                               date_to=(today + datetime.timedelta(days=30)).isoformat())
    if not rows:
        return "Пока никто не отметил доступные дни.", None
    counts = {}
    for r in rows:
        counts[r["date"]] = counts.get(r["date"], 0) + 1
    kb = InlineKeyboardBuilder()
    for date, n in sorted(counts.items()):
        kb.button(text=f"{pretty(date)} · {n} чел.", callback_data=f"day:{date}")
    kb.adjust(2)
    return "Выберите день:", kb.as_markup()


def day_view(date: str):
    rows = db.get_availability(date_from=date, date_to=date)
    kb = InlineKeyboardBuilder()
    for r in rows:
        mark = "✅" if r["is_assigned"] else "⬜"
        kb.button(text=f"{mark} {r['name']} · {PLATFORM_RU[r['platform']]}",
                  callback_data=f"as:{r['telegram_id']}:{date}")
    kb.button(text="⬅ К списку дней", callback_data="days")
    kb.adjust(1)
    return f"{pretty(date)}: нажмите на сотрудника, чтобы поставить в график или снять.", kb.as_markup()


@dp.message(Command("avail"))
async def cmd_avail(m: Message):
    if not is_boss(m.from_user.id):
        return await m.answer("Эта команда только для начальника.")
    text, kb = dates_view()
    await m.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "days")
async def cb_days(call: CallbackQuery):
    if not is_boss(call.from_user.id):
        return await call.answer("Только для начальника", show_alert=True)
    text, kb = dates_view()
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("day:"))
async def cb_day(call: CallbackQuery):
    if not is_boss(call.from_user.id):
        return await call.answer("Только для начальника", show_alert=True)
    text, kb = day_view(call.data.split(":", 1)[1])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("as:"))
async def cb_assign(call: CallbackQuery):
    if not is_boss(call.from_user.id):
        return await call.answer("Только для начальника", show_alert=True)
    _, emp_id, date = call.data.split(":")
    emp_id = int(emp_id)
    rows = db.get_availability(date_from=date, date_to=date, employee_id=emp_id)
    if not rows:
        return await call.answer("Запись не найдена (сотрудник её снял)", show_alert=True)
    new_state = not rows[0]["is_assigned"]
    db.set_assigned(emp_id, date, new_state)
    msg = (f"✅ Вас поставили в график: {pretty(date)}, {PLATFORM_RU[rows[0]['platform']]}"
           if new_state else f"❌ Ваша смена {pretty(date)} снята.")
    try:
        await bot.send_message(emp_id, msg)
    except Exception:
        pass
    text, kb = day_view(date)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Готово")


# ---------------- Начальник: сотрудники, штрафы, бонусы ----------------

@dp.message(Command("staff"))
async def cmd_staff(m: Message):
    if not is_boss(m.from_user.id):
        return await m.answer("Эта команда только для начальника.")
    people = [e for e in db.get_all_employees() if e["role"] != "boss"]
    if not people:
        return await m.answer("Пока нет сотрудников (они появляются после /start).")
    lines = [f"{escape(e['name'])} - <code>{e['telegram_id']}</code>" for e in people]
    await m.answer("Сотрудники (нажмите на ID, чтобы скопировать):\n" + "\n".join(lines), parse_mode="HTML")


async def adjust(m: Message, command: CommandObject, kind: str):
    if not is_boss(m.from_user.id):
        return await m.answer("Эта команда только для начальника.")
    usage = f"Формат: /{command.command} ID сумма причина\nПример: /{command.command} 123456789 500 опоздание"
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 2:
        return await m.answer(usage)
    try:
        emp_id = int(parts[0])
        amount = float(parts[1].replace(",", "."))
    except ValueError:
        return await m.answer(usage)
    if amount <= 0:
        return await m.answer("Сумма должна быть больше нуля.")
    if not db.get_employee(emp_id):
        return await m.answer("Сотрудник с таким ID не найден. Смотрите /staff")
    comment = parts[2] if len(parts) > 2 else ""
    db.add_adjustment(emp_id, kind, amount, comment)
    title = "➖ Вам выписан штраф" if kind == "fine" else "➕ Вам начислен бонус"
    try:
        await bot.send_message(emp_id, f"{title}: {amount:g} ₽\n{comment}")
        await m.answer("Готово, сотрудник уведомлён.")
    except Exception:
        await m.answer("Записано, но уведомить не получилось (сотрудник не запускал бота).")


@dp.message(Command("fine"))
async def cmd_fine(m: Message, command: CommandObject):
    await adjust(m, command, "fine")


@dp.message(Command("bonus"))
async def cmd_bonus(m: Message, command: CommandObject):
    await adjust(m, command, "bonus")
