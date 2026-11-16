import asyncio
import os
import sqlite3
import time
import random
import math

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import (Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery)
from dotenv import load_dotenv


# ==================================================
# 1. НАСТРОЙКИ
# ==================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Не найден токен Telegram-бота!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

DB_NAME = "imperiya.db"

FARM_INCOME = 20
SAWMILL_INCOME = 15
QUARRY_INCOME = 10

TAX_PER_10_PEOPLE = 10

POPULATION_GROWTH = 2
FOOD_PER_PERSON = 0.02

BASE_CAPACITY = 100
HOUSE_CAPACITY = 50

MAX_OFFLINE_HOURS = 24

ATTACK_COOLDOWN = 3600
DEFENSE_SHIELD = 1800


# ==================================================
# 2. БАЗА ДАННЫХ
# ==================================================


# ==================================================
# 2A. ОБЩАЯ КАРТА МИРА (первый этап)
# ==================================================

WORLD_SIZE = 100  # 100 x 100 = 10 000 провинций
MAP_RADIUS = 2
CLAIM_COST = {"gold": 300, "wood": 150, "stone": 100}
TERRAINS = ["Лес", "Равнина", "Горы", "Пустошь", "Побережье"]


def create_world_if_needed(db):
    """Создаёт карту один раз; существующие провинции не перезаписываются."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS provinces (
            id INTEGER PRIMARY KEY, x INTEGER NOT NULL, y INTEGER NOT NULL,
            terrain TEXT NOT NULL, resource TEXT NOT NULL, owner_id INTEGER,
            level INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
            UNIQUE(x, y)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_provinces_owner ON provinces(owner_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_provinces_coordinates ON provinces(x, y)")
    if db.execute("SELECT COUNT(*) FROM provinces").fetchone()[0] == 0:
        rng = random.Random(20261116)
        rows = []
        pid = 1
        for y in range(WORLD_SIZE):
            for x in range(WORLD_SIZE):
                rows.append((
                    pid, x, y, rng.choice(TERRAINS),
                    rng.choice(("Золото", "Дерево", "Камень", "Еда")),
                    None, 1, int(time.time())
                ))
                pid += 1
        db.executemany(
            "INSERT INTO provinces (id,x,y,terrain,resource,owner_id,level,created_at) VALUES (?,?,?,?,?,?,?,?)",
            rows
        )


def assign_starting_province(db, user_id):
    row = db.execute(
        "SELECT province_id FROM players WHERE user_id=?",
        (user_id,)
    ).fetchone()
    if not row or row[0] is not None:
        return
    province = db.execute(
        "SELECT id FROM provinces WHERE owner_id IS NULL ORDER BY ABS(x-?) + ABS(y-?), id LIMIT 1",
        (WORLD_SIZE // 2, WORLD_SIZE // 2)
    ).fetchone()
    if province:
        db.execute(
            "UPDATE provinces SET owner_id=? WHERE id=? AND owner_id IS NULL",
            (user_id, province[0])
        )
        db.execute(
            "UPDATE players SET province_id=? WHERE user_id=?",
            (province[0], user_id)
        )


def province_symbol(terrain):
    return {
        "Лес": "🌲",
        "Равнина": "🌾",
        "Горы": "⛰",
        "Пустошь": "🏜",
        "Побережье": "🌊"
    }.get(terrain, "·")


def init_db():

    with sqlite3.connect(DB_NAME) as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                city_name TEXT DEFAULT 'Новая столица',
                gold INTEGER DEFAULT 1000,
                wood INTEGER DEFAULT 500,
                stone INTEGER DEFAULT 500,
                food INTEGER DEFAULT 500,
                population INTEGER DEFAULT 100,
                farm_level INTEGER DEFAULT 0,
                sawmill_level INTEGER DEFAULT 0,
                quarry_level INTEGER DEFAULT 0,
                last_update INTEGER DEFAULT 0,
                house_level INTEGER DEFAULT 0,
                food_remainder REAL DEFAULT 0,
                barracks_level INTEGER DEFAULT 0
            )
        """)

        columns = [
            row[1] for row in
            db.execute("PRAGMA table_info(players)").fetchall()
        ]

        migrations = {
            "last_update": "INTEGER DEFAULT 0",
            "house_level": "INTEGER DEFAULT 0",
            "food_remainder": "REAL DEFAULT 0",
            "barracks_level": "INTEGER DEFAULT 0",
            "province_id": "INTEGER"
        }

        for column, definition in migrations.items():

            if column not in columns:

                db.execute(
                    f"ALTER TABLE players ADD COLUMN "
                    f"{column} {definition}"
                )

        db.execute("""
            CREATE TABLE IF NOT EXISTS armies (
                user_id INTEGER PRIMARY KEY,
                swordsmen INTEGER DEFAULT 0,
                archers INTEGER DEFAULT 0,
                cavalry INTEGER DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS heroes (
                user_id INTEGER PRIMARY KEY,
                name TEXT DEFAULT 'Артемий',
                level INTEGER DEFAULT 1,
                experience INTEGER DEFAULT 0
            )
        """)

        # Положение единого армейского отряда на общей карте.
        db.execute("""
            CREATE TABLE IF NOT EXISTS army_positions (
                user_id INTEGER PRIMARY KEY,
                province_id INTEGER NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS attack_cooldowns (
                user_id INTEGER PRIMARY KEY,
                last_attack INTEGER DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS defense_shields (
                user_id INTEGER PRIMARY KEY,
                shield_until INTEGER DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS battle_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attacker_id INTEGER,
                defender_id INTEGER,
                winner_id INTEGER,
                attacker_power INTEGER,
                defender_power INTEGER,
                reward_gold INTEGER,
                created_at INTEGER
            )
        """)

        # Новые игровые системы: ежедневные награды, добыча и рынок.
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_rewards (
                user_id INTEGER PRIMARY KEY, last_claim INTEGER NOT NULL DEFAULT 0,
                streak INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER NOT NULL, item_name TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, item_name)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS equipment (
                user_id INTEGER NOT NULL, slot TEXT NOT NULL, item_name TEXT NOT NULL,
                attack_bonus INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, slot)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS dungeon_cooldowns (
                user_id INTEGER PRIMARY KEY, last_run INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS auctions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL,
                item_name TEXT NOT NULL, quantity INTEGER NOT NULL, price INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        # Создаём армии и героев старым игрокам
        db.execute("""
            INSERT OR IGNORE INTO armies (user_id)
            SELECT user_id FROM players
        """)

        db.execute("""
            INSERT OR IGNORE INTO heroes (user_id)
            SELECT user_id FROM players
        """)

        # Старым игрокам не начисляем ресурсы задним числом
        db.execute("""
            UPDATE players
            SET last_update = ?
            WHERE last_update IS NULL OR last_update = 0
        """, (int(time.time()),))

        # Карта добавляется рядом со старой экономикой; старые поля не меняются.
        create_world_if_needed(db)
        for row in db.execute(
            "SELECT user_id FROM players WHERE province_id IS NULL"
        ).fetchall():
            assign_starting_province(db, row[0])

        # Старые армии размещаем в столицах; существующие позиции не сбрасываем.
        db.execute("""
            INSERT OR IGNORE INTO army_positions (user_id, province_id)
            SELECT user_id, province_id FROM players
            WHERE province_id IS NOT NULL
        """)


# ==================================================
# 3. РЕГИСТРАЦИЯ И ПОЛУЧЕНИЕ ДАННЫХ
# ==================================================

def register_player(user_id, username):

    now = int(time.time())

    with sqlite3.connect(DB_NAME) as db:

        db.execute("""
            INSERT OR IGNORE INTO players
            (user_id, username, last_update)
            VALUES (?, ?, ?)
        """, (user_id, username, now))

        # Автоматически создаём армию
        db.execute("""
            INSERT OR IGNORE INTO armies (user_id)
            VALUES (?)
        """, (user_id,))

        # Автоматически создаём героя
        db.execute("""
            INSERT OR IGNORE INTO heroes
            (user_id, name, level, experience)
            VALUES (?, 'Артемий', 1, 0)
        """, (user_id,))
        assign_starting_province(db, user_id)
        db.execute("""
            INSERT OR IGNORE INTO army_positions (user_id, province_id)
            SELECT user_id, province_id FROM players
            WHERE user_id = ? AND province_id IS NOT NULL
        """, (user_id,))


def get_player(user_id):

    with sqlite3.connect(DB_NAME) as db:

        db.row_factory = sqlite3.Row

        result = db.execute(
            "SELECT * FROM players WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        return dict(result) if result else None


def get_army(user_id):

    with sqlite3.connect(DB_NAME) as db:

        db.row_factory = sqlite3.Row

        result = db.execute(
            "SELECT * FROM armies WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        return dict(result) if result else None


def get_hero(user_id):

    with sqlite3.connect(DB_NAME) as db:

        db.row_factory = sqlite3.Row

        result = db.execute(
            "SELECT * FROM heroes WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        return dict(result) if result else None
# ==================================================
# 4. ЭКОНОМИКА
# ==================================================

def city_capacity(player):

    return (
        BASE_CAPACITY
        + player["house_level"] * HOUSE_CAPACITY
    )


def calculate_income(player):

    return {
        "gold": (
            player["population"] // 10
        ) * TAX_PER_10_PEOPLE,

        "wood": (
            player["sawmill_level"] * SAWMILL_INCOME
        ),

        "stone": (
            player["quarry_level"] * QUARRY_INCOME
        ),

        "food": (
            player["farm_level"] * FARM_INCOME
        )
    }


def collect_income(user_id):

    now = int(time.time())

    with sqlite3.connect(DB_NAME) as db:

        db.row_factory = sqlite3.Row

        result = db.execute(
            "SELECT * FROM players WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not result:
            return

        p = dict(result)

        elapsed = max(
            0,
            now - p["last_update"]
        )

        elapsed = min(
            elapsed,
            MAX_OFFLINE_HOURS * 3600
        )

        hours = elapsed / 3600

        if hours <= 0:
            return

        income = calculate_income(p)

        gold = int(income["gold"] * hours)
        wood = int(income["wood"] * hours)
        stone = int(income["stone"] * hours)

        food_produced = income["food"] * hours

        food_consumed = (
            p["population"] * FOOD_PER_PERSON * hours
        )

        food_balance = (
            p["food"]
            + p["food_remainder"]
            + food_produced
            - food_consumed
        )

        population = p["population"]

        if food_balance >= 0:

            growth = int(POPULATION_GROWTH * hours)

            population = min(
                city_capacity(p),
                population + growth
            )

            food = int(food_balance)

            remainder = food_balance - food

        else:

            loss = max(1, int(hours))

            population = max(
                10,
                population - loss
            )

            food = 0
            remainder = 0

        db.execute("""
            UPDATE players
            SET gold = gold + ?,
                wood = wood + ?,
                stone = stone + ?,
                food = ?,
                food_remainder = ?,
                population = ?,
                last_update = ?
            WHERE user_id = ?
        """, (
            gold,
            wood,
            stone,
            food,
            remainder,
            population,
            now,
            user_id
        ))


# ==================================================
# 5. СТРОИТЕЛЬСТВО
# ==================================================

BUILDINGS = {

    "🌾 Ферма": {
        "column": "farm_level",
        "gold": 100,
        "wood": 50,
        "stone": 0
    },

    "🪓 Лесопилка": {
        "column": "sawmill_level",
        "gold": 150,
        "wood": 100,
        "stone": 0
    },

    "⛏ Каменоломня": {
        "column": "quarry_level",
        "gold": 200,
        "wood": 100,
        "stone": 50
    },

    "🏠 Жилой дом": {
        "column": "house_level",
        "gold": 250,
        "wood": 150,
        "stone": 50
    },

    "🏰 Казармы": {
        "column": "barracks_level",
        "gold": 500,
        "wood": 300,
        "stone": 100
    }
}


# ==================================================
# 6. ВОЙСКА
# ==================================================

UNITS = {

    "⚔️ Мечники": {
        "column": "swordsmen",
        "amount": 10,
        "gold": 200,
        "food": 100,
        "wood": 0
    },

    "🏹 Лучники": {
        "column": "archers",
        "amount": 10,
        "gold": 250,
        "food": 0,
        "wood": 100
    },

    "🐎 Кавалерия": {
        "column": "cavalry",
        "amount": 5,
        "gold": 400,
        "food": 150,
        "wood": 0
    }
}

UNIT_BUTTONS = {
    "⚔️ Нанять мечников": "⚔️ Мечники",
    "🏹 Нанять лучников": "🏹 Лучники",
    "🐎 Нанять кавалерию": "🐎 Кавалерия"
}

UNIT_POWER = {
    "swordsmen": 10,
    "archers": 12,
    "cavalry": 20
}


# ==================================================
# 7. КЛАВИАТУРЫ
# ==================================================

def main_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🏰 Мой город"),
                KeyboardButton(text="⚒ Строительство")
            ],
            [
                KeyboardButton(text="💰 Ресурсы"),
                KeyboardButton(text="📊 Экономика")
            ],
            [
                KeyboardButton(text="⚔️ Армия"),
                KeyboardButton(text="🦸 Мой герой")
            ],
            [
                KeyboardButton(text="🛡 Кланы"),
                KeyboardButton(text="🗺 Карта мира")
            ],
            [
                KeyboardButton(text="🎁 Ежедневная награда"),
                KeyboardButton(text="🎒 Инвентарь")
            ],
            [
                KeyboardButton(text="🐉 Подземелье"),
                KeyboardButton(text="🏷 Аукцион")
            ],
            [
                KeyboardButton(text="👤 Мой профиль")
            ]
        ],
        resize_keyboard=True
    )


def building_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🌾 Ферма"),
                KeyboardButton(text="🪓 Лесопилка")
            ],
            [
                KeyboardButton(text="⛏ Каменоломня"),
                KeyboardButton(text="🏠 Жилой дом")
            ],
            [
                KeyboardButton(text="🏰 Казармы")
            ],
            [
                KeyboardButton(text="⬅️ Назад")
            ]
        ],
        resize_keyboard=True
    )


