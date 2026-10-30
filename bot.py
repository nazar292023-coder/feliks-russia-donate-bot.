
import os
import re
import sqlite3
import logging
from threading import Thread

from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "donate.db")
CARD_DETAILS = os.getenv(
    "CARD_DETAILS",
    "Реквизиты уточняются у владельца."
)
OWNER = "crmp_feliks"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

app_web = Flask(__name__)


@app_web.route("/")
def home():
    return "FELIKS RUSSIA DONATE: online", 200


def run_web():
    port = int(os.getenv("PORT", "10000"))
    app_web.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                price TEXT NOT NULL DEFAULT 'Уточнить',
                description TEXT DEFAULT '',
                photo TEXT DEFAULT '',
                active INTEGER DEFAULT 1
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                product TEXT,
                status TEXT DEFAULT 'Новая'
            )
        """)

        columns = {
            row["name"]
            for row in c.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        }

        if "payment_method" not in columns:
            c.execute(
                "ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT ''"
            )

        if "proof_file_id" not in columns:
            c.execute(
                "ALTER TABLE orders ADD COLUMN proof_file_id TEXT DEFAULT ''"
            )

        count = c.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        if count == 0:
            vehicles = [
                ("Транспорт", "Mustang Hoonigan", "150 ₽ / 100 ⭐"),
                ("Транспорт", "Квадроцикл", "300 ₽ / 200 ⭐"),
                ("Транспорт", "Лимузин", "150 ₽ / 100 ⭐"),
                ("Транспорт", "GAZ 2402 Halloween", "100 ₽ / 50 ⭐"),
                ("Транспорт", "BMW M5 CS", "250 ₽ / 200 ⭐"),
                ("Транспорт", "Marussia B1", "150 ₽ / 100 ⭐"),
                ("Транспорт", "Mercedes-AMG E63 S Wagon", "200 ₽ / 150 ⭐"),
                ("Транспорт", "Lamborghini Countach", "230 ₽ / 150 ⭐"),
                ("Транспорт", "Mercedes-Benz AMG Project One", "150 ₽ / 150 ⭐"),
                ("Транспорт", "BMW M5 F90 ППС", "200 ₽ / 150 ⭐"),
                ("Транспорт", "Porsche Spyder", "200 ₽ / 200 ⭐"),
                ("Транспорт", "SSG 033", "200 ₽ / 150 ⭐"),
            ]

            skins = [
                ("Скины", "София", "150 ₽"),
                ("Скины", "Лео", "230 ₽"),
                ("Скины", "Хиш Мил", "129 ₽"),
                ("Скины", "Максимка", "349 ₽"),
            ]

            for category, name, price in vehicles + skins:
                c.execute(
                    "INSERT INTO products(category,name,price) VALUES(?,?,?)",
                    (category, name, price)
                )


def is_admin(update):
    return bool(
        update.effective_user
        and ADMIN_ID
        and update.effective_user.id == ADMIN_ID
    )


def main_menu():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🚘 Транспорт"), KeyboardButton("👕 Скины")],
            [KeyboardButton("💰 Покупка вирты"), KeyboardButton("🎁 Скидки")],
            [KeyboardButton("📦 Мои заказы"), KeyboardButton("ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Все товары", callback_data="admin:products")],
        [InlineKeyboardButton("➕ Добавить товар", callback_data="admin:add")],
        [InlineKeyboardButton("✏️ Изменить товар", callback_data="admin:editlist")],
        [InlineKeyboardButton("🖼 Изменить фото", callback_data="admin:photolist")],
        [InlineKeyboardButton("🗑 Удалить товар", callback_data="admin:deletelist")],
        [InlineKeyboardButton("📋 Заказы", callback_data="admin:orders")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="admin:close")]
    ])


def product_keyboard(category):
    with db() as c:
        products = c.execute(
            """
            SELECT * FROM products
            WHERE category=? AND active=1
            ORDER BY id
            """,
            (category,)
        ).fetchall()

    rows = [
        [InlineKeyboardButton(
            f"{p['name']} — {p['price']}",
            callback_data=f"product:{p['id']}"
        )]
        for p in products
    ]

    return InlineKeyboardMarkup(rows) if rows else None


def payment_keyboard(pid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплата картой", callback_data=f"paycard:{pid}")],
        [InlineKeyboardButton("💎 Оплата BC", callback_data=f"paybc:{pid}")],
        [InlineKeyboardButton("⭐ Оплата звёздами", callback_data=f"paystars:{pid}")]
    ])


def get_ruble_amount(price):
    match = re.search(r"(\d+)\s*₽", price or "")
    return match.group(1) if match else None


async def notify_admin(context, order_id, product, user, method, price):
    if not ADMIN_ID:
        log.warning("ADMIN_ID не настроен")
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📋 Открыть заказ",
            callback_data=f"orderinfo:{order_id}"
        )
    ]])

    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🛒 НОВЫЙ ЗАКАЗ #{order_id}\n\n"
            f"📦 Товар: {product}\n"
            f"💵 Цена: {price}\n"
            f"💳 Способ оплаты: {method}\n"
            f"👤 Игрок: {user.full_name}\n"
            f"🆔 ID: {user.id}\n"
            f"Статус: Ожидает оплаты/связи",
            reply_markup=keyboard
        )
    except Exception:
        log.exception("Не удалось уведомить администратора")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🎮 Добро пожаловать в FELIKS RUSSIA | DONATE!\n\n"
        "Выбери нужный раздел в меню ниже.",
        reply_markup=main_menu()
    )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    context.user_data.pop("action", None)
    await update.message.reply_text(
        "🛠 Админ-панель:",
        reply_markup=admin_menu()
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in (
        "action", "product_id", "proof_order_id",
        "new_name", "new_price"
    ):
        context.user_data.pop(key, None)

    await update.message.reply_text(
        "Действие отменено. Выбери раздел меню.",
        reply_markup=main_menu()
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = (msg.text or "").strip()
    action = context.user_data.get("action")

    if action and is_admin(update):

        if action == "add_name":
            context.user_data["new_name"] = text
            context.user_data["action"] = "add_price"
            await msg.reply_text("Введи цену товара, например 150 ₽.")
            return

        if action == "add_price":
            context.user_data["new_price"] = text
            context.user_data["action"] = "add_category"
            await msg.reply_text(
                "Введи категорию: Транспорт, Скины или Вирта."
            )
            return

        if action == "add_category":
            normalized = text.lower()

            if normalized in ("вирта", "валюта", "игровая валюта"):
                category = "Вирта"
            elif normalized in ("транспорт", "машины", "тс"):
                category = "Транспорт"
            elif normalized in ("скины", "скин"):
                category = "Скины"
            else:
                category = text

            with db() as c:
                c.execute(
                    "INSERT INTO products(category,name,price) VALUES(?,?,?)",
                    (
                        category,
                        context.user_data["new_name"],
                        context.user_data["new_price"]
                    )
                )

            context.user_data.pop("action", None)
            await msg.reply_text(
                "✅ Товар добавлен!",
                reply_markup=admin_menu()
            )
            return

        edit_fields = {
            "edit_name": "name",
            "edit_price": "price",
            "edit_description": "description",
            "edit_category": "category"
        }

        if action in edit_fields:
            column = edit_fields[action]
            product_id = context.user_data.get("product_id")

            with db() as c:
                c.execute(
                    f"UPDATE products SET {column}=? WHERE id=?",
                    (text, product_id)
                )

            context.user_data.pop("action", None)
            context.user_data.pop("product_id", None)

            await msg.reply_text(
                "✅ Изменения сохранены.",
                reply_markup=admin_menu()
            )
            return

    if text == "🏠 Главное меню":
        await start(update, context)

    elif text == "🚘 Транспорт":
        keyboard = product_keyboard("Транспорт")
        await msg.reply_text(
            "🚘 Выбери транспорт:" if keyboard else "Товаров пока нет.",
            reply_markup=keyboard
        )

    elif text == "👕 Скины":
        keyboard = product_keyboard("Скины")
        await msg.reply_text(
            "👕 Выбери скин:" if keyboard else "Товаров пока нет.",
            reply_markup=keyboard
        )

    elif text == "💰 Покупка вирты":
        keyboard = product_keyboard("Вирта")
        await msg.reply_text(
            "💰 Выбери доступный товар в категории «Вирта». "
            "Если список пуст, уточни количество и цену у владельца: "
            "@crmp_feliks.",
            reply_markup=keyboard
        )

    elif text == "🎁 Скидки":
        await msg.reply_text(
            "🎁 СКИДКИ И НОВОСТИ\n\n"
            "Здесь публикуется информация о новых товарах, "
            "актуальных ценах и специальных предложениях.\n\n"
            "По вопросам: @crmp_feliks"
        )

    elif text == "ℹ️ Помощь":
        await msg.reply_text(
            "ℹ️ ПОМОЩЬ — FELIKS RUSSIA | DONATE\n\n"
            "🚘 Транспорт — выбор транспорта.\n"
            "👕 Скины — выбор скина.\n"
            "💰 Покупка вирты — игровая валюта.\n"
            "🎁 Скидки — новости и предложения.\n"
            "📦 Мои заказы — список заявок.\n\n"
            "💳 Карта: бот отправит реквизиты. После оплаты "
            "отправь фото или файл чека.\n"
            "💎 BC и ⭐: оплата согласовывается с владельцем "
            "@crmp_feliks.\n\n"
            "Товар выдаётся вручную после проверки оплаты.\n"
            "/cancel — отмена действия."
        )

    elif text == "📦 Мои заказы":
        with db() as c:
            orders = c.execute(
                """
                SELECT * FROM orders
                WHERE user_id=?
                ORDER BY id DESC LIMIT 10
                """,
                (update.effective_user.id,)
            ).fetchall()

        if not orders:
            await msg.reply_text("📦 У тебя пока нет заказов.")
        else:
            body = "\n".join(
                f"#{o['id']} — {o['product']} — {o['status']}"
                for o in orders
            )
            await msg.reply_text("📦 Твои заказы:\n\n" + body)

    else:
        await msg.reply_text(
            "Выбери действие кнопками меню.",
            reply_markup=main_menu()
        )


async def product_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        pid = int(q.data.split(":")[1])
    except (IndexError, ValueError):
        return

    with db() as c:
        p = c.execute(
            "SELECT * FROM products WHERE id=? AND active=1",
            (pid,)
        ).fetchone()

    if not p:
        await q.message.reply_text("Этот товар больше недоступен.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🛒 Оформить заказ",
            callback_data=f"order:{pid}"
        )],
        [InlineKeyboardButton(
            "⬅️ Назад",
            callback_data=f"back:{p['category']}"
        )]
    ])

    caption = (
        f"📦 {p['name']}\n"
        f"💵 Цена: {p['price']}\n"
        f"📝 {p['description'] or 'Описание пока не добавлено.'}"
    )

    if p["photo"]:
        try:
            await q.message.reply_photo(
                photo=p["photo"],
                caption=caption,
                reply_markup=keyboard
            )
        except Exception:
            log.exception("Не удалось отправить фото товара")
            await q.message.reply_text(
                caption,
                reply_markup=keyboard
            )
    else:
        await q.message.reply_text(
            caption,
            reply_markup=keyboard
        )


async def back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    category = q.data.split(":", 1)[1]
    keyboard = product_keyboard(category)

    await q.message.reply_text(
        f"Выбери товар: {category}" if keyboard else "Товаров пока нет.",
        reply_markup=keyboard
    )


async def order_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        pid = int(q.data.split(":")[1])
    except (IndexError, ValueError):
        return

    with db() as c:
        p = c.execute(
            "SELECT * FROM products WHERE id=? AND active=1",
            (pid,)
        ).fetchone()

    if not p:
        await q.message.reply_text("Товар недоступен.")
        return

    await q.message.reply_text(
        f"🛒 Оформление заказа\n\n"
        f"Товар: {p['name']}\n"
        f"Цена: {p['price']}\n\n"
        "Выбери способ оплаты:",
        reply_markup=payment_keyboard(pid)
    )


async def payment_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        method, pid_text = q.data.split(":")
        pid = int(pid_text)
    except (ValueError, IndexError):
        return

    with db() as c:
        p = c.execute(
            "SELECT * FROM products WHERE id=? AND active=1",
            (pid,)
        ).fetchone()

    if not p:
        await q.message.reply_text("Товар больше недоступен.")
        return

    user = q.from_user
    product_text = f"{p['name']} — {p['price']}"

    if method == "paycard":
        amount = get_ruble_amount(p["price"])

        if not amount:
            await q.message.reply_text(
                "Не удалось определить цену в рублях. "
                "Обратись к владельцу @crmp_feliks."
            )
            return

        with db() as c:
            cur = c.execute(
                """
                INSERT INTO orders(
                    user_id,username,product,status,payment_method
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    user.id,
                    user.username or user.full_name,
                    product_text,
                    "Ожидается чек",
                    "Карта"
                )
            )
            order_id = cur.lastrowid

        context.user_data["proof_order_id"] = order_id

        await q.message.reply_text(
            f"💳 ОПЛАТА КАРТОЙ\n\n"
            f"📦 Товар: {p['name']}\n"
            f"💵 Сумма: {amount} ₽\n\n"
            f"Реквизиты для оплаты:\n{CARD_DETAILS}\n\n"
            "После оплаты отправь сюда фото или файл чека.\n"
            f"Номер заказа: #{order_id}\n"
            "Чек будет отправлен администратору на проверку.\n"
            "Для отмены напиши /cancel."
        )

        await notify_admin(
            context, order_id, p["name"], user, "Карта", p["price"]
        )

    elif method in ("paybc", "paystars"):
        payment_name = "BC" if method == "paybc" else "Звёзды"

        with db() as c:
            cur = c.execute(
                """
                INSERT INTO orders(
                    user_id,username,product,status,payment_method
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    user.id,
                    user.username or user.full_name,
                    product_text,
                    "Ожидает связи с владельцем",
                    payment_name
                )
            )
            order_id = cur.lastrowid

        await q.message.reply_text(
            f"{'💎' if method == 'paybc' else '⭐'} "
            f"ОПЛАТА {payment_name.upper()}\n\n"
            f"📦 Товар: {p['name']}\n"
            f"💵 Цена: {p['price']}\n\n"
            f"Для оформления оплаты свяжись с владельцем: @{OWNER}\n"
            f"Номер заказа: #{order_id}\n"
            "Выдача товара будет после подтверждения оплаты."
        )

        await notify_admin(
            context, order_id, p["name"], user, payment_name, p["price"]
        )


async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = context.user_data.get("proof_order_id")
    message = update.message

    if not order_id or not message:
        return

    if not message.photo and not message.document:
        return

    with db() as c:
        order = c.execute(
            "SELECT * FROM orders WHERE id=? AND user_id=?",
            (order_id, update.effective_user.id)
        ).fetchone()

    if not order or order["status"] != "Ожидается чек":
        context.user_data.pop("proof_order_id", None)
        await message.reply_text(
            "Заказ не найден или чек уже отправлен. "
            "Проверь раздел «Мои заказы»."
        )
        return

    file_id = (
        message.photo[-1].file_id
        if message.photo
        else message.document.file_id
    )

    if not ADMIN_ID:
        await message.reply_text(
            "⚠️ Администратор ещё не настроен. "
            "Сообщи владельцу @crmp_feliks."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Подтвердить оплату",
            callback_data=f"paid:{order_id}"
        )],
        [InlineKeyboardButton(
            "❌ Отклонить чек",
            callback_data=f"reject:{order_id}"
        )]
    ])

    username = order["username"] or "не указан"

    caption = (
        f"🧾 ЧЕК ПО ЗАКАЗУ #{order_id}\n\n"
        f"📦 Товар: {order['product']}\n"
        "💳 Способ: Карта\n"
        f"👤 Покупатель: {username}\n"
        f"🆔 ID: {order['user_id']}\n"
        "📌 Статус: Чек отправлен — проверка\n\n"
        "Проверь поступление денег, затем выбери действие."
    )

    try:
        if message.photo:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=file_id,
                caption=caption,
                reply_markup=keybo
