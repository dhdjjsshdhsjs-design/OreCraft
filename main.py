import asyncio
import random
import os
import asyncpg
from datetime import datetime, timezone, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatMemberStatus
from aiogram.utils.deep_linking import create_start_link, decode_payload

# ================= ВСЕ ДАННЫЕ ЗДЕСЬ =================
BOT_TOKEN = "8578879179:AAFuUpoj7fltR_YPSDr--NC0MuF9FaNxFvA"

DATABASE_URL = "postgresql://neondb_owner:npg_DwGM4Qqr8ykZ@ep-proud-band-b4cu50dz-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

CHANNEL_USERNAME = "OreCraftNews"
CHAT_USERNAME = "OreCraftChat"
CHANNEL_LINK = f"https://t.me/{CHANNEL_USERNAME}"
CHAT_LINK = f"https://t.me/{CHAT_USERNAME}"

REFERRAL_REWARD = 500
SALE_TAX_PERCENT = 5   # налог с продажи, %

# ---- АДМИНЫ ----
ADMIN_IDS = [8786951363]   # ← свой ID

# ---- ЭНЕРГИЯ ----
MAX_ENERGY = 100
ENERGY_COST = 1
ENERGY_REGEN_SECONDS = 5

# ---- КИРКИ (цена, шансы, прочность) ----
PICKAXES = {
    "Каменная":     {"price": 0,     "chances": (60, 25, 10, 4,  1),  "durability": 50},
    "Железная":     {"price": 1500,  "chances": (40, 30, 18, 9,  3),  "durability": 150},
    "Золотая":      {"price": 3500,  "chances": (30, 28, 25, 12, 5),  "durability": 200},
    "Алмазная":     {"price": 5000,  "chances": (20, 25, 25, 20, 10), "durability": 350},
    "Незеритовая":  {"price": 15000, "chances": (12, 18, 25, 30, 15), "durability": 700},
    "Мифическая":   {"price": 50000, "chances": (5,  10, 20, 40, 25), "durability": 2000},
}
PICKAXE_ORDER = ["Каменная", "Железная", "Золотая", "Алмазная", "Незеритовая", "Мифическая"]

# ---- БУСТЫ ----
BOOSTS = {
    "energy":    {"name": "⚡ Энергия +50",     "price": 300,  "desc": "+50 энергии сразу"},
    "x2ore":     {"name": "💰 ×2 руда",         "price": 1000, "desc": "×2 руды за копание, 1 час"},
    "autoclick": {"name": "🤖 Авто-копание",    "price": 2500, "desc": "Копает каждые 3 сек, 30 мин"},
    "luck":      {"name": "🍀 ×2 удача",        "price": 5000, "desc": "×2 шанс редкой руды, 1 час"},
}

# ---- РУДЫ ----
ORES = {
    "coal":    {"name": "Угольная руда",   "img": "coal.png",    "price": 10,  "min": 5,   "max": 20},
    "iron":    {"name": "Железная руда",   "img": "iron.png",    "price": 30,  "min": 15,  "max": 60},
    "gold":    {"name": "Золотая руда",    "img": "gold.png",    "price": 80,  "min": 40,  "max": 160},
    "diamond": {"name": "Алмазная руда",   "img": "diamond.png", "price": 250, "min": 120, "max": 500},
    "emerald": {"name": "Изумрудная руда", "img": "emerald.png", "price": 500, "min": 250, "max": 1000},
}

PRICE_STEP = {"coal": 1, "iron": 3, "gold": 8, "diamond": 25, "emerald": 50}
PRICE_UPDATE_SECONDS = 60

MINE_COOLDOWN_SECONDS = 1
AD_EVERY_N_MINES = 10   # реклама каждые N копаний
# ====================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool: asyncpg.Pool = None

ICONS = {"coal": "🪨", "iron": "⚙️", "gold": "🟡", "diamond": "💎", "emerald": "💚"}