def army_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⚔️ Нанять мечников"),
                KeyboardButton(text="🏹 Нанять лучников")
            ],
            [
                KeyboardButton(text="🐎 Нанять кавалерию")
            ],
            [
                KeyboardButton(text="🗺 Противники"),
                KeyboardButton(text="📍 Положение армии")
            ],
            [
                KeyboardButton(text="📜 История боёв")
            ],
            [
                KeyboardButton(text="⬅️ Назад")
            ]
        ],
        resize_keyboard=True
    )


def hero_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⭐ Улучшить героя")
            ],
            [
                KeyboardButton(text="⬅️ Назад")
            ]
        ],
        resize_keyboard=True
    )


# ==================================================
# 8. СТАРТ ИГРЫ
# ==================================================

@dp.message(CommandStart())
async def start_game(message: Message):

    user = message.from_user

    register_player(
        user.id,
        user.username or "Игрок"
    )

    collect_income(user.id)

    await message.answer(
        "👑 ДОБРО ПОЖАЛОВАТЬ В ИМПЕРИЮ!\n\n"
        "Твоя столица ждёт развития!\n\n"
        "🏰 Строй город.\n"
        "🌾 Производи ресурсы.\n"
        "👥 Развивай население.\n"
        "⚔️ Собирай армию.\n"
        "🦸 Прокачивай героя.\n"
        "🛡 Сражайся с другими правителями!\n\n"
        "Выбери действие:",
        reply_markup=main_menu()
            )


# ==================================================
# 9. МОЙ ГОРОД
# ==================================================

@dp.message(lambda m: m.text == "🏰 Мой город")
async def show_city(message: Message):

    uid = message.from_user.id
    collect_income(uid)
    p = get_player(uid)

    if not p:
        await message.answer("Сначала напиши /start")
        return

    income = calculate_income(p)
    city_photo = os.path.join(os.path.dirname(__file__), "city_3d.png")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌾 Ферма", callback_data="citybuild:farm"),
            InlineKeyboardButton(text="🪓 Лесопилка", callback_data="citybuild:sawmill")
        ],
        [
            InlineKeyboardButton(text="⛏ Каменоломня", callback_data="citybuild:quarry"),
            InlineKeyboardButton(text="🏠 Жилой дом", callback_data="citybuild:house")
        ],
        [
            InlineKeyboardButton(text="🏰 Казармы", callback_data="citybuild:barracks")
        ]
    ])

    caption = (
        f"🏰 ГОРОД: {p['city_name']}\\n"
        f"👥 Население: {p['population']} / {city_capacity(p)}\\n\\n"
        f"🌾 Ферма — уровень {p['farm_level']}\\n"
        f"🪓 Лесопилка — уровень {p['sawmill_level']}\\n"
        f"⛏ Каменоломня — уровень {p['quarry_level']}\\n"
        f"🏠 Жилые дома — уровень {p['house_level']}\\n"
        f"🏰 Казармы — уровень {p['barracks_level']}\\n\\n"
        f"📈 Доход в час: 🪙 {income['gold']} | 🪵 {income['wood']} | "
        f"🪨 {income['stone']} | 🌾 {income['food']}\\n\\n"
        "Нажми на здание, чтобы улучшить его."
    )

    if os.path.exists(city_photo):
        await message.answer_photo(
            FSInputFile(city_photo),
            caption=caption,
            reply_markup=keyboard
        )
    else:
        await message.answer(
            "Не найдена картинка city_3d.png. Положи её рядом с bot_v3.py.\\n\\n" + caption,
            reply_markup=keyboard
        )


