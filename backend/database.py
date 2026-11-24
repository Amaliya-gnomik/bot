# database.py - всё, что связано с базой данных SQLite (один файл schedule.db).

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "schedule.db")

ROLES = ("boss", "employee")
PLATFORMS = ("Ozon", "WB", "Yandex")
ADJUSTMENT_TYPES = ("fine", "bonus")


@contextmanager
def get_db():
    """Открывает базу, сохраняет изменения и закрывает."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Создаёт таблицы, если их ещё нет."""
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                telegram_id INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                role        TEXT NOT NULL DEFAULT 'employee'
                            CHECK (role IN ('boss', 'employee')),
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
        db.execute("""
            CREATE TABLE IF NOT EXISTS availability (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                date        TEXT NOT NULL,
                platform    TEXT NOT NULL CHECK (platform IN ('Ozon', 'WB', 'Yandex')),
                is_assigned INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (employee_id) REFERENCES employees (telegram_id),
                UNIQUE (employee_id, date)
            )""")
        db.execute("""
            CREATE TABLE IF NOT EXISTS adjustments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                type        TEXT NOT NULL CHECK (type IN ('fine', 'bonus')),
                amount      REAL NOT NULL CHECK (amount > 0),
                comment     TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees (telegram_id)
            )""")


# ---------------- СОТРУДНИКИ ----------------

def add_employee(telegram_id: int, name: str, role: str = "employee"):
    """Добавляет сотрудника; если он уже есть - обновляет имя и роль."""
    if role not in ROLES:
        raise ValueError(f"Роль должна быть одной из {ROLES}")
    with get_db() as db:
        db.execute("""
            INSERT INTO employees (telegram_id, name, role) VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET name = excluded.name, role = excluded.role
        """, (telegram_id, name, role))


def get_employee(telegram_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM employees WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return dict(row) if row else None


def get_all_employees():
    with get_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM employees ORDER BY name").fetchall()]


# ---------------- ДОСТУПНОСТЬ ----------------

def add_availability(employee_id: int, date: str, platform: str):
    """Один день доступности (если запись уже есть - меняет площадку)."""
    if platform not in PLATFORMS:
        raise ValueError(f"Площадка должна быть одной из {PLATFORMS}")
    with get_db() as db:
        db.execute("""
            INSERT INTO availability (employee_id, date, platform) VALUES (?, ?, ?)
            ON CONFLICT(employee_id, date) DO UPDATE SET platform = excluded.platform
        """, (employee_id, date, platform))


def replace_availability(employee_id: int, days: list, from_date: str):
    """Сохраняет выбор сотрудника из Mini App.
    days = [{"date": "2026-12-05", "platform": "Ozon"}, ...]
    - дни, которые сотрудник снял, удаляются (начиная с from_date);
    - дни, на которые начальник уже поставил в график, не трогаем.
    """
    keep = {d["date"] for d in days}
    with get_db() as db:
        old = db.execute(
            "SELECT date FROM availability WHERE employee_id = ? AND date >= ? AND is_assigned = 0",
            (employee_id, from_date),
        ).fetchall()
        for r in old:
            if r["date"] not in keep:
                db.execute("DELETE FROM availability WHERE employee_id = ? AND date = ?",
                           (employee_id, r["date"]))
        for d in days:
            db.execute("""
                INSERT INTO availability (employee_id, date, platform) VALUES (?, ?, ?)
                ON CONFLICT(employee_id, date) DO UPDATE SET platform = excluded.platform
                WHERE is_assigned = 0
            """, (employee_id, d["date"], d["platform"]))


def get_availability(date_from: str = None, date_to: str = None, employee_id: int = None):
    """Доступность (с именами). Все параметры необязательны."""
    query = """
        SELECT a.date, a.platform, a.is_assigned, e.telegram_id, e.name
        FROM availability a JOIN employees e ON e.telegram_id = a.employee_id
        WHERE 1 = 1
    """
    params = []
    if date_from:
        query += " AND a.date >= ?"; params.append(date_from)
    if date_to:
        query += " AND a.date <= ?"; params.append(date_to)
    if employee_id:
        query += " AND a.employee_id = ?"; params.append(employee_id)
    query += " ORDER BY a.date, e.name"
    with get_db() as db:
        return [dict(r) for r in db.execute(query, params).fetchall()]


def set_assigned(employee_id: int, date: str, assigned: bool = True):
    """Начальник ставит сотрудника в график / снимает."""
    with get_db() as db:
        db.execute("UPDATE availability SET is_assigned = ? WHERE employee_id = ? AND date = ?",
                   (1 if assigned else 0, employee_id, date))


# ---------------- ШТРАФЫ И БОНУСЫ ----------------

def add_adjustment(employee_id: int, type_: str, amount: float, comment: str = ""):
    if type_ not in ADJUSTMENT_TYPES:
        raise ValueError(f"Тип должен быть одним из {ADJUSTMENT_TYPES}")
    with get_db() as db:
        db.execute("INSERT INTO adjustments (employee_id, type, amount, comment) VALUES (?, ?, ?, ?)",
                   (employee_id, type_, amount, comment))


def get_adjustments(employee_id: int):
    with get_db() as db:
        rows = db.execute("SELECT * FROM adjustments WHERE employee_id = ? ORDER BY id DESC",
                          (employee_id,)).fetchall()
        return [dict(r) for r in rows]