# ---------------- БАЗА ----------------
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance BIGINT DEFAULT 0,
                level INTEGER DEFAULT 1,
                pickaxe TEXT DEFAULT 'Каменная',
                pickaxe_durability INTEGER DEFAULT 50,
                energy INTEGER DEFAULT 100,
                last_energy_update TIMESTAMPTZ DEFAULT NOW(),
                last_mine_time TIMESTAMPTZ DEFAULT NOW(),
                mines_count INTEGER DEFAULT 0,
                total_mined INTEGER DEFAULT 0,
                total_sold INTEGER DEFAULT 0,
                total_earned BIGINT DEFAULT 0,
                best_ore TEXT DEFAULT NULL,
                ore_coal INTEGER DEFAULT 0,
                ore_iron INTEGER DEFAULT 0,
                ore_gold INTEGER DEFAULT 0,
                ore_diamond INTEGER DEFAULT 0,
                ore_emerald INTEGER DEFAULT 0,
                referrer_id BIGINT DEFAULT NULL,
                referrals_count INTEGER DEFAULT 0,
                boost_x2ore_until TIMESTAMPTZ DEFAULT NULL,
                boost_luck_until TIMESTAMPTZ DEFAULT NULL,
                autoclick_until TIMESTAMPTZ DEFAULT NULL,
                joined_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ore_key TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                last_update TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        for key, ore in ORES.items():
            await conn.execute("""
                INSERT INTO prices (ore_key, price) VALUES ($1, $2)
                ON CONFLICT (ore_key) DO NOTHING
            """, key, ore["price"])

        # дефолтный текст рекламы
        await conn.execute("""
            INSERT INTO settings (key, value) VALUES ('ad_text', '')
            ON CONFLICT (key) DO NOTHING
        """)

        print("✅ БД инициализирована")


async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


async def create_user(user_id: int, username: str, first_name: str, referrer_id: int = None):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name, referrer_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username, first_name, referrer_id)


async def add_referral_reward(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE users 
            SET balance = balance + $1, referrals_count = referrals_count + 1
            WHERE user_id = $2
        """, REFERRAL_REWARD, user_id)


async def get_setting(key: str):
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT value FROM settings WHERE key = $1", key)


async def set_setting(key: str, value: str):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2
        """, key, value)


# ---------------- ЦЕНЫ ----------------
async def get_prices():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT ore_key, price FROM prices")
    return {r["ore_key"]: r["price"] for r in rows}


async def update_prices():
    prices = await get_prices()
    async with db_pool.acquire() as conn:
        for key, ore in ORES.items():
            current = prices[key]
            step = PRICE_STEP[key]
            change = random.choice([-step, -step, 0, step, step])
            new_price = max(ore["min"], min(ore["max"], current + change))
            await conn.execute("""
                UPDATE prices SET price = $1, last_update = NOW() WHERE ore_key = $2
            """, new_price, key)


async def price_loop():
    while True:
        await asyncio.sleep(PRICE_UPDATE_SECONDS)
        try:
            await update_prices()
        except Exception as e:
            print(f"❌ Ошибка обновления цен: {e}")