@dp.callback_query(lambda c: c.data and c.data.startswith("citybuild:"))
async def upgrade_city_building(callback: CallbackQuery):
    uid = callback.from_user.id
    building_key = callback.data.split(":", 1)[1]

    building_names = {
        "farm": "🌾 Ферма",
        "sawmill": "🪓 Лесопилка",
        "quarry": "⛏ Каменоломня",
        "house": "🏠 Жилой дом",
        "barracks": "🏰 Казармы"
    }
    name = building_names.get(building_key)
    if not name or name not in BUILDINGS:
        await callback.answer("Неизвестное здание", show_alert=True)
        return

    collect_income(uid)
    building = BUILDINGS[name]
    column = building["column"]

    with sqlite3.connect(DB_NAME) as db:
        p = db.execute(
            "SELECT gold, wood, stone FROM players WHERE user_id=?",
            (uid,)
        ).fetchone()

        if not p:
            await callback.answer("Сначала напиши /start", show_alert=True)
            return

        if p[0] < building["gold"] or p[1] < building["wood"] or p[2] < building["stone"]:
            await callback.answer(
                f"Не хватает ресурсов! Нужно: {building['gold']} золота, "
                f"{building['wood']} дерева, {building['stone']} камня.",
                show_alert=True
            )
            return

        db.execute(
            f"""UPDATE players
                SET gold=gold-?, wood=wood-?, stone=stone-?,
                    {column}={column}+1
                WHERE user_id=?""",
            (building["gold"], building["wood"], building["stone"], uid)
        )

    updated = get_player(uid)
    await callback.answer(f"{name}: улучшено до уровня {updated[column]}!")
    # Обновляем экран города вместе с уровнями и остатком ресурсов.
    income = calculate_income(updated)
    text = (
        f"🏰 ГОРОД: {updated['city_name']}\\n"
        f"👥 Население: {updated['population']} / {city_capacity(updated)}\\n\\n"
        f"🌾 Ферма — уровень {updated['farm_level']}\\n"
        f"🪓 Лесопилка — уровень {updated['sawmill_level']}\\n"
        f"⛏ Каменоломня — уровень {updated['quarry_level']}\\n"
        f"🏠 Жилые дома — уровень {updated['house_level']}\\n"
        f"🏰 Казармы — уровень {updated['barracks_level']}\\n\\n"
        f"📈 Доход в час: 🪙 {income['gold']} | 🪵 {income['wood']} | "
        f"🪨 {income['stone']} | 🌾 {income['food']}\\n\\n"
        "Нажми на здание, чтобы улучшить его."
    )
    try:
        await callback.message.edit_caption(caption=text)
    except Exception:
        await callback.message.answer(text)


# ==================================================
# 10. РЕСУРСЫ
# ==================================================

@dp.message(lambda m: m.text == "💰 Ресурсы")
async def show_resources(message: Message):

    uid = message.from_user.id

    collect_income(uid)

    p = get_player(uid)

    if not p:
        await message.answer("Сначала напиши /start")
        return

    await message.answer(
        "💰 СОКРОВИЩНИЦА ИМПЕРИИ\n\n"
        f"🪙 Золото: {p['gold']}\n"
        f"🪵 Дерево: {p['wood']}\n"
        f"🪨 Камень: {p['stone']}\n"
        f"🌾 Еда: {p['food']}"
    )


# ==================================================
# 11. ЭКОНОМИКА
# ==================================================

@dp.message(lambda m: m.text == "📊 Экономика")
async def show_economy(message: Message):

    uid = message.from_user.id

    collect_income(uid)

    p = get_player(uid)

    if not p:
        await message.answer("Сначала напиши /start")
        return

    income = calculate_income(p)

    consumption = (
        p["population"] * FOOD_PER_PERSON
    )

    await message.answer(
        "📊 ЭКОНОМИКА ГОРОДА\n\n"

        "📈 ДОХОД ЗА ЧАС:\n"
        f"🪙 Налоги: +{income['gold']}\n"
        f"🪵 Дерево: +{income['wood']}\n"
        f"🪨 Камень: +{income['stone']}\n"
        f"🌾 Производство еды: +{income['food']}\n\n"

        "📉 РАСХОД ЗА ЧАС:\n"
        f"🍞 Потребление еды: -{consumption:.1f}\n\n"

        f"👥 Население: {p['population']}\n"
        f"🏠 Вместимость: {city_capacity(p)}"
    )


# ==================================================
# 12. СТРОИТЕЛЬСТВО
# ==================================================

@dp.message(lambda m: m.text == "⚒ Строительство")
async def show_buildings(message: Message):

    await message.answer(
        "⚒ СТРОИТЕЛЬСТВО ИМПЕРИИ\n\n"

        "🌾 Ферма: 100 золота, 50 дерева.\n"
        "🪓 Лесопилка: 150 золота, 100 дерева.\n"
        "⛏ Каменоломня: 200 золота, 100 дерева, 50 камня.\n"
        "🏠 Жилой дом: 250 золота, 150 дерева, 50 камня.\n"
        "🏰 Казармы: 500 золота, 300 дерева, 100 камня.\n\n"

        "Выбери здание:",
        reply_markup=building_menu()
    )


@dp.message(lambda m: m.text in BUILDINGS)
async def build_building(message: Message):

    uid = message.from_user.id

    collect_income(uid)

    building = BUILDINGS[message.text]
    column = building["column"]

    with sqlite3.connect(DB_NAME) as db:

        p = db.execute("""
            SELECT gold, wood, stone
            FROM players
            WHERE user_id = ?
        """, (uid,)).fetchone()

        if not p:
            await message.answer("Сначала напиши /start")
            return

        if (
            p[0] < building["gold"]
            or p[1] < building["wood"]
            or p[2] < building["stone"]
        ):
            await message.answer(
                "❌ Недостаточно ресурсов!"
            )
            return

        db.execute(f"""
            UPDATE players
            SET gold = gold - ?,
                wood = wood - ?,
                stone = stone - ?,
                {column} = {column} + 1
            WHERE user_id = ?
        """, (
            building["gold"],
            building["wood"],
            building["stone"],
            uid
        ))

    updated = get_player(uid)

    await message.answer(
        "🎉 ЗДАНИЕ ПОСТРОЕНО!\n\n"
        f"🏗 {message.text}\n"
        f"⭐ Новый уровень: {updated[column]}\n\n"
        f"🪙 Золото: {updated['gold']}\n"
        f"🪵 Дерево: {updated['wood']}\n"
        f"🪨 Камень: {updated['stone']}"
    )


# ==================================================
# 13. АРМИЯ
# ==================================================

@dp.message(lambda m: m.text == "⚔️ Армия")
async def show_army(message: Message):

    uid = message.from_user.id

    collect_income(uid)

    p = get_player(uid)
    army = get_army(uid)
    hero = get_hero(uid)

    if not p or not army or not hero:
        await message.answer("Сначала напиши /start")
        return

    power = (
        army["swordsmen"] * UNIT_POWER["swordsmen"]
        + army["archers"] * UNIT_POWER["archers"]
        + army["cavalry"] * UNIT_POWER["cavalry"]
    )

    await message.answer(
        "⚔️ АРМИЯ ИМПЕРИИ\n\n"

        f"⚔️ Мечники: {army['swordsmen']}\n"
        f"🏹 Лучники: {army['archers']}\n"
        f"🐎 Кавалерия: {army['cavalry']}\n\n"

        f"💪 Сила войск: {power}\n"
        f"🦸 Герой: {hero['name']}\n"
        f"⭐ Уровень героя: {hero['level']}\n"
        f"🏰 Уровень казарм: {p['barracks_level']}\n\n"

        "Выбери действие:",
        reply_markup=army_menu()
    )


# ==================================================
# 14. НАЙМ ВОЙСК
# ==================================================

@dp.message(lambda m: m.text in UNIT_BUTTONS)
async def recruit_units(message: Message):

    uid = message.from_user.id

    collect_income(uid)

    p = get_player(uid)

    if not p:
        await message.answer("Сначала напиши /start")
        return

    if p["barracks_level"] < 1:
        await message.answer(
            "❌ Сначала построй казармы!\n\n"
            "Открой раздел «⚒ Строительство»."
        )
        return

    unit_name = UNIT_BUTTONS[message.text]
    unit = UNITS[unit_name]

    with sqlite3.connect(DB_NAME) as db:

        resources = db.execute("""
            SELECT gold, food, wood
            FROM players
            WHERE user_id = ?
        """, (uid,)).fetchone()

        if (
            resources[0] < unit["gold"]
            or resources[1] < unit["food"]
            or resources[2] < unit["wood"]
        ):
            await message.answer(
                "❌ Недостаточно ресурсов для найма!"
            )
            return

        db.execute("""
            UPDATE players
            SET gold = gold - ?,
                food = food - ?,
                wood = wood - ?
            WHERE user_id = ?
        """, (
            unit["gold"],
            unit["food"],
            unit["wood"],
            uid
        ))

        db.execute(f"""
            UPDATE armies
            SET {unit['column']} = {unit['column']} + ?
            WHERE user_id = ?
        """, (
            unit["amount"],
            uid
        ))

    await message.answer(
        "🎖 НОВЫЕ ВОЙСКА НАНЯТЫ!\n\n"
        f"{unit_name}\n"
        f"Количество: {unit['amount']}\n\n"
        "Армия становится сильнее!"
    )


# ==================================================
# 15. ГЕРОЙ
# ==================================================

@dp.message(lambda m: m.text == "🦸 Мой герой")
async def show_hero(message: Message):

    uid = message.from_user.id

    hero = get_hero(uid)

    if not hero:
        await message.answer("Сначала напиши /start")
        return

    bonus = hero["level"] * 5

    await message.answer(
        "🦸 ГЕРОЙ ИМПЕРИИ\n\n"
        f"👑 Имя: {hero['name']}\n"
        f"⭐ Уровень: {hero['level']}\n"
        f"✨ Опыт: {hero['experience']}\n"
        f"💪 Бонус армии: +{bonus}%\n\n"
        "Герой усиливает твою армию.",
        reply_markup=hero_menu()
    )


