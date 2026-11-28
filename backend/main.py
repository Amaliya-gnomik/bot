# main.py - сервер FastAPI. Он же запускает бота.

import asyncio
import datetime
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram.types import MenuButtonWebApp, WebAppInfo

import database as db
from bot import bot, dp, BOT_TOKEN, BOSS_ID, WEBAPP_URL, notify_boss
await bot.delete_webhook(drop_pending_updates=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="График", web_app=WebAppInfo(url=WEBAPP_URL)))
    task = asyncio.create_task(dp.start_polling(bot, handle_signals=False, close_bot_session=False))
    yield
    task.cancel()
    await bot.session.close()


app = FastAPI(lifespan=lifespan)

# Настройка CORS: разрешаем все домены и заголовки
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def check_init_data(init_data: str):
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        if time.time() - int(parsed["auth_date"]) > 86400:
            return None
        return json.loads(parsed["user"])
    except Exception:
        return None


def current_user(x_init_data: str = Header(default="")):
    user = check_init_data(x_init_data)
    if not user:
        raise HTTPException(401, "Откройте приложение через Telegram")
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or str(user["id"])
    role = "boss" if user["id"] == BOSS_ID else "employee"
    db.add_employee(user["id"], name, role)
    user["name"] = name
    return user


class Day(BaseModel):
    date: str
    platform: str


class SaveRequest(BaseModel):
    days: list[Day]


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/availability")
async def my_availability(user=Depends(current_user)):
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    rows = db.get_availability(date_from=yesterday, employee_id=user["id"])
    return [{"date": r["date"], "platform": r["platform"], "is_assigned": bool(r["is_assigned"])} for r in rows]


@app.post("/api/availability")
async def save_availability(req: SaveRequest, user=Depends(current_user)):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    if len(req.days) > 62:
        raise HTTPException(400, "Слишком много дней")
    clean = []
    for d in req.days:
        try:
            day = datetime.date.fromisoformat(d.date)
        except ValueError:
            raise HTTPException(400, "Неверная дата")
        if day < yesterday:
            raise HTTPException(400, "Нельзя выбрать прошедший день")
        if d.platform not in db.PLATFORMS:
            raise HTTPException(400, "Неверная площадка")
        clean.append({"date": day.isoformat(), "platform": d.platform})
    db.replace_availability(user["id"], clean, yesterday.isoformat())
    if user["id"] != BOSS_ID:
        await notify_boss(f"📅 {user['name']} обновил(а) доступность: {len(clean)} дн.")
    return {"saved": len(clean)}


# --- ВАЖНО: Явный обработчик для OPTIONS-запросов (Preflight) ---
# Иногда FastAPI/Starlette не отвечает на preflight автоматически, если заголовки сложные.
# Этот обработчик гарантирует, что браузер получит разрешение.
@app.options("/{full_path:path}")
async def options_handler(full_path: str):
    return {"message": "OK"}
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)