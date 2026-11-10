import asyncio
import logging
import sys
import uuid
import time
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    Message, 
    LabeledPrice, 
    PreCheckoutQuery
)
import aiohttp
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

gitignore_content = """# База данных SQLite
*.db
*.sqlite
*.sqlite3

# Служебные файлы Python
__pycache__/
*.py[cod]
*$py.class

# Системные файлы
.DS_Store
Thumbs.db
"""

# Создаем файл .gitignore в корневой папке
if not os.path.exists(".gitignore"):
    with open(".gitignore", "w", encoding="utf-8") as f:
        f.write(gitignore_content.strip())
    print("Файл .gitignore успешно создан!")
else:
    print("Файл .gitignore уже существует.")


# --- НАСТРОЙКИ ---
BOT_TOKEN = "9067851856:AAGPYkUtKzmA8sSaIHCUAmcJr4tjAn8_S8s"
PAYMENT_PROVIDER_TOKEN = "9067851856:AAGPYkUtKzmA8sSaIHCUAmcJr4tjAn8_S8s"

# Данные твоего сервера и панели 3X-UI
SERVER_IP = "ТВОЙ_IP_СЕРВЕРА"  # Сюда впиши IP-адрес твоего VPS от H1Cloud
PANEL_URL = f"http://{SERVER_IP}:2053"  # Порт панели (обычно 2053 или 8080)
PANEL_USER = "admin"
PANEL_PASS = "password"
INBOUND_ID = 1
PUBLIC_KEY = "ТВОЙ_PUBLIC_KEY"
SHORT_ID = "ТВОЙ_SHORT_ID"