@dp.message(lambda m: m.text == "⭐ Улучшить героя")
async def upgrade_hero(message: Message):

    uid = message.from_user.id

    hero = get_hero(uid)
    p = get_player(uid)

    if not hero or not p:
        await message.answer("Сначала напиши /start")
        return

    cost = hero["level"] * 300
    if p["gold"] < cost:
        await message.answer(
            f"❌ Нужно {cost} золота для улучшения героя."
        )
        return

    with sqlite3.connect(DB_NAME) as db:

        db.execute("""
            UPDATE players
            SET gold = gold - ?
            WHERE user_id = ?
        """, (cost, uid))

        db.execute("""
            UPDATE heroes
            SET level = level + 1
            WHERE user_id = ?
        """, (uid,))

    hero = get_hero(uid)

    await message.answer(
        "⭐ ГЕРОЙ УЛУЧШЕН!\n\n"
        f"👑 {hero['name']}\n"
        f"Новый уровень: {hero['level']}\n"
        f"Бонус армии: +{hero['level'] * 5}%"
    )


# ==================================================
# 16. ПОИСК ПРОТИВНИКОВ
# ==================================================

@dp.message(lambda m: m.text == "🗺 Противники")
async def show_opponents(message: Message):

    uid = message.from_user.id

    with sqlite3.connect(DB_NAME) as db:

        db.row_factory = sqlite3.Row

        opponents = db.execute("""
            SELECT user_id, username, city_name, population
            FROM players
            WHERE user_id != ?
            ORDER BY population DESC
            LIMIT 10
        """, (uid,)).fetchall()

    if not opponents:

        await message.answer(
            "🌍 Пока нет других правителей.\n\n"
            "Пригласи друзей начать игру!"
        )
        return

    text = "🗺 СПИСОК ПРОТИВНИКОВ\n\n"

    for opponent in opponents:

        text += (
            f"🏰 {opponent['city_name']}\n"
            f"👤 {opponent['username']}\n"
            f"👥 Население: {opponent['population']}\n"
            f"🆔 ID: {opponent['user_id']}\n"
            f"/attack {opponent['user_id']} — бой за город\n"
            f"/duel {opponent['user_id']} — быстрая дуэль\n\n"
        )

    text += "Используй команду атаки, чтобы начать бой."

    await message.answer(text)


# ==================================================
# 17. СИЛА АРМИИ И ПОТЕРИ
# ==================================================

def army_power(army, hero):

    base = (
        army["swordsmen"] * UNIT_POWER["swordsmen"]
        + army["archers"] * UNIT_POWER["archers"]
        + army["cavalry"] * UNIT_POWER["cavalry"]
    )

    bonus = 1 + hero["level"] * 0.05
    # Экипировка добавляет плоский бонус к итоговой силе армии.
    user_id = army.get("user_id")
    equipment_bonus = 0
    if user_id is not None:
        with sqlite3.connect(DB_NAME) as db:
            row = db.execute(
                "SELECT COALESCE(SUM(attack_bonus), 0) FROM equipment WHERE user_id=?",
                (user_id,)
            ).fetchone()
            equipment_bonus = row[0] if row else 0

    return int(base * bonus) + equipment_bonus


def apply_losses(army, percentage):

    result = {}

    for unit in [
        "swordsmen",
        "archers",
        "cavalry"
    ]:

        count = army[unit]

        losses = min(
            count,
            math.ceil(count * percentage)
        )

        result[unit] = count - losses

    return result


# ==================================================
# 18. PvP СРАЖЕНИЯ
# ==================================================

@dp.message(Command("attack"))
async def attack_player(message: Message):

    attacker_id = message.from_user.id

    parts = (message.text or "").split()

    if len(parts) != 2 or not parts[1].isdigit():

        await message.answer(
            "⚔️ Использование:\n"
            "/attack ID_противника\n\n"
            "Сначала открой «🗺 Противники»."
        )
        return

    defender_id = int(parts[1])

    if attacker_id == defender_id:

        await message.answer(
            "❌ Нельзя атаковать собственный город!"
        )
        return

    attacker = get_player(attacker_id)
    defender = get_player(defender_id)

    if not attacker or not defender:

        await message.answer(
            "❌ Один из игроков не найден."
        )
        return

    if attacker["barracks_level"] < 1:

        await message.answer(
            "❌ Сначала построй казармы!"
        )
        return

    attacker_army = get_army(attacker_id)
    defender_army = get_army(defender_id)

    attacker_hero = get_hero(attacker_id)
    defender_hero = get_hero(defender_id)

    attacker_count = sum(
        attacker_army[k]
        for k in [
            "swordsmen",
            "archers",
            "cavalry"
        ]
    )

    if attacker_count == 0:

        await message.answer(
            "❌ Твоя армия пуста! Сначала найми войска."
        )
        return

    now = int(time.time())

    # Проверка защиты и перерыва между атаками
    with sqlite3.connect(DB_NAME) as db:

        cooldown = db.execute("""
            SELECT last_attack
            FROM attack_cooldowns
            WHERE user_id = ?
        """, (attacker_id,)).fetchone()

        if cooldown and now - cooldown[0] < ATTACK_COOLDOWN:

            remaining = ATTACK_COOLDOWN - (
                now - cooldown[0]
            )

            await message.answer(
                f"⏳ Следующая атака доступна "
                f"через {remaining // 60 + 1} минут."
            )
            return

        shield = db.execute("""
            SELECT shield_until
            FROM defense_shields
            WHERE user_id = ?
        """, (defender_id,)).fetchone()

        if shield and shield[0] > now:

            remaining = shield[0] - now

            await message.answer(
                "🛡 Город противника защищён!\n\n"
                f"Щит действует ещё {remaining // 60 + 1} минут."
            )
            return

    # Начисляем ресурсы перед боем
    collect_income(attacker_id)
    collect_income(defender_id)

    attacker_army = get_army(attacker_id)
    defender_army = get_army(defender_id)

    attacker_hero = get_hero(attacker_id)
    defender_hero = get_hero(defender_id)

    attack_power = army_power(
        attacker_army,
        attacker_hero
    )

    defense_power = army_power(
        defender_army,
        defender_hero
    )

    attack_roll = attack_power * random.uniform(0.9, 1.1)

    defense_roll = defense_power * random.uniform(0.9, 1.1)

    attacker_wins = attack_roll > defense_roll

    if attacker_wins:

        winner_id = attacker_id

        attacker_losses = apply_losses(
            attacker_army,
            0.20
        )

        defender_losses = apply_losses(
            defender_army,
            0.60
        )

    else:

        winner_id = defender_id

        attacker_losses = apply_losses(
            attacker_army,
            0.60
        )

        defender_losses = apply_losses(
            defender_army,
            0.20
        )

    reward_gold = 0
    reward_wood = 0
    reward_stone = 0
    reward_food = 0

    with sqlite3.connect(DB_NAME) as db:

        # Запоминаем время атаки
        db.execute("""
            INSERT INTO attack_cooldowns
            (user_id, last_attack)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET last_attack = excluded.last_attack
        """, (attacker_id, now))

        # Обновляем армию нападающего
        db.execute("""
            UPDATE armies
            SET swordsmen = ?,
                archers = ?,
                cavalry = ?
            WHERE user_id = ?
        """, (
            attacker_losses["swordsmen"],
            attacker_losses["archers"],
            attacker_losses["cavalry"],
            attacker_id
        ))

        # Обновляем армию защитника
        db.execute("""
            UPDATE armies
            SET swordsmen = ?,
                archers = ?,
                cavalry = ?
            WHERE user_id = ?
        """, (
            defender_losses["swordsmen"],
            defender_losses["archers"],
            defender_losses["cavalry"],
            defender_id
        ))

        # Добыча победителя
        if attacker_wins:

            resources = db.execute("""
                SELECT gold, wood, stone, food
                FROM players
                WHERE user_id = ?
            """, (defender_id,)).fetchone()

            reward_gold = int(resources[0] * 0.10)
            reward_wood = int(resources[1] * 0.10)
            reward_stone = int(resources[2] * 0.10)
            reward_food = int(resources[3] * 0.10)

            db.execute("""
                UPDATE players
                SET gold = gold - ?,
                    wood = wood - ?,
                    stone = stone - ?,
                    food = food - ?
                WHERE user_id = ?
            """, (
                reward_gold,
                reward_wood,
                reward_stone,
                reward_food,
                defender_id
            ))

            db.execute("""
                UPDATE players
                SET gold = gold + ?,
                    wood = wood + ?,
                    stone = stone + ?,
                    food = food + ?
                WHERE user_id = ?
            """, (
                reward_gold,
                reward_wood,
                reward_stone,
                reward_food,
                attacker_id
            ))

        # Защитный щит
        db.execute("""
            INSERT INTO defense_shields
            (user_id, shield_until)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET shield_until = excluded.shield_until
        """, (
            defender_id,
            now + DEFENSE_SHIELD
        ))

        # История боя
        db.execute("""
            INSERT INTO battle_log (
                attacker_id,
                defender_id,
                winner_id,
                attacker_power,
                defender_power,
                reward_gold,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            attacker_id,
            defender_id,
            winner_id,
            attack_power,
            defense_power,
            reward_gold,
            now
        ))

        # Опыт героям
        db.execute("""
            UPDATE heroes
            SET experience = experience + 50
            WHERE user_id = ?
        """, (attacker_id,))

        if not attacker_wins:

            db.execute("""
                UPDATE heroes
                SET experience = experience + 50
                WHERE user_id = ?
            """, (defender_id,))

        # Автоматическое повышение уровня
        for player_id in [attacker_id, defender_id]:

            hero = db.execute("""
                SELECT level, experience
                FROM heroes
                WHERE user_id = ?
            """, (player_id,)).fetchone()

            level = hero[0]
            experience = hero[1]

            while experience >= level * 100:

                experience -= level * 100
                level += 1

            db.execute("""
                UPDATE heroes
                SET level = ?,
                    experience = ?
                WHERE user_id = ?
            """, (
                level,
                experience,
                player_id
            ))
    result = (
        "🏆 ПОБЕДА!\n"
        if attacker_wins
        else "🛡 ПОРАЖЕНИЕ! Противник защитил город.\n"
    )

    reward_text = ""

    if attacker_wins:

        reward_text = (
            "\n🎁 ДОБЫЧА:\n"
            f"🪙 Золото: {reward_gold}\n"
            f"🪵 Дерево: {reward_wood}\n"
            f"🪨 Камень: {reward_stone}\n"
            f"🌾 Еда: {reward_food}\n"
        )

    await message.answer(
        "⚔️ РЕЗУЛЬТАТ СРАЖЕНИЯ\n\n"

        + result

        + "\n"
        + f"💪 Сила атаки: {attack_power}\n"
        + f"🛡 Сила защиты: {defense_power}\n\n"

        + "📉 ТВОЯ АРМИЯ ПОСЛЕ БОЯ:\n"
        + f"⚔️ Мечники: {attacker_losses['swordsmen']}\n"
        + f"🏹 Лучники: {attacker_losses['archers']}\n"
        + f"🐎 Кавалерия: {attacker_losses['cavalry']}\n"

        + reward_text
    )

    # Уведомление защитника
    try:

        await bot.send_message(
            defender_id,
            "⚔️ НА ТВОЙ ГОРОД НАПАЛИ!\n\n"
            f"ID нападающего: {attacker_id}\n"
            f"Результат: {'Нападающий победил' if attacker_wins else 'Ты отстоял город'}\n\n"
            "🛡 Защитный щит действует 30 минут."
        )

    except Exception:
        pass


# ==================================================
# 19. ИСТОРИЯ БОЁВ
# ==================================================

@dp.message(lambda m: m.text == "📜 История боёв")
async def battle_history(message: Message):

    uid = message.from_user.id

    with sqlite3.connect(DB_NAME) as db:

        db.row_factory = sqlite3.Row

        battles = db.execute("""
            SELECT *
            FROM battle_log
            WHERE attacker_id = ?
               OR defender_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (uid, uid)).fetchall()

    if not battles:

        await message.answer(
            "📜 История сражений пока пуста."
        )
        return

    text = "📜 ПОСЛЕДНИЕ СРАЖЕНИЯ\n\n"

    for battle in battles:

        result = (
            "🏆 Победа"
            if battle["winner_id"] == uid
            else "❌ Поражение"
        )

        text += (
            f"{result}\n"
            f"⚔️ Нападающий: {battle['attacker_id']}\n"
            f"🛡 Защитник: {battle['defender_id']}\n"
            f"💪 Сила атаки: {battle['attacker_power']}\n"
            f"🛡 Сила защиты: {battle['defender_power']}\n"
            f"🪙 Добыча: {battle['reward_gold']}\n\n"
        )

    await message.answer(text)