# ---------------- ЭНЕРГИЯ ----------------
async def refresh_energy(user_id: int):
    user = await get_user(user_id)
    if not user:
        return 0

    last = user["last_energy_update"]
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    elapsed = int((now - last).total_seconds())
    gained = elapsed // ENERGY_REGEN_SECONDS

    energy = user["energy"]
    was_full = energy >= MAX_ENERGY

    if gained > 0:
        energy = min(MAX_ENERGY, energy + gained)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE users SET energy = $1, last_energy_update = NOW()
                WHERE user_id = $2
            """, energy, user_id)

        if not was_full and energy >= MAX_ENERGY:
            try:
                await bot.send_message(
                    user_id,
                    "⚡ <b>Энергия полностью восстановлена!</b>\n\n100/100 — можешь копать! ⛏️",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    return energy


async def spend_energy(user_id: int, amount: int = 1) -> bool:
    energy = await refresh_energy(user_id)
    if energy < amount:
        return False
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET energy = energy - $1 WHERE user_id = $2", amount, user_id)
    return True


# ---------------- ПОДПИСКА ----------------
async def check_sub(user_id: int) -> bool:
    try:
        member_channel = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        member_chat = await bot.get_chat_member(f"@{CHAT_USERNAME}", user_id)
        ok = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
        return member_channel.status in ok and member_chat.status in ok
    except Exception:
        return False


# ---------------- КЛАВИАТУРЫ ----------------
def sub_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="💬 Вступить в чат", url=CHAT_LINK)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]
    ])


def main_menu(is_admin: bool = False):
    rows = [
        [KeyboardButton(text="⛏️ Копать")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎒 Инвентарь")],
        [KeyboardButton(text="🛒 Рынок"), KeyboardButton(text="⚒️ Бусты")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛡️ Админка")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def market_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Продать руду", callback_data="sell_menu")],
        [InlineKeyboardButton(text="📊 Цены на руду", callback_data="prices_menu")],
        [InlineKeyboardButton(text="⛏️ Купить кирку", callback_data="buy_menu")]
    ])


def boosts_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{b['name']} — {b['price']}$", callback_data=f"boost_{key}")]
        for key, b in BOOSTS.items()
    ] + [[InlineKeyboardButton(text="⬅️ Закрыть", callback_data="boost_close")]])


def back_market_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_market")]
    ])


def sell_menu_kb(user, prices):
    rows = []
    for key, ore in ORES.items():
        amount = user[f"ore_{key}"]
        price = prices[key]
        tax = price * SALE_TAX_PERCENT // 100
        netto = price - tax
        rows.append([InlineKeyboardButton(
            text=f"{ICONS[key]} {ore['name']}: {amount} шт — {netto}$/шт (налог {tax}$)",
            callback_data=f"sell_{key}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_market")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_menu(current_pickaxe):
    current_idx = PICKAXE_ORDER.index(current_pickaxe)
    rows = []
    for i, name in enumerate(PICKAXE_ORDER):
        info = PICKAXES[name]
        if i <= current_idx:
            rows.append([InlineKeyboardButton(
                text=f"🔒 {name} — {info['price']}$ (недоступно)",
                callback_data="noop"
            )])
        else:
            rows.append([InlineKeyboardButton(
                text=f"⛏️ Купить {name} — {info['price']}$ (прочн. {info['durability']})",
                callback_data=f"buy_{name}"
            )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_market")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pickaxe_info_text():
    text = "⛏️ <b>Кирки, шансы и прочность</b>\n\n"
    for name, info in PICKAXES.items():
        c, ir, g, d, e = info["chances"]
        text += (
            f"<b>{name}</b> — {info['price']}$ | 🔧 {info['durability']}\n"
            f"🪨 {c}% | ⚙️ {ir}% | 🟡 {g}% | 💎 {d}% | 💚 {e}%\n\n"
        )
    return text


# ---------------- АДМИН ПАНЕЛЬ ----------------
def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Список игроков", callback_data="admin_users")],
        [InlineKeyboardButton(text="💰 Топ по балансу", callback_data="admin_top")],
        [InlineKeyboardButton(text="📢 Реклама", callback_data="admin_ad")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⬅️ Закрыть", callback_data="admin_close")]
    ])


@dp.message(F.text == "🛡️ Админка")
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У тебя нет доступа.")
        return
    await message.answer(
        "🛡️ <b>Админ-панель</b>\n\nВыбирай действие:",
        reply_markup=admin_menu(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("❌ Нет доступа", show_alert=True)
        return
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        day_ago = datetime.now(timezone.utc) - timedelta(days=1)
        active = await conn.fetchval("SELECT COUNT(*) FROM users WHERE joined_at > $1", day_ago)
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        total_mined = await conn.fetchval("SELECT COALESCE(SUM(total_mined), 0) FROM users")
        total_refs = await conn.fetchval("SELECT COALESCE(SUM(referrals_count), 0) FROM users")
        top = await conn.fetch("SELECT first_name, balance FROM users ORDER BY balance DESC LIMIT 5")

    top_text = "\n".join([f"   {i+1}. {r['first_name']} — {r['balance']}$" for i, r in enumerate(top)])

    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего игроков: <b>{total}</b>\n"
        f"🟢 Новых за 24ч: <b>{active}</b>\n"
        f"💰 Общий баланс: <b>{total_balance}$</b>\n"
        f"⛏️ Всего копаний: <b>{total_mined}</b>\n"
        f"👥 Всего рефералов: <b>{total_refs}</b>\n\n"
        f"🏆 <b>Топ-5 по балансу:</b>\n{top_text}"
    )
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ]), parse_mode="HTML")


@dp.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("❌ Нет доступа", show_alert=True)
        return
    async with db_pool.acquire() as conn:
        users = await conn.fetch("""
            SELECT user_id, first_name, username, balance, pickaxe
            FROM users ORDER BY joined_at DESC LIMIT 20
        """)
    text = "👥 <b>Последние 20 игроков</b>\n\n"
    for u in users:
        text += f"• <b>{u['first_name']}</b> (@{u['username'] or '—'})\n   ID: <code>{u['user_id']}</code> | {u['balance']}$ | {u['pickaxe']}\n"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ]), parse_mode="HTML")


@dp.callback_query(F.data == "admin_top")
async def admin_top(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("❌ Нет доступа", show_alert=True)
        return
    async with db_pool.acquire() as conn:
        top = await conn.fetch("""
            SELECT first_name, username, balance, total_mined, level
            FROM users ORDER BY balance DESC LIMIT 10
        """)
    text = "🏆 <b>Топ-10 игроков</b>\n\n"
    for i, u in enumerate(top, 1):
        text += f"{i}. <b>{u['first_name']}</b> (@{u['username'] or '—'})\n   💰 {u['balance']}$ | ⛏️ {u['total_mined']} | 📈 Ур. {u['level']}\n"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ]), parse_mode="HTML")


@dp.callback_query(F.data == "admin_ad")
async def admin_ad(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("❌ Нет доступа", show_alert=True)
        return
    ad = await get_setting("ad_text") or "(пусто)"
    text = (
        f"📢 <b>Настройка рекламы</b>\n\n"
        f"Реклама показывается каждые <b>{AD_EVERY_N_MINES}</b> копаний.\n\n"
        f"<b>Текущий текст:</b>\n{ad}\n\n"
        f"Изменить: <code>/set_ad Твой текст рекламы</code>\n"
        f"Убрать: <code>/set_ad off</code>"
    )
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ]), parse_mode="HTML")


@dp.message(Command("set_ad"))
async def cmd_set_ad(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = message.text.replace("/set_ad", "").strip()
    if not text:
        await message.answer("❌ Напиши текст: <code>/set_ad Твой текст</code>", parse_mode="HTML")
        return
    if text.lower() == "off":
        await set_setting("ad_text", "")
        await message.answer("✅ Реклама отключена.")
        return
    await set_setting("ad_text", text)
    await message.answer(f"✅ Текст рекламы сохранён:\n\n{text}")


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("❌ Нет доступа", show_alert=True)
        return
    await call.message.edit_text(
        "📣 <b>Рассылка</b>\n\nФормат: <code>/broadcast Текст</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
        ])
    )


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("❌ Пустое сообщение.")
        return
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")
    sent = failed = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], f"📢 <b>Сообщение от админа:</b>\n\n{text}", parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Отправлено: {sent}\n❌ Не доставлено: {failed}")


@dp.callback_query(F.data == "admin_back")
async def admin_back(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.message.edit_text(
        "🛡️ <b>Админ-панель</b>\n\nВыбирай действие:",
        reply_markup=admin_menu(), parse_mode="HTML"
    )


@dp.callback_query(F.data == "admin_close")
async def admin_close(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    await call.message.delete()


# ---------------- /start ----------------
@dp.message(CommandStart(deep_link=True))
async def cmd_start_ref(message: Message, command: object):
    referrer_id = None
    if command.args:
        try:
            payload = decode_payload(command.args)
            referrer_id = int(payload)
        except Exception:
            pass

    user_id = message.from_user.id
    existing = await get_user(user_id)

    if not existing:
        await create_user(user_id, message.from_user.username, message.from_user.first_name, referrer_id)

    if referrer_id and not existing and referrer_id != user_id:
        await add_referral_reward(referrer_id)

    is_admin = user_id in ADMIN_IDS

    if await check_sub(user_id):
        await message.answer(f"⛏️ Привет, {message.from_user.first_name}!\n\nВыбирай действие 👇", reply_markup=main_menu(is_admin))
    else:
        await message.answer("⛏️ Привет! Чтобы играть, подпишись на канал и вступи в чат 👇", reply_markup=sub_keyboard())


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    is_admin = message.from_user.id in ADMIN_IDS

    if await check_sub(message.from_user.id):
        await message.answer(f"⛏️ Привет, {message.from_user.first_name}!\n\nВыбирай действие 👇", reply_markup=main_menu(is_admin))
    else:
        await message.answer("⛏️ Привет! Чтобы играть, подпишись на канал и вступи в чат 👇", reply_markup=sub_keyboard())


@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery):
    if await check_sub(call.from_user.id):
        await call.message.delete()
        is_admin = call.from_user.id in ADMIN_IDS
        await call.message.answer("✅ Подписка подтверждена! 👇", reply_markup=main_menu(is_admin))
    else:
        await call.answer("❌ Ты ещё не подписался!", show_alert=True)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Помощь по OreCraft</b>\n\n"
        "⛏️ <b>Копать</b> — добывай руду (1 энергия, кирка теряет прочность)\n"
        "🎒 <b>Инвентарь</b> — вся твоя руда\n"
        "👤 <b>Профиль</b> — статистика и реф-ссылка\n"
        "🛒 <b>Рынок</b> — продать руду, купить кирку\n"
        "⚒️ <b>Бусты</b> — ускорители за $\n\n"
        "⚡ Энергия: +1 каждые 5 сек\n"
        "🔧 Прочность: кирка ломается после N копаний\n"
        "💸 Налог: 5% с продажи\n"
        "💰 Реферал: 500$ за друга\n\n"
        "Удачи, шахтёр! 💎",
        parse_mode="HTML"
    )


# ---------------- ПРОФИЛЬ ----------------
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    if not await check_sub(message.from_user.id):
        await message.answer("❌ Сначала подпишись!", reply_markup=sub_keyboard())
        return

    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Напиши /start")
        return

    joined = user["joined_at"]
    if joined.tzinfo is None:
        joined = joined.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - joined
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes = rem // 60

    ref_link = await create_start_link(bot, str(message.from_user.id), encode=True)
    best = user["best_ore"]
    best_name = ORES[best]["name"] if best and best in ORES else "—"

    max_dur = PICKAXES[user["pickaxe"]]["durability"]

    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📛 Ник: <b>{user['first_name']}</b>\n"
        f"🔗 @{user['username'] or 'нет'}\n\n"
        f"💰 Баланс: <b>{user['balance']}$</b>\n"
        f"📈 Уровень: <b>{user['level']}</b>\n"
        f"⛏️ Кирка: <b>{user['pickaxe']}</b>\n"
        f"🔧 Прочность: <b>{user['pickaxe_durability']}/{max_dur}</b>\n"
        f"⚡ Энергия: <b>{user['energy']}/{MAX_ENERGY}</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"⛏️ Всего копаний: <b>{user['total_mined']}</b>\n"
        f"💵 Продано руды: <b>{user['total_sold']} шт</b>\n"
        f"💰 Заработано: <b>{user['total_earned']}$</b>\n"
        f"🏆 Лучшая руда: <b>{best_name}</b>\n\n"
        f"⏱ В боте: <b>{days}д {hours}ч {minutes}м</b>\n"
        f"👥 Рефералов: <b>{user['referrals_count']}</b> (по {REFERRAL_REWARD}$)\n\n"
        f"🔗 Твоя ссылка:\n<code>{ref_link}</code>",
        parse_mode="HTML"
    )


# ---------------- ИНВЕНТАРЬ ----------------
@dp.message(F.text == "🎒 Инвентарь")
async def inventory(message: Message):
    if not await check_sub(message.from_user.id):
        await message.answer("❌ Сначала подпишись!", reply_markup=sub_keyboard())
        return
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Напиши /start")
        return

    total = 0
    text = "🎒 <b>Инвентарь</b>\n\n"
    for key, ore in ORES.items():
        amount = user[f"ore_{key}"]
        total += amount
        text += f"{ICONS[key]} {ore['name']}: <b>{amount} шт</b>\n"
    text += f"\n📦 Всего руды: <b>{total} шт</b>"
    await message.answer(text, parse_mode="HTML")


# ---------------- КОПАТЬ ----------------
async def do_mine(user_id: int):
    """Логика одного копания. Возвращает (result_key, new_energy, broke, pickaxe_name)."""
    user = await get_user(user_id)
    if not user:
        return None

    pickaxe_name = user["pickaxe"]
    durability = user["pickaxe_durability"]

    # кирка сломана?
    if durability <= 0:
        return ("broke", 0, True, pickaxe_name)

    # шансы (с учётом luck-буста)
    chances = list(PICKAXES[pickaxe_name]["chances"])
    now = datetime.now(timezone.utc)
    luck_until = user["boost_luck_until"]
    if luck_until and luck_until.replace(tzinfo=timezone.utc) > now:
        # ×2 шанс на алмаз и изумруд за счёт угля
        chances[0] = max(0, chances[0] - 10)
        chances[3] += 5
        chances[4] += 5

    result_key = random.choices(
        ["coal", "iron", "gold", "diamond", "emerald"],
        weights=chances, k=1
    )[0]

    # ×2 руда
    amount = 1
    x2_until = user["boost_x2ore_until"]
    if x2_until and x2_until.replace(tzinfo=timezone.utc) > now:
        amount = 2

    # обновляем
    new_dur = durability - 1
    broke = new_dur <= 0

    async with db_pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET ore_{result_key} = ore_{result_key} + $1, "
            f"total_mined = total_mined + 1, "
            f"pickaxe_durability = $2, "
            f"last_mine_time = NOW() "
            f"WHERE user_id = $3",
            amount, new_dur, user_id
        )
        order = ["coal", "iron", "gold", "diamond", "emerald"]
        best = user["best_ore"]
        if best is None or order.index(result_key) > order.index(best):
            await conn.execute("UPDATE users SET best_ore = $1 WHERE user_id = $2", result_key, user_id)

    new_energy = await refresh_energy(user_id)
    return (result_key, new_energy, broke, pickaxe_name)


@dp.message(F.text == "⛏️ Копать")
async def mine(message: Message):
    if not await check_sub(message.from_user.id):
        await message.answer("❌ Сначала подпишись!", reply_markup=sub_keyboard())
        return

    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Напиши /start")
        return

    # проверка кирки
    if user["pickaxe_durability"] <= 0:
        await message.answer(
            "🔧 <b>Кирка сломана!</b>\n\n"
            "Купи новую в 🛒 Рынок → ⛏️ Купить кирку\n"
            "Или возьми бесплатную Каменную — она всегда доступна.",
            parse_mode="HTML"
        )
        return

    # кулдаун
    last_mine = user["last_mine_time"]
    if last_mine.tzinfo is None:
        last_mine = last_mine.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - last_mine).total_seconds() < MINE_COOLDOWN_SECONDS:
        return

    energy = await refresh_energy(message.from_user.id)
    if energy < ENERGY_COST:
        await message.answer(
            f"⚡ <b>Энергия закончилась!</b>\n\n"
            f"Осталось: <b>{energy}/{MAX_ENERGY}</b>\n"
            f"Восстановление: +1 каждые {ENERGY_REGEN_SECONDS} сек.",
            parse_mode="HTML"
        )
        return

    await spend_energy(message.from_user.id, ENERGY_COST)

    result = await do_mine(message.from_user.id)
    if not result:
        return

    result_key, new_energy, broke, pickaxe_name = result

    if result_key == "broke":
        await message.answer("🔧 Кирка сломана!")
        return

    ore = ORES[result_key]
    user = await get_user(message.from_user.id)
    max_dur = PICKAXES[pickaxe_name]["durability"]

    caption = (
        f"⛏️ Вы добыли: <b>{ore['name']}</b>\n"
        f"🔨 Киркой: <b>{pickaxe_name}</b>\n"
        f"🔧 Прочность: <b>{user['pickaxe_durability']}/{max_dur}</b>\n"
        f"⚡ Энергия: <b>{new_energy}/{MAX_ENERGY}</b>"
    )
    if broke:
        caption += "\n\n💥 <b>Кирка сломалась! Купи новую в Рынке.</b>"

    try:
        photo = FSInputFile(ore["img"])
        await message.answer_photo(photo=photo, caption=caption, parse_mode="HTML")
    except Exception:
        await message.answer(caption, parse_mode="HTML")

    # ---- РЕКЛАМА ----
    user = await get_user(message.from_user.id)
    if user["mines_count"] > 0 and user["mines_count"] % AD_EVERY_N_MINES == 0:
        ad = await get_setting("ad_text")
        if ad:
            await message.answer(f"📢 <b>Реклама</b>\n\n{ad}", parse_mode="HTML")

    # увеличиваем счётчик
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET mines_count = mines_count + 1 WHERE user_id = $1", message.from_user.id)


# ---------------- РЫНОК ----------------
@dp.message(F.text == "🛒 Рынок")
async def market(message: Message):
    if not await check_sub(message.from_user.id):
        await message.answer("❌ Сначала подпишись!", reply_markup=sub_keyboard())
        return
    await message.answer("🛒 <b>Рынок</b>\n\nВыбирай действие:", reply_markup=market_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "back_market")
async def cb_back_market(call: CallbackQuery):
    await call.message.edit_text("🛒 <b>Рынок</b>\n\nВыбирай действие:", reply_markup=market_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer("🔒 Эта кирка недоступна", show_alert=False)


# ---------------- ЦЕНЫ ----------------
@dp.callback_query(F.data == "prices_menu")
async def cb_prices_menu(call: CallbackQuery):
    prices = await get_prices()
    text = f"📊 <b>Цены на руду</b> (налог {SALE_TAX_PERCENT}%)\n\n"
    for key, ore in ORES.items():
        price = prices[key]
        base = ore["price"]
        arrow = "📈" if price > base else ("📉" if price < base else "➖")
        tax = price * SALE_TAX_PERCENT // 100
        netto = price - tax
        text += (
            f"{ICONS[key]} <b>{ore['name']}</b>\n"
            f"   Цена: <b>{price}$</b> {arrow} | На руки: <b>{netto}$</b> (налог {tax}$)\n"
            f"   Мин: {ore['min']}$ | Макс: {ore['max']}$\n\n"
        )
    await call.message.edit_text(text, reply_markup=back_market_kb(), parse_mode="HTML")


# ---------------- ПРОДАЖА ----------------
@dp.callback_query(F.data == "sell_menu")
async def cb_sell_menu(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    prices = await get_prices()
    text = f"💰 <b>Продажа руды</b>\n\nНалог: <b>{SALE_TAX_PERCENT}%</b>\nВыбери руду (продаётся вся сразу):"
    await call.message.edit_text(text, reply_markup=sell_menu_kb(user, prices), parse_mode="HTML")


@dp.callback_query(F.data.startswith("sell_"))
async def cb_sell_ore(call: CallbackQuery):
    key = call.data.split("_")[1]
    if key not in ORES:
        await call.answer("Ошибка")
        return

    user = await get_user(call.from_user.id)
    amount = user[f"ore_{key}"]
    if amount <= 0:
        await call.answer(f"❌ У тебя нет {ORES[key]['name']}!", show_alert=True)
        return

    prices = await get_prices()
    price = prices[key]
    gross = amount * price
    tax = gross * SALE_TAX_PERCENT // 100
    netto = gross - tax

    async with db_pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET ore_{key} = 0, "
            f"balance = balance + $1, "
            f"total_sold = total_sold + $2, "
            f"total_earned = total_earned + $1 "
            f"WHERE user_id = $3",
            netto, amount, call.from_user.id
        )

    await call.answer(
        f"✅ Продано {amount} шт за {gross}$\n💸 Налог: {tax}$\n💰 На руки: {netto}$",
        show_alert=True
    )

    user = await get_user(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=sell_menu_kb(user, prices))
    except Exception:
        pass


# ---------------- ПОКУПКА КИРКИ ----------------
@dp.callback_query(F.data == "buy_menu")
async def cb_buy_menu(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    text = pickaxe_info_text()
    text += f"💰 Твой баланс: <b>{user['balance']}$</b>\n"
    text += f"⛏️ Текущая кирка: <b>{user['pickaxe']}</b>"
    await call.message.edit_text(text, reply_markup=buy_menu(user["pickaxe"]), parse_mode="HTML")


@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy_pickaxe(call: CallbackQuery):
    name = call.data.split("_", 1)[1]
    if name not in PICKAXES:
        await call.answer("Ошибка")
        return

    user = await get_user(call.from_user.id)
    current_idx = PICKAXE_ORDER.index(user["pickaxe"])
    new_idx = PICKAXE_ORDER.index(name)

    if new_idx <= current_idx:
        await call.answer("🔒 Эту кирку купить нельзя!", show_alert=True)
        return

    price = PICKAXES[name]["price"]
    if user["balance"] < price:
        await call.answer(f"❌ Не хватает! Нужно {price}$", show_alert=True)
        return

    durability = PICKAXES[name]["durability"]

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET balance = balance - $1, pickaxe = $2, pickaxe_durability = $3 WHERE user_id = $4",
            price, name, durability, call.from_user.id
        )

    await call.answer(f"✅ Куплена кирка: {name}!", show_alert=True)

    user = await get_user(call.from_user.id)
    text = pickaxe_info_text()
    text += f"💰 Твой баланс: <b>{user['balance']}$</b>\n"
    text += f"⛏️ Текущая кирка: <b>{user['pickaxe']}</b>"
    try:
        await call.message.edit_text(text, reply_markup=buy_menu(user["pickaxe"]), parse_mode="HTML")
    except Exception:
        pass


# ---------------- БУСТЫ ----------------
@dp.message(F.text == "⚒️ Бусты")
async def boosts(message: Message):
    if not await check_sub(message.from_user.id):
        await message.answer("❌ Сначала подпишись!", reply_markup=sub_keyboard())
        return
    user = await get_user(message.from_user.id)
    text = "⚒️ <b>Магазин бустов</b>\n\n"
    for key, b in BOOSTS.items():
        text += f"{b['name']} — <b>{b['price']}$</b>\n   {b['desc']}\n\n"
    text += f"💰 Твой баланс: <b>{user['balance']}$</b>"
    await message.answer(text, reply_markup=boosts_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "boost_close")
async def cb_boost_close(call: CallbackQuery):
    await call.message.delete()


@dp.callback_query(F.data.startswith("boost_"))
async def cb_boost_buy(call: CallbackQuery):
    key = call.data.split("_", 1)[1]
    if key not in BOOSTS:
        return

    user = await get_user(call.from_user.id)
    b = BOOSTS[key]

    if user["balance"] < b["price"]:
        await call.answer(f"❌ Не хватает! Нужно {b['price']}$", show_alert=True)
        return

    now = datetime.now(timezone.utc)

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", b["price"], call.from_user.id)

        if key == "energy":
            await conn.execute(f"UPDATE users SET energy = LEAST({MAX_ENERGY}, energy + 50) WHERE user_id = $1", call.from_user.id)
        elif key == "x2ore":
            await conn.execute("UPDATE users SET boost_x2ore_until = $1 WHERE user_id = $2", now + timedelta(hours=1), call.from_user.id)
        elif key == "autoclick":
            await conn.execute("UPDATE users SET autoclick_until = $1 WHERE user_id = $2", now + timedelta(minutes=30), call.from_user.id)
        elif key == "luck":
            await conn.execute("UPDATE users SET boost_luck_until = $1 WHERE user_id = $2", now + timedelta(hours=1), call.from_user.id)

    await call.answer(f"✅ Активирован буст: {b['name']}!", show_alert=True)


# ---------------- АВТО-КОПАНИЕ (фоновое) ----------------
async def autoclick_loop():
    while True:
        await asyncio.sleep(3)
        try:
            now = datetime.now(timezone.utc)
            async with db_pool.acquire() as conn:
                users = await conn.fetch("""
                    SELECT user_id FROM users 
                    WHERE autoclick_until IS NOT NULL AND autoclick_until > $1
                      AND pickaxe_durability > 0 AND energy >= $2
                """, now, ENERGY_COST)

            for u in users:
                uid = u["user_id"]
                await spend_energy(uid, ENERGY_COST)
                result = await do_mine(uid)
                if result and result[0] != "broke":
                    key, energy, broke, pk = result
                    ore = ORES[key]
                    try:
                        await bot.send_message(
                            uid,
                            f"🤖 <b>Авто-копание</b>\n\n"
                            f"⛏️ Добыто: <b>{ore['name']}</b>\n"
                            f"⚡ Энергия: <b>{energy}/{MAX_ENERGY}</b>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
        except Exception as e:
            print(f"❌ Autoclick ошибка: {e}")


# ---------------- HEALTH-CHECK ----------------
async def health_handler(request):
    return web.Response(text="OK", status=200)


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 HTTP-сервер на порту {port}")


# ---------------- ЗАПУСК ----------------
async def main():
    await init_db()
    await start_web_server()
    asyncio.create_task(price_loop())
    asyncio.create_task(autoclick_loop())
    print("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