DB_FILE = "kizo_vpn.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                vless_key TEXT,
                expiry_date TIMESTAMP,
                test_used INTEGER DEFAULT 0,
                invited_by INTEGER
            )
        """)
        await db.commit()

async def get_user_from_db(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT vless_key, expiry_date, test_used, invited_by FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def save_user_sub(user_id: int, username: str, vless_key: str, days: int, is_test: bool = False):
    expiry_date = datetime.now() + timedelta(days=days)
    async with aiosqlite.connect(DB_FILE) as db:
        if is_test:
            await db.execute("""
                INSERT INTO users (user_id, username, vless_key, expiry_date, test_used)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    vless_key = excluded.vless_key,
                    expiry_date = excluded.expiry_date,
                    test_used = 1
            """, (user_id, username, vless_key, expiry_date))
        else:
            await db.execute("""
                INSERT INTO users (user_id, username, vless_key, expiry_date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    vless_key = excluded.vless_key,
                    expiry_date = excluded.expiry_date
            """, (user_id, username, vless_key, expiry_date))
        await db.commit()

# --- КЛАВИАТУРЫ ---
def get_main_menu(has_test: bool = False):
    keyboard = [
        [InlineKeyboardButton(text="🚀 Получить KIZO VPN", callback_data="get_vpn")],
    ]
    if not has_test:
        keyboard.append([InlineKeyboardButton(text="🎁 Тест на 3 дня", callback_data="get_test")])
        
    keyboard.extend([
        [InlineKeyboardButton(text="👥 Реферальная программа", callback_data="referral")],
        [InlineKeyboardButton(text="💳 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton(text="📖 Инструкция", callback_data="instruction")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main")]]
    )

# --- РАБОТА С 3X-UI ЧЕРЕЗ API ---
async def create_xui_client(user_id: int, user_name: str, days: int) -> str:
    client_uuid = str(uuid.uuid4())
    email = f"user_{user_id}_{user_name or 'client'}"
    expiry_time = int((time.time() + days * 86400) * 1000)

    login_url = f"{PANEL_URL}/login"
    add_client_url = f"{PANEL_URL}/panel/api/inbounds/addClient"

    async with aiohttp.ClientSession() as session:
        login_data = {"username": PANEL_USER, "password": PANEL_PASS}
        async with session.post(login_url, data=login_data) as resp:
            if resp.status != 200:
                raise Exception("Ошибка авторизации в панели 3X-UI")

        payload = {
            "id": INBOUND_ID,
            "settings": f"""{{
                "clients": [
                    {{
                        "id": "{client_uuid}",
                        "alterId": 0,
                        "email": "{email}",
                        "limitIp": 0,
                        "totalGB": 0,
                        "expiryTime": {expiry_time},
                        "enable": true,
                        "tgId": "",
                        "flow": "xtls-rprx-vision"
                    }}
                ]
            }}"""
        }

        async with session.post(add_client_url, json=payload) as resp:
            result = await resp.json()
            if not result.get("success"):
                raise Exception(f"Не удалось создать клиента в панели: {result.get('msg')}")

    vless_link = (
        f"vless://{client_uuid}@{SERVER_IP}:443?"
        f"type=tcp&security=reality&pbk={PUBLIC_KEY}&fp=chrome&sni=google.com&sid={SHORT_ID}"
        f"#{user_name}_KIZO"
    )
    return vless_link

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    args = command.args
    
    user_data = await get_user_from_db(user_id)
    
    if not user_data and args and args.startswith("ref"):
        try:
            referrer_id = int(args.replace("ref", ""))
            if referrer_id != user_id:
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.execute(
                        "INSERT OR IGNORE INTO users (user_id, username, invited_by) VALUES (?, ?, ?)",
                        (user_id, message.from_user.username, referrer_id)
                    )
                    await db.commit()
                
                async with aiosqlite.connect(DB_FILE) as db:
                    async with db.execute("SELECT expiry_date FROM users WHERE user_id = ?", (referrer_id,)) as cursor:
                        ref_user = await cursor.fetchone()
                        if ref_user and ref_user[0]:
                            current_expiry = datetime.strptime(ref_user[0].split('.')[0], "%Y-%m-%d %H:%M:%S")
                            new_expiry = max(current_expiry, datetime.now()) + timedelta(days=3)
                            await db.execute("UPDATE users SET expiry_date = ? WHERE user_id = ?", (new_expiry, referrer_id))
                            await db.commit()
                            
                            try:
                                await bot.send_message(
                                    referrer_id,
                                    "🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n"
                                    "Вам начислено **+3 дня** к подписке в подарок! 🎁"
                                )
                            except Exception:
                                pass
        except ValueError:
            pass

    user_data_updated = await get_user_from_db(user_id)
    has_test = user_data_updated[2] if user_data_updated else 0
    
    text = (
        f"Привет, {user_name}! 👋\n\n"
        "Добро пожаловать в **KIZO VPN** — твой быстрый и безопасный доступ в интернет без ограничений.\n\n"
        "Выбери нужный пункт в меню ниже:"
    )
    await message.answer(text, reply_markup=get_main_menu(has_test), parse_mode="Markdown")

@dp.callback_query(F.data == "get_vpn")
async def process_get_vpn(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 30 дней (200 руб.)", callback_data="pay_30_days")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ]
    )
    await callback.message.edit_text(
        "⚡ **Выбери тариф:**\n\n"
        "• 30 дней — **200 руб.**\n\n"
        "Нажми кнопку ниже для безопасной оплаты:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "get_test")
async def process_get_test(callback: types.CallbackQuery):
    user = callback.from_user
    user_data = await get_user_from_db(user.id)
    
    if user_data and user_data[2] == 1:
        await callback.answer("⚠️ Вы уже использовали бесплатный тестовый период!", show_alert=True)
        return
        
    try:
        vpn_key = await create_xui_client(user.id, user.username or user.first_name, days=3)
        await save_user_sub(user.id, user.username or user.first_name, vpn_key, days=3, is_test=True)
        
        await callback.message.edit_text(
            "🎁 **Тестовый период активирован!**\n\n"
            "Вам предоставлен доступ к **KIZO VPN на 3 дня**.\n\n"
            "🔑 **Твой ключ доступа:**\n"
            f"`{vpn_key}`\n\n"
            "Скопируй его и вставь в приложение **Hiddify (Happ)**. Приятного пользования!",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка при выдаче теста: {e}")
        await callback.answer("⚠️ Произошла ошибка при создании теста. Попробуйте позже.", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "referral")
async def process_referral(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref{user_id}"
    
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE invited_by = ?", (user_id,)) as cursor:
            friends_count = (await cursor.fetchone())[0]

    text = (
        "👥 **Реферальная программа**\n\n"
        "Приглашай друзей по своей ссылке и получай **+3 дня** к подписке за каждого зарегистрированного друга!\n\n"
        f"🔗 **Твоя ссылка:**\n`{ref_link}`\n\n"
        f"📊 Приглашено друзей: **{friends_count}**"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "my_sub")
async def process_my_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = await get_user_from_db(user_id)
    
    if user_data and user_data[0]:
        vless_key, expiry_date, _, _ = user_data
        text = (
            "💳 **Информация о твоей подписке:**\n\n"
            f"⏳ Активна до: `{expiry_date}`\n\n"
            "🔑 **Твой ключ доступа:**\n"
            f"`{vless_key}`"
        )
    else:
        text = (
            "💳 **Информация о подписке:**\n\n"
            "У тебя пока нет активных подписок.\n"
            "Нажми «🎁 Тест на 3 дня» или «🚀 Получить KIZO VPN»."
        )
        
    has_test = user_data[2] if user_data else 0
    await callback.message.edit_text(text, reply_markup=get_main_menu(has_test), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "instruction")
async def process_instruction(callback: types.CallbackQuery):
    text = (
        "📖 **Как подключить KIZO VPN:**\n\n"
        "1. Скачай приложение **Hiddify (Happ)** (или v2rayNG / FoXray).\n"
        "2. Скопируй свой ключ из раздела **«Моя подписка»**.\n"
        "3. Импортируй его в приложение и включи тумблер!"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "support")
async def process_support(callback: types.CallbackQuery):
    text = "🆘 Возникли вопросы? Напиши администратору: @username"
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def process_back(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = await get_user_from_db(user_id)
    has_test = user_data[2] if user_data else 0
    
    await callback.message.edit_text(
        "Главное меню **KIZO VPN**:",
        reply_markup=get_main_menu(has_test),
        parse_mode="Markdown"
    )
    await callback.answer()

# --- ОПЛАТА ---

@dp.callback_query(F.data == "pay_30_days")
async def process_pay(callback: types.CallbackQuery):
    prices = [LabeledPrice(label="Подписка KIZO VPN (30 дней)", amount=20000)]
    await callback.message.answer_invoice(
        title="KIZO VPN (1 месяц)",
        description="Доступ к быстрому и безопасному VPN на 30 дней",
        payload="kizo_vpn_30_days",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        start_parameter="kizo-vpn-pay",
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    user = message.from_user
    try:
        vpn_key = await create_xui_client(user.id, user.username or user.first_name, days=30)
        await save_user_sub(user.id, user.username or user.first_name, vpn_key, days=30, is_test=False)
        
        await message.answer(
            "🎉 **Оплата прошла успешно!**\n\n"
            "Твой персональный ключ доступа сохранен в разделе **«Моя подписка»**:\n"
            f"`{vpn_key}`\n\n"
            "Скопируй его и вставь в приложение **Hiddify (Happ)**! 🚀",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка при генерации: {e}")
        await message.answer("⚠️ Оплата прошла, но произошла ошибка генерации. Напиши администратору.")

# --- ФОНОВАЯ ПРОВЕРКА ПОДПИСОК ---
async def check_expired_subscriptions():
    async with aiosqlite.connect(DB_FILE) as db:
        now = datetime.now()
        async with db.execute("SELECT user_id, username FROM users WHERE expiry_date < ?", (now,)) as cursor:
            expired_users = await cursor.fetchall()
            
            if not expired_users:
                return

            for row in expired_users:
                user_id = row[0]
                try:
                    await bot.send_message(
                        user_id, 
                        "⚠️ **Срок действия твоей подписки (или тестового периода) истек!**\n\n"
                        "Доступ к интернету был автоматически приостановлен. "
                        "Продли подписку в главном меню, чтобы снова активировать VPN."
                    )
                except Exception as ex:
                    logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {ex}")

# --- ВЕБ-СЕРВЕР ДЛЯ НЕУСЫПАЮЩЕГО БОТА ---
async def handle_ping(request):
    return web.Response(text="Kizo VPN Bot is active!")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', handle_ping)])
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Koyeb сам назначает порт через системную переменную PORT. 
    # Если запуск идет локально, он подставит 8000 по умолчанию.
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    await site.start()


# --- ЗАПУСК ---
async def main():
    await init_db()
    
    # Запуск веб-сервера для поддержки активности
    await start_web_server()
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_expired_subscriptions, "interval", days=1)
    scheduler.start()
    
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    print("Бот KIZO VPN успешно запущен и веб-сервер активен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