# ==================================================
# 20. ПРОФИЛЬ
# ==================================================

@dp.message(lambda m: m.text == "👤 Мой профиль")
async def show_profile(message: Message):

    uid = message.from_user.id

    collect_income(uid)

    p = get_player(uid)
    hero = get_hero(uid)
    army = get_army(uid)

    if not p or not hero or not army:

        await message.answer("Сначала напиши /start")
        return

    total_units = sum(
        army[k]
        for k in [
            "swordsmen",
            "archers",
            "cavalry"
        ]
    )

    await message.answer(
        "👤 ПРОФИЛЬ ПРАВИТЕЛЯ\n\n"

        f"🏰 Город: {p['city_name']}\n"
        f"👥 Население: {p['population']}\n"
        f"💰 Золото: {p['gold']}\n"
        f"⚔️ Войск: {total_units}\n\n"

        f"🦸 Герой: {hero['name']}\n"
        f"⭐ Уровень героя: {hero['level']}\n"
        f"✨ Опыт: {hero['experience']}"
    )


# ==================================================
# 21. НАЗАД И БУДУЩИЕ РАЗДЕЛЫ
# ==================================================

@dp.message(lambda m: m.text == "⬅️ Назад")
async def go_back(message: Message):

    await message.answer(
        "Главное меню империи 👑",
        reply_markup=main_menu()
    )


@dp.message(lambda m: m.text == "🛡 Кланы")
async def clans_placeholder(message: Message):
    await message.answer("🛡 Система альянсов будет добавлена следующим этапом.")



# ==================================================
# 21A. ПЕРЕМЕЩЕНИЕ АРМИИ И ЗАХВАТ ЧУЖИХ ПРОВИНЦИЙ
# ==================================================

@dp.message(lambda m: m.text == "📍 Положение армии")
@dp.message(Command("armypos"))
async def show_army_position(message: Message):
    uid = message.from_user.id
    with sqlite3.connect(DB_NAME) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("""
            SELECT ap.province_id, p.x, p.y, p.terrain, p.owner_id
            FROM army_positions ap
            JOIN provinces p ON p.id = ap.province_id
            WHERE ap.user_id = ?
        """, (uid,)).fetchone()

    if not row:
        await message.answer("Положение армии не найдено. Напиши /start.")
        return

    ownership = "твоя провинция" if row["owner_id"] == uid else "чужая территория"
    await message.answer(
        f"📍 АРМИЯ НАХОДИТСЯ В ПРОВИНЦИИ #{row['province_id']}\\n"
        f"Координаты: ({row['x']}, {row['y']})\\n"
        f"Местность: {row['terrain']}\\n"
        f"Статус: {ownership}\\n\\n"
        "Перемещение: /move ID_провинции\\n"
        "Атака чужой провинции: /war ID_провинции\\n"
        "Открой /map, чтобы посмотреть соседние земли."
    )


@dp.message(Command("move"))
async def move_army(message: Message):
    uid = message.from_user.id
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /move ID_провинции")
        return

    target_id = int(parts[1])
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("""
            SELECT ap.province_id, p.x, p.y
            FROM army_positions ap
            JOIN provinces p ON p.id = ap.province_id
            WHERE ap.user_id = ?
        """, (uid,)).fetchone()
        target = db.execute("""
            SELECT id, x, y, owner_id, terrain
            FROM provinces WHERE id = ?
        """, (target_id,)).fetchone()

        if not current or not target:
            db.rollback()
            await message.answer("Армия или указанная провинция не найдены. Напиши /start.")
            return
        if target[3] != uid:
            db.rollback()
            await message.answer(
                "Перемещаться обычной командой можно только по своим провинциям. "
                "Для атаки чужой земли используй /war ID."
            )
            return
        distance = max(abs(current[1] - target[1]), abs(current[2] - target[2]))
        if distance != 1:
            db.rollback()
            await message.answer("За один ход можно переместиться только в соседнюю провинцию.")
            return
        db.execute(
            "UPDATE army_positions SET province_id = ? WHERE user_id = ?",
            (target_id, uid)
        )
        db.commit()

    await message.answer(
        f"🚶 Армия переместилась в провинцию #{target_id} "
        f"({target[4]}).\\nНовое положение: /armypos"
    )


@dp.message(Command("war"))
async def attack_province(message: Message):
    uid = message.from_user.id
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /war ID_провинции. Сначала открой /map.")
        return

    target_id = int(parts[1])
    attacker = get_player(uid)
    attacker_army = get_army(uid)
    attacker_hero = get_hero(uid)
    if not attacker or not attacker_army or not attacker_hero:
        await message.answer("Сначала напиши /start.")
        return
    if attacker["barracks_level"] < 1:
        await message.answer("Сначала построй казармы!")
        return
    if sum(attacker_army[k] for k in ("swordsmen", "archers", "cavalry")) <= 0:
        await message.answer("Твоя армия пуста! Сначала найми войска.")
        return

    now = int(time.time())
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute("BEGIN IMMEDIATE")
        position = db.execute("""
            SELECT ap.province_id, p.x, p.y
            FROM army_positions ap
            JOIN provinces p ON p.id = ap.province_id
            WHERE ap.user_id = ?
        """, (uid,)).fetchone()
        target = db.execute("""
            SELECT id, x, y, owner_id, terrain
            FROM provinces WHERE id = ?
        """, (target_id,)).fetchone()

        if not position or not target:
            db.rollback()
            await message.answer("Армия или провинция не найдены.")
            return
        defender_id = target[3]
        if defender_id is None:
            db.rollback()
            await message.answer(
                "Эта провинция свободна. Захвати её командой /claim ID."
            )
            return
        if defender_id == uid:
            db.rollback()
            await message.answer("Это твоя провинция. Используй /move ID.")
            return
        if max(abs(position[1] - target[1]), abs(position[2] - target[2])) != 1:
            db.rollback()
            await message.answer(
                "Атаковать можно только провинцию, соседнюю с текущим положением армии."
            )
            return

        cooldown = db.execute(
            "SELECT last_attack FROM attack_cooldowns WHERE user_id = ?",
            (uid,)
        ).fetchone()
        if cooldown and now - cooldown[0] < ATTACK_COOLDOWN:
            remaining = ATTACK_COOLDOWN - (now - cooldown[0])
            db.rollback()
            await message.answer(
                f"⏳ Следующая атака доступна через {remaining // 60 + 1} минут."
            )
            return

        shield = db.execute(
            "SELECT shield_until FROM defense_shields WHERE user_id = ?",
            (defender_id,)
        ).fetchone()
        if shield and shield[0] > now:
            remaining = shield[0] - now
            db.rollback()
            await message.answer(
                f"🛡 Провинции этого игрока защищены ещё {remaining // 60 + 1} минут."
            )
            return

        db.commit()

    collect_income(uid)
    collect_income(defender_id)
    attacker_army = get_army(uid)
    defender_army = get_army(defender_id)
    attacker_hero = get_hero(uid)
    defender_hero = get_hero(defender_id)
    if not defender_army or not defender_hero:
        await message.answer("Не удалось получить оборону противника.")
        return

    attack_power = army_power(attacker_army, attacker_hero)
    defense_power = army_power(defender_army, defender_hero)
    attacker_wins = (
        attack_power * random.uniform(0.9, 1.1)
        > defense_power * random.uniform(0.9, 1.1)
    )
    attacker_losses = apply_losses(attacker_army, 0.20 if attacker_wins else 0.60)
    defender_losses = apply_losses(defender_army, 0.60 if attacker_wins else 0.20)

    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute("BEGIN IMMEDIATE")
        # Повторно проверяем владельца, чтобы не перезаписать результат параллельной атаки.
        current_owner = db.execute(
            "SELECT owner_id FROM provinces WHERE id = ?", (target_id,)
        ).fetchone()
        if not current_owner or current_owner[0] != defender_id:
            db.rollback()
            await message.answer("Провинция уже сменила владельца. Обнови /map.")
            return

        db.execute("""
            UPDATE armies SET swordsmen=?, archers=?, cavalry=? WHERE user_id=?
        """, (
            attacker_losses["swordsmen"], attacker_losses["archers"],
            attacker_losses["cavalry"], uid
        ))
        db.execute("""
            UPDATE armies SET swordsmen=?, archers=?, cavalry=? WHERE user_id=?
        """, (
            defender_losses["swordsmen"], defender_losses["archers"],
            defender_losses["cavalry"], defender_id
        ))
        db.execute("""
            INSERT INTO attack_cooldowns (user_id, last_attack) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_attack=excluded.last_attack
        """, (uid, now))
        db.execute("""
            INSERT INTO defense_shields (user_id, shield_until) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET shield_until=excluded.shield_until
        """, (defender_id, now + DEFENSE_SHIELD))

        if attacker_wins:
            db.execute(
                "UPDATE provinces SET owner_id=? WHERE id=? AND owner_id=?",
                (uid, target_id, defender_id)
            )
            db.execute(
                "UPDATE army_positions SET province_id=? WHERE user_id=?",
                (target_id, uid)
            )

        db.execute("""
            INSERT INTO battle_log (
                attacker_id, defender_id, winner_id, attacker_power,
                defender_power, reward_gold, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (uid, defender_id, uid if attacker_wins else defender_id,
              attack_power, defense_power, now))
        db.execute(
            "UPDATE heroes SET experience=experience+50 WHERE user_id=?",
            (uid,)
        )
        if not attacker_wins:
            db.execute(
                "UPDATE heroes SET experience=experience+50 WHERE user_id=?",
                (defender_id,)
            )
        for player_id in (uid, defender_id):
            hero_row = db.execute(
                "SELECT level, experience FROM heroes WHERE user_id=?",
                (player_id,)
            ).fetchone()
            if hero_row:
                level, experience = hero_row
                while experience >= level * 100:
                    experience -= level * 100
                    level += 1
                db.execute(
                    "UPDATE heroes SET level=?, experience=? WHERE user_id=?",
                    (level, experience, player_id)
                )
        db.commit()

    if attacker_wins:
        result = (
            f"🏆 ПОБЕДА! Провинция #{target_id} теперь твоя.\\n"
            "Армия заняла захваченную территорию."
        )
    else:
        result = (
            f"🛡 ПОРАЖЕНИЕ! Провинция #{target_id} осталась у противника.\\n"
            "Армия осталась на прежней позиции."
        )

    await message.answer(
        "⚔️ БИТВА ЗА ПРОВИНЦИЮ\\n\\n"
        + result
        + f"\\n\\n💪 Сила атаки: {attack_power}"
        + f"\\n🛡 Сила защиты: {defense_power}"
        + "\\n\\n📉 ТВОЯ АРМИЯ:"
        + f"\\n⚔️ Мечники: {attacker_losses['swordsmen']}"
        + f"\\n🏹 Лучники: {attacker_losses['archers']}"
        + f"\\n🐎 Кавалерия: {attacker_losses['cavalry']}"
        + "\\n\\nПоложение армии: /armypos"
    )
    try:
        await bot.send_message(
            defender_id,
            f"⚔️ На твою провинцию #{target_id} напали!\\n"
            f"Результат: {'Провинция захвачена' if attacker_wins else 'Ты отбил атаку'}."
        )
    except Exception:
        pass


@dp.message(lambda m: m.text == "🗺 Карта мира")
@dp.message(Command("map"))
async def show_world_map(message: Message):
    uid = message.from_user.id
    register_player(uid, message.from_user.username or "Игрок")
    with sqlite3.connect(DB_NAME) as db:
        db.row_factory = sqlite3.Row
        player = db.execute("SELECT province_id FROM players WHERE user_id=?", (uid,)).fetchone()
        if not player or player["province_id"] is None:
            await message.answer("Не удалось найти стартовую провинцию. Повтори /start.")
            return
        center = db.execute("SELECT x,y FROM provinces WHERE id=?", (player["province_id"],)).fetchone()
        cx, cy = center["x"], center["y"]
        x1, x2 = max(0,cx-MAP_RADIUS), min(WORLD_SIZE-1,cx+MAP_RADIUS)
        y1, y2 = max(0,cy-MAP_RADIUS), min(WORLD_SIZE-1,cy+MAP_RADIUS)
        rows = db.execute("SELECT id,x,y,terrain,resource,owner_id FROM provinces WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?", (x1,x2,y1,y2)).fetchall()
    grid={(p["x"],p["y"]):p for p in rows}
    lines=["🗺 ОБЩАЯ КАРТА ИМПЕРИИ",f"Твоя столица: провинция #{player['province_id']}",""]
    for y in range(y1,y2+1):
        cells=[]
        for x in range(x1,x2+1):
            p=grid.get((x,y))
            if not p: cells.append("⬛")
            elif p["owner_id"]==uid: cells.append("🏰" if p["id"]==player["province_id"] else "🟩")
            elif p["owner_id"] is not None: cells.append("🟥")
            else: cells.append(province_symbol(p["terrain"]))
        lines.append("".join(cells))
    lines += ["", "🏰 столица · 🟩 твоя земля · 🟥 чужая · 🌲 лес · 🌾 равнина · ⛰ горы · 🏜 пустошь · 🌊 побережье", "", "Ближайшие свободные провинции:"]
    # Показываем доступные земли на границе всех владений, а не только вокруг столицы.
    with sqlite3.connect(DB_NAME) as db:
        db.row_factory = sqlite3.Row
        frontier = db.execute("""
            SELECT n.id,n.x,n.y,n.terrain,n.resource
            FROM provinces n
            WHERE n.owner_id IS NULL
              AND EXISTS (
                SELECT 1 FROM provinces owned
                WHERE owned.owner_id = ?
                  AND ABS(owned.x-n.x) <= 1
                  AND ABS(owned.y-n.y) <= 1
                  AND (owned.x != n.x OR owned.y != n.y)
              )
            ORDER BY n.id LIMIT 5
        """, (uid,)).fetchall()
    for p in frontier:
        lines.append(f"#{p['id']} — {p['terrain']}, ресурс: {p['resource']} (/claim {p['id']})")
    if not frontier: lines.append("Свободных соседних провинций рядом нет.")
    with sqlite3.connect(DB_NAME) as db:
        db.row_factory = sqlite3.Row
        enemies = db.execute("""
            SELECT n.id, n.terrain, p.username, p.city_name
            FROM provinces n
            JOIN players p ON p.user_id = n.owner_id
            WHERE n.owner_id IS NOT NULL AND n.owner_id != ?
              AND EXISTS (
                SELECT 1 FROM provinces owned
                WHERE owned.owner_id = ?
                  AND ABS(owned.x-n.x) <= 1
                  AND ABS(owned.y-n.y) <= 1
                  AND (owned.x != n.x OR owned.y != n.y)
              )
            ORDER BY n.id LIMIT 5
        """, (uid, uid)).fetchall()
    lines += ["", "Чужие соседние провинции:"]
    for enemy in enemies:
        lines.append(
            f"#{enemy['id']} — {enemy['terrain']}, "
            f"{enemy['city_name']} ({enemy['username']}) (/war {enemy['id']})"
        )
    if not enemies:
        lines.append("Соседних провинций других игроков пока нет.")
    lines += ["", f"Цена захвата: {CLAIM_COST['gold']} золота, {CLAIM_COST['wood']} дерева, {CLAIM_COST['stone']} камня.", "Свободные соседние земли: /claim ID.", "Чужие соседние провинции: /war ID.", "Перемещение по своим землям: /move ID.", "Положение армии: /armypos."]
    await message.answer("\n".join(lines))


@dp.message(Command("claim"))
async def claim_province(message: Message):
    uid=message.from_user.id
    parts=(message.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit():
        await message.answer("Использование: /claim ID. Сначала открой /map.")
        return
    pid=int(parts[1])
    if pid<1 or pid>WORLD_SIZE*WORLD_SIZE:
        await message.answer("Такой провинции на карте нет.")
        return
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        db.execute("BEGIN IMMEDIATE")
        player=db.execute("SELECT gold,wood,stone,province_id FROM players WHERE user_id=?",(uid,)).fetchone()
        target=db.execute("SELECT id,x,y,terrain,resource,owner_id FROM provinces WHERE id=?",(pid,)).fetchone()
        if not player or player[3] is None or not target:
            db.rollback(); await message.answer("Игрок или провинция не найдены. Напиши /start."); return
        if target[5] is not None:
            db.rollback(); await message.answer("Эта провинция уже занята."); return
        adjacent=db.execute("""
            SELECT 1 FROM provinces owned
            WHERE owned.owner_id = ?
              AND ABS(owned.x-?) <= 1 AND ABS(owned.y-?) <= 1
              AND (owned.x != ? OR owned.y != ?)
            LIMIT 1
        """,(uid,target[1],target[2],target[1],target[2])).fetchone()
        if not adjacent:
            db.rollback(); await message.answer("Можно захватывать только земли, соседние с твоими владениями. Открой /map."); return
        if player[0]<CLAIM_COST["gold"] or player[1]<CLAIM_COST["wood"] or player[2]<CLAIM_COST["stone"]:
            db.rollback(); await message.answer(f"Недостаточно ресурсов. Нужно {CLAIM_COST['gold']} золота, {CLAIM_COST['wood']} дерева и {CLAIM_COST['stone']} камня."); return
        changed=db.execute("UPDATE provinces SET owner_id=? WHERE id=? AND owner_id IS NULL",(uid,pid))
        if changed.rowcount!=1:
            db.rollback(); await message.answer("Провинцию уже занял другой игрок. Обнови /map."); return
        db.execute("UPDATE players SET gold=gold-?,wood=wood-?,stone=stone-? WHERE user_id=?",(CLAIM_COST["gold"],CLAIM_COST["wood"],CLAIM_COST["stone"],uid))
        db.commit()
    await message.answer(f"🏴 Провинция #{pid} присоединена!\nМестность: {target[3]}\nРесурс: {target[4]}\nОткрой /map, чтобы увидеть свои земли.")


# ==================================================
# 21B. НОВЫЕ СИСТЕМЫ: НАГРАДЫ, PvP, ПОДЗЕМЕЛЬЯ, СНАРЯЖЕНИЕ, АУКЦИОН
# ==================================================

ITEMS = {
    "Ржавый меч": {"slot": "weapon", "bonus": 5, "value": 100},
    "Стальной меч": {"slot": "weapon", "bonus": 15, "value": 350},
    "Клинок дракона": {"slot": "weapon", "bonus": 30, "value": 900},
    "Кожаная броня": {"slot": "armor", "bonus": 5, "value": 120},
    "Доспех стража": {"slot": "armor", "bonus": 15, "value": 400},
    "Доспех дракона": {"slot": "armor", "bonus": 30, "value": 1000},
}


def add_item(user_id, item_name, quantity=1):
    with sqlite3.connect(DB_NAME) as db:
        db.execute("""
            INSERT INTO inventory(user_id,item_name,quantity) VALUES(?,?,?)
            ON CONFLICT(user_id,item_name) DO UPDATE SET quantity=quantity+excluded.quantity
        """, (user_id, item_name, quantity))


def take_item(user_id, item_name, quantity=1):
    with sqlite3.connect(DB_NAME) as db:
        row = db.execute(
            "SELECT quantity FROM inventory WHERE user_id=? AND item_name=?",
            (user_id, item_name)
        ).fetchone()
        if not row or row[0] < quantity:
            return False
        if row[0] == quantity:
            db.execute("DELETE FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
        else:
            db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_name=?",
                       (quantity, user_id, item_name))
        return True


@dp.message(lambda m: m.text == "🎁 Ежедневная награда")
@dp.message(Command("daily"))
async def daily_reward(message: Message):
    uid = message.from_user.id
    register_player(uid, message.from_user.username or "Игрок")
    now = int(time.time())
    with sqlite3.connect(DB_NAME) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT last_claim,streak FROM daily_rewards WHERE user_id=?", (uid,)).fetchone()
        last_claim, streak = row if row else (0, 0)
        if now - last_claim < 86400:
            remaining = 86400 - (now - last_claim)
            db.rollback()
            await message.answer(f"🎁 Награда уже получена. Возвращайся через {remaining // 3600} ч. {(remaining % 3600) // 60} мин.")
            return
        streak = streak + 1 if now - last_claim < 172800 else 1
        gold = 250 + min(streak, 7) * 50
        db.execute("""
            INSERT INTO daily_rewards(user_id,last_claim,streak) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET last_claim=excluded.last_claim,streak=excluded.streak
        """, (uid, now, streak))
        db.execute("UPDATE players SET gold=gold+?,food=food+100 WHERE user_id=?", (gold, uid))
        db.commit()
    await message.answer(f"🎉 Ежедневная награда получена!\n🪙 Золото: +{gold}\n🍖 Еда: +100\n🔥 Серия входов: {streak} дн.")


@dp.message(lambda m: m.text == "🐉 Подземелье")
@dp.message(Command("dungeon"))
async def run_dungeon(message: Message):
    uid = message.from_user.id
    register_player(uid, message.from_user.username or "Игрок")
    hero = get_hero(uid)
    army = get_army(uid)
    if not hero or not army or sum(army[k] for k in ("swordsmen", "archers", "cavalry")) <= 0:
        await message.answer("Для похода в подземелье нужна армия. Найми войска в разделе ⚔️ Армия.")
        return
    now = int(time.time())
    with sqlite3.connect(DB_NAME) as db:
        row = db.execute("SELECT last_run FROM dungeon_cooldowns WHERE user_id=?", (uid,)).fetchone()
        if row and now - row[0] < 3600:
            mins = (3600 - (now - row[0]) + 59) // 60
            await message.answer(f"⏳ Подземелье восстановится через {mins} мин.")
            return
        db.execute("INSERT INTO dungeon_cooldowns(user_id,last_run) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_run=excluded.last_run", (uid, now))
    power = army_power(army, hero)
    enemy_power = random.randint(60, 180) + hero["level"] * 12
    won = power >= enemy_power or random.random() < min(0.25, power / max(1, enemy_power) * 0.15)
    if won:
        gold = random.randint(180, 400) + hero["level"] * 20
        xp = random.randint(25, 60)
        item = random.choice(list(ITEMS)) if random.random() < 0.45 else None
        with sqlite3.connect(DB_NAME) as db:
            db.execute("UPDATE players SET gold=gold+? WHERE user_id=?", (gold, uid))
            db.execute("UPDATE heroes SET experience=experience+? WHERE user_id=?", (xp, uid))
            if item:
                db.execute("INSERT INTO inventory(user_id,item_name,quantity) VALUES(?,?,1) ON CONFLICT(user_id,item_name) DO UPDATE SET quantity=quantity+1", (uid, item))
        reward = f"🪙 Золото: +{gold}\n✨ Опыт героя: +{xp}"
        if item:
            reward += f"\n🎒 Найден предмет: {item}"
        await message.answer(f"🐉 ПОБЕДА В ПОДЗЕМЕЛЬЕ!\nСила героя и армии: {power}\nСила босса: {enemy_power}\n\n{reward}\n\nСледующий поход — через час.")
    else:
        await message.answer(f"💀 Отряд отступил!\nТвоя сила: {power}\nСила монстра: {enemy_power}\nПопробуй улучшить армию и экипировку. Повторный поход через час.")


@dp.message(lambda m: m.text == "🎒 Инвентарь")
@dp.message(Command("inventory"))
async def show_inventory(message: Message):
    uid = message.from_user.id
    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("SELECT item_name,quantity FROM inventory WHERE user_id=? ORDER BY item_name", (uid,)).fetchall()
        gear = db.execute("SELECT slot,item_name,attack_bonus FROM equipment WHERE user_id=?", (uid,)).fetchall()
    lines = ["🎒 ТВОЙ ИНВЕНТАРЬ", ""]
    if rows:
        lines.extend(f"• {name} ×{qty}" for name, qty in rows)
    else:
        lines.append("Пока пусто. Добывай предметы в /dungeon.")
    lines += ["", "🛡 ЭКИПИРОВКА:"]
    if gear:
        lines.extend(f"• {slot}: {name} (+{bonus} к силе)" for slot, name, bonus in gear)
    else:
        lines.append("Ничего не экипировано.")
    lines += ["", "Надеть предмет: /equip Название предмета"]
    await message.answer("\n".join(lines))


@dp.message(Command("equip"))
async def equip_item(message: Message):
    uid = message.from_user.id
    item_name = (message.text or "").partition(" ")[2].strip()
    if item_name not in ITEMS:
        await message.answer("Укажи точное название предмета. Доступные: " + ", ".join(ITEMS))
        return
    if not take_item(uid, item_name):
        await message.answer("Этого предмета нет в инвентаре. Проверь /inventory.")
        return
    item = ITEMS[item_name]
    with sqlite3.connect(DB_NAME) as db:
        old = db.execute("SELECT item_name FROM equipment WHERE user_id=? AND slot=?", (uid, item["slot"])).fetchone()
        if old:
            db.execute("INSERT INTO inventory(user_id,item_name,quantity) VALUES(?,?,1) ON CONFLICT(user_id,item_name) DO UPDATE SET quantity=quantity+1", (uid, old[0]))
        db.execute("INSERT INTO equipment(user_id,slot,item_name,attack_bonus) VALUES(?,?,?,?) ON CONFLICT(user_id,slot) DO UPDATE SET item_name=excluded.item_name,attack_bonus=excluded.attack_bonus", (uid, item["slot"], item_name, item["bonus"]))
    await message.answer(f"✅ Экипировано: {item_name}\nБонус: +{item['bonus']} к силе армии. Старый предмет возвращён в инвентарь.")


@dp.message(lambda m: m.text == "🏷 Аукцион")
@dp.message(Command("auction"))
async def show_auction(message: Message):
    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("SELECT id,item_name,quantity,price,seller_id FROM auctions ORDER BY id DESC LIMIT 15").fetchall()
    if not rows:
        await message.answer("🏷 Аукцион пока пуст.\nВыставить предмет: /sell Название|количество|цена")
        return
    lines = ["🏷 АУКЦИОН ИМПЕРИИ", ""]
    lines.extend(f"#{r[0]} — {r[1]} ×{r[2]} | {r[3]} золота | продавец {r[4]}\n/buy {r[0]}" for r in rows)
    lines += ["", "Продать предмет: /sell Название|количество|цена"]
    await message.answer("\n".join(lines))


@dp.message(Command("sell"))
async def sell_item(message: Message):
    uid = message.from_user.id
    raw = (message.text or "").partition(" ")[2]
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Формат: /sell Название предмета|количество|цена")
        return
    name, qty, price = parts[0], int(parts[1]), int(parts[2])
    if qty < 1 or price < 1 or price > 10_000_000:
        await message.answer("Количество и цена должны быть положительными; цена не выше 10 000 000.")
        return
    if name not in ITEMS:
        await message.answer("На аукцион пока можно выставлять экипировку: " + ", ".join(ITEMS))
        return
    with sqlite3.connect(DB_NAME) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_name=?", (uid, name)).fetchone()
        if not row or row[0] < qty:
            db.rollback()
            await message.answer("Недостаточно предметов в инвентаре.")
            return
        if row[0] == qty:
            db.execute("DELETE FROM inventory WHERE user_id=? AND item_name=?", (uid, name))
        else:
            db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_name=?", (qty, uid, name))
        db.execute("INSERT INTO auctions(seller_id,item_name,quantity,price,created_at) VALUES(?,?,?,?,?)", (uid, name, qty, price, int(time.time())))
        db.commit()
    await message.answer(f"🏷 Лот выставлен: {name} ×{qty} за {price} золота. Посмотреть: /auction")


@dp.message(Command("buy"))
async def buy_auction_item(message: Message):
    uid = message.from_user.id
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /buy ID_лота. Список: /auction")
        return
    lot_id = int(parts[1])
    with sqlite3.connect(DB_NAME) as db:
        db.execute("BEGIN IMMEDIATE")
        lot = db.execute("SELECT seller_id,item_name,quantity,price FROM auctions WHERE id=?", (lot_id,)).fetchone()
        buyer = db.execute("SELECT gold FROM players WHERE user_id=?", (uid,)).fetchone()
        if not lot or not buyer:
            db.rollback(); await message.answer("Лот или игрок не найден. Напиши /start."); return
        seller_id, item_name, qty, price = lot
        if seller_id == uid:
            db.rollback(); await message.answer("Нельзя купить собственный лот."); return
        if buyer[0] < price:
            db.rollback(); await message.answer(f"Не хватает золота. Нужно {price}."); return
        seller = db.execute("SELECT user_id FROM players WHERE user_id=?", (seller_id,)).fetchone()
        if not seller:
            db.rollback(); await message.answer("Продавец больше не существует."); return
        db.execute("UPDATE players SET gold=gold-? WHERE user_id=?", (price, uid))
        db.execute("UPDATE players SET gold=gold+? WHERE user_id=?", (price, seller_id))
        db.execute("INSERT INTO inventory(user_id,item_name,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_name) DO UPDATE SET quantity=quantity+excluded.quantity", (uid, item_name, qty))
        db.execute("DELETE FROM auctions WHERE id=?", (lot_id,))
        db.commit()
    await message.answer(f"✅ Куплено: {item_name} ×{qty} за {price} золота. Предмет добавлен в /inventory.")
    try:
        await bot.send_message(seller_id, f"💰 Твой лот #{lot_id} продан за {price} золота!")
    except Exception:
        pass


@dp.message(Command("duel"))
async def duel_player(message: Message):
    """Отдельная PvP-дуэль без захвата провинций; территориальная война остаётся /war."""
    uid = message.from_user.id
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /duel ID_игрока. Список игроков: 🗺 Противники.")
        return
    opponent_id = int(parts[1])
    if opponent_id == uid:
        await message.answer("Нельзя вызвать на дуэль самого себя."); return
    register_player(uid, message.from_user.username or "Игрок")
    attacker, defender = get_player(uid), get_player(opponent_id)
    army_a, army_b = get_army(uid), get_army(opponent_id)
    hero_a, hero_b = get_hero(uid), get_hero(opponent_id)
    if not all((attacker, defender, army_a, army_b, hero_a, hero_b)):
        await message.answer("Игрок не найден или ещё не начал игру."); return
    if sum(army_a[k] for k in ("swordsmen", "archers", "cavalry")) == 0 or sum(army_b[k] for k in ("swordsmen", "archers", "cavalry")) == 0:
        await message.answer("Для дуэли у обоих игроков должна быть хотя бы одна боевая единица."); return
    now = int(time.time())
    with sqlite3.connect(DB_NAME) as db:
        row = db.execute("SELECT last_attack FROM attack_cooldowns WHERE user_id=?", (uid,)).fetchone()
        if row and now-row[0] < 900:
            await message.answer(f"⏳ Повторная дуэль будет доступна через {(900-(now-row[0])+59)//60} мин."); return
    power_a, power_b = army_power(army_a, hero_a), army_power(army_b, hero_b)
    # Небольшая случайность делает исход не полностью предсказуемым.
    score_a = int(power_a * random.uniform(0.85, 1.15))
    score_b = int(power_b * random.uniform(0.85, 1.15))
    won = score_a >= score_b
    winner, loser = (uid, opponent_id) if won else (opponent_id, uid)
    loser_gold = defender["gold"] if won else attacker["gold"]
    reward = min(200, max(0, int(loser_gold * 0.05)))
    with sqlite3.connect(DB_NAME) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO attack_cooldowns(user_id,last_attack) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_attack=excluded.last_attack", (uid, now))
        db.execute("UPDATE players SET gold=gold+? WHERE user_id=?", (reward, winner))
        db.execute("UPDATE players SET gold=MAX(0,gold-?) WHERE user_id=?", (reward, loser))
        db.execute("INSERT INTO battle_log(attacker_id,defender_id,winner_id,attacker_power,defender_power,reward_gold,created_at) VALUES(?,?,?,?,?,?,?)", (uid, opponent_id, winner, power_a, power_b, reward, now))
        db.commit()
    await message.answer(f"⚔️ ДУЭЛЬ ЗАВЕРШЕНА!\nТвоя сила: {power_a}\nСила соперника: {power_b}\n🏆 Победитель: {'ты' if won else 'соперник'}\n💰 Награда победителю: {reward} золота.\nПровинции и армия не теряются.")
    try:
        await bot.send_message(opponent_id, f"⚔️ Игрок {uid} вызвал тебя на дуэль.\nРезультат: {'ты победил' if not won else 'победил соперник'}. Награда: {reward} золота.")
    except Exception:
        pass


# ==================================================
# 22. ЗАПУСК
# ==================================================

async def main():

    init_db()

    print("====================================")
    print("👑 ИМПЕРИЯ ЗАПУЩЕНА!")
    print("🏰 Экономика активна.")
    print("⚔️ Военная система активна.")
    print("🦸 Герои активны.")
    print("🛡 PvP-сражения активны.")
    print("💾 SQLite подключена.")
    print("====================================")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())