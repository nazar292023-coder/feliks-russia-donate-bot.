import os
import sqlite3
import logging
import threading
from datetime import datetime

from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler,
    ContextTypes, filters
)

# =============== НАСТРОЙКИ ===============

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CARD_DETAILS = os.getenv("CARD_DETAILS", "Реквизиты карты не настроены.")
BC_DETAILS = os.getenv("BC_DETAILS", "Реквизиты BC не настроены.")
SUPPORT_TEXT = os.getenv(
    "SUPPORT_TEXT",
    "По вопросам оплаты обратитесь к администратору."
)

DB_PATH = os.getenv("DB_PATH", "donate.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# =============== FLASK ===============

web = Flask(__name__)

@web.route("/")
def health():
    return "FELIKS RUSSIA DONATE: OK", 200

def run_web():
    web.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000"))
    )

# =============== БАЗА ДАННЫХ ===============

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT DEFAULT '',
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'Вирта',
                card_price REAL DEFAULT 0,
                bc_price REAL DEFAULT 0,
                stars_price INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER DEFAULT 0,
                product_id INTEGER DEFAULT 0,
                product_name TEXT DEFAULT '',
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT '',
                payment_method TEXT DEFAULT '',
                status TEXT DEFAULT 'Ожидает оплаты',
                receipt_file_id TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)

        # Добавление недостающих столбцов в старую базу
        migrations = {
            "users": {
                "username": "TEXT DEFAULT ''",
                "first_name": "TEXT DEFAULT ''",
                "created_at": "TEXT DEFAULT ''"
            },
            "products": {
                "name": "TEXT DEFAULT ''",
                "description": "TEXT DEFAULT ''",
                "category": "TEXT DEFAULT 'Вирта'",
                "card_price": "REAL DEFAULT 0",
                "bc_price": "REAL DEFAULT 0",
                "stars_price": "INTEGER DEFAULT 0",
                "active": "INTEGER DEFAULT 1"
            },
            "orders": {
                "user_id": "INTEGER DEFAULT 0",
                "product_id": "INTEGER DEFAULT 0",
                "product_name": "TEXT DEFAULT ''",
                "amount": "REAL DEFAULT 0",
                "currency": "TEXT DEFAULT ''",
                "payment_method": "TEXT DEFAULT ''",
                "status": "TEXT DEFAULT 'Ожидает оплаты'",
                "receipt_file_id": "TEXT DEFAULT ''",
                "created_at": "TEXT DEFAULT ''"
            }
        }

        for table, fields in migrations.items():
            existing = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})")
            }

            for field, definition in fields.items():
                if field not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {field} {definition}"
                    )

        conn.commit()

def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT INTO users(user_id, username, first_name, created_at)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

def get_product(product_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE id=?",
            (product_id,)
        ).fetchone()

def get_order(order_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE id=?",
            (order_id,)
        ).fetchone()

def create_order(user_id, product, method, amount, currency, status):
    with db() as conn:
        cursor = conn.execute("""
            INSERT INTO orders(
                user_id, product_id, product_name, amount,
                currency, payment_method, status, created_at
            )
            VALUES(?,?,?,?,?,?,?,?)
        """, (
            user_id,
            product["id"],
            product["name"],
            amount,
            currency,
            method,
            status,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        return cursor.lastrowid

# =============== КЛАВИАТУРЫ ===============

def home_keyboard(user_id):
    buttons = [
        [InlineKeyboardButton("🛒 Каталог", callback_data="catalog")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")]
    ]

    if user_id == ADMIN_ID and ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(
                "⚙️ Админ-панель",
                callback_data="admin_home"
            )
        ])

    return InlineKeyboardMarkup(buttons)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("➕ Добавить товар", callback_data="add_product")],
        [InlineKeyboardButton("🛍 Товары", callback_data="manage_products")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="broadcast")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="home")]
    ])

def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ В меню", callback_data="home")]
    ])

# =============== СТАРТ ===============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    await update.message.reply_text(
        "👋 Добро пожаловать в FELIKS RUSSIA | DONATE!\n\n"
        "🛒 Здесь ты можешь приобрести товары для своего игрового аккаунта.\n\n"
        "📦 В нашем магазине доступны:\n\n"
        "👕 Скины\n"
        "🚗 Транспорт\n"
        "💰 Вирты\n\n"
        "✨ Выбирай нужную категорию и оформляй покупку!\n\n"
        "❤️ Спасибо, что выбираешь FELIKS RUSSIA!",
        reply_markup=home_keyboard(update.effective_user.id)
    )
        reply_markup=home_keyboard(update.effective_user.id)
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "⚙️ Админ-панель",
        reply_markup=admin_keyboard()
    )

# =============== КАТАЛОГ ===============

async def show_catalog(query):
    with db() as conn:
        categories = conn.execute("""
            SELECT DISTINCT category
            FROM products
            WHERE active=1
            ORDER BY category
        """).fetchall()

    if not categories:
        await query.edit_message_text(
            "🛒 Каталог пока пуст.",
            reply_markup=back_keyboard()
        )
        return

    buttons = [
        [InlineKeyboardButton(
            row["category"],
            callback_data=f"cat:{row['category']}"
        )]
        for row in categories
    ]

    buttons.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="home")
    ])

    await query.edit_message_text(
        "🛒 Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_category(query, category):
    with db() as conn:
        products = conn.execute("""
            SELECT * FROM products
            WHERE active=1 AND category=?
            ORDER BY id DESC
        """, (category,)).fetchall()

    if not products:
        await query.edit_message_text(
            "В этой категории пока нет товаров.",
            reply_markup=back_keyboard()
        )
        return

    buttons = []

    for product in products:
        buttons.append([
            InlineKeyboardButton(
                f"{product['name']} · {product['card_price']:g} ₽",
                callback_data=f"product:{product['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Категории",
            callback_data="catalog"
        )
    ])

    await query.edit_message_text(
        f"📂 {category}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_product(query, product_id):
    product = get_product(product_id)

    if not product or not product["active"]:
        await query.edit_message_text(
            "Товар недоступен.",
            reply_markup=back_keyboard()
        )
        return

    text = (
        f"🛍 {product['name']}\n\n"
        f"{product['description']}\n\n"
        f"💳 Карта: {product['card_price']:g} ₽\n"
        f"🪙 BC: {product['bc_price']:g}\n"
        f"⭐ Stars: {product['stars_price']}"
    )

    buttons = [
        [InlineKeyboardButton(
            "💳 Оплатить картой",
            callback_data=f"pay:card:{product_id}"
        )],
        [InlineKeyboardButton(
            "🪙 Оплатить BC",
            callback_data=f"pay:bc:{product_id}"
        )],
        [InlineKeyboardButton(
            "⭐ Оплатить Stars",
            callback_data=f"pay:stars:{product_id}"
        )],
        [InlineKeyboardButton(
            "⬅️ Назад",
            callback_data=f"cat:{product['category']}"
        )]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# =============== ЗАКАЗЫ ===============

async def my_orders(query, user_id):
    with db() as conn:
        orders = conn.execute("""
            SELECT * FROM orders
            WHERE user_id=?
            ORDER BY id DESC LIMIT 10
        """, (user_id,)).fetchall()

    if not orders:
        await query.edit_message_text(
            "У вас пока нет заказов.",
            reply_markup=back_keyboard()
        )
        return

    lines = ["📦 Ваши заказы:\n"]

    for order in orders:
        lines.append(
            f"№{order['id']} — {order['product_name']}\n"
            f"{order['amount']:g} {order['currency']}\n"
            f"Статус: {order['status']}\n"
        )

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=back_keyboard()
    )

# =============== ОПЛАТА ===============

async def create_payment(query, context, method, product_id):
    product = get_product(product_id)

    if not product or not product["active"]:
        await query.edit_message_text("Товар недоступен.")
        return

    if method == "card":
        amount = float(product["card_price"])

        if amount <= 0:
            await query.edit_message_text("Цена картой не настроена.")
            return

        order_id = create_order(
            query.from_user.id,
            product,
            "Карта",
            amount,
            "RUB",
            "Ожидает чек"
        )

        context.user_data["awaiting_receipt_order"] = order_id

        await query.edit_message_text(
            f"💳 Заказ №{order_id}\n\n"
            f"Товар: {product['name']}\n"
            f"Сумма: {amount:g} ₽\n\n"
            f"Реквизиты:\n{CARD_DETAILS}\n\n"
            "После оплаты отправьте фото или файл чека."
        )

    elif method == "bc":
        amount = float(product["bc_price"])

        if amount <= 0:
            await query.edit_message_text("Цена BC не настроена.")
            return

        order_id = create_order(
            query.from_user.id,
            product,
            "BC",
            amount,
            "BC",
            "Ожидает чек"
        )

        context.user_data["awaiting_receipt_order"] = order_id

        await query.edit_message_text(
            f"🪙 Заказ №{order_id}\n\n"
            f"Товар: {product['name']}\n"
            f"Сумма: {amount:g} BC\n\n"
            f"Информация для оплаты:\n{BC_DETAILS}\n\n"
            "После оплаты отправьте фото или файл чека."
        )

    elif method == "stars":
        amount = int(product["stars_price"])

        if amount <= 0:
            await query.edit_message_text("Цена Stars не настроена.")
            return

        order_id = create_order(
            query.from_user.id,
            product,
            "Telegram Stars",
            amount,
            "XTR",
            "Ожидает оплаты"
        )

        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=product["name"][:32],
            description=f"Заказ FELIKS RUSSIA №{order_id}",
            payload=f"feliks_order:{order_id}",
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(product["name"][:32], amount)
            ]
        )

# =============== TELEGRAM STARS ===============

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query

    try:
        order_id = int(q.invoice_payload.split(":")[1])
        order = get_order(order_id)

        valid = (
            order is not None
            and order["user_id"] == q.from_user.id
            and order["payment_method"] == "Telegram Stars"
            and order["status"] == "Ожидает оплаты"
            and q.currency == "XTR"
            and int(order["amount"]) == q.total_amount
        )
    except (ValueError, IndexError, TypeError):
        valid = False

    if valid:
        await q.answer(ok=True)
    else:
        await q.answer(
            ok=False,
            error_message="Заказ не найден или сумма не совпадает."
        )

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment

    try:
        order_id = int(payment.invoice_payload.split(":")[1])
    except (ValueError, IndexError):
        return

    order = get_order(order_id)

    if not order:
        return

    if (
        order["user_id"] != update.effective_user.id
        or order["payment_method"] != "Telegram Stars"
        or order["status"] != "Ожидает оплаты"
        or payment.currency != "XTR"
        or int(order["amount"]) != payment.total_amount
    ):
        logger.warning("Payment validation failed for order %s", order_id)
        return

    with db() as conn:
        conn.execute(
            "UPDATE orders SET status=? WHERE id=?",
            ("Оплачено", order_id)
        )

    await update.message.reply_text(
        f"✅ Заказ №{order_id} успешно оплачен через Stars!"
    )

# =============== ЧЕКИ ===============

async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = context.user_data.get("awaiting_receipt_order")

    if not order_id:
        return

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("Отправьте фото или файл чека.")
        return

    order = get_order(order_id)

    if (
        not order
        or order["user_id"] != update.effective_user.id
        or order["status"] != "Ожидает чек"
    ):
        context.user_data.pop("awaiting_receipt_order", None)
        await update.message.reply_text("Этот заказ уже нельзя отправить на проверку.")
        return

    with db() as conn:
        conn.execute("""
            UPDATE orders
            SET receipt_file_id=?, status=?
            WHERE id=?
        """, (file_id, "Чек отправлен", order_id))

    context.user_data.pop("awaiting_receipt_order", None)

    await update.message.reply_text(
        f"📨 Чек по заказу №{order_id} отправлен на проверку."
    )

    if ADMIN_ID:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"approve:{order_id}"
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject:{order_id}"
            )
        ]])

        caption = (
            f"🧾 Чек на проверку\n"
            f"Заказ №{order_id}\n"
            f"Пользователь: {order['user_id']}\n"
            f"Товар: {order['product_name']}\n"
            f"Сумма: {order['amount']:g} {order['currency']}"
        )

        try:
            if update.message.photo:
                await context.bot.send_photo(
                    ADMIN_ID,
                    file_id,
                    caption=caption,
                    reply_markup=keyboard
                )
            else:
                await context.bot.send_document(
                    ADMIN_ID,
                    file_id,
                    caption=caption,
                    reply_markup=keyboard
                )
        except Exception:
            logger.exception("Не удалось отправить чек администратору")

# =============== АДМИН-ПАНЕЛЬ ===============

async def admin_orders(query):
    with db() as conn:
        orders = conn.execute("""
            SELECT * FROM orders
            WHERE status='Чек отправлен'
            ORDER BY id DESC LIMIT 20
        """).fetchall()

    if not orders:
        await query.edit_message_text(
            "Нет чеков на проверке.",
            reply_markup=admin_keyboard()
        )
        return

    buttons = []

    for order in orders:
        buttons.append([
            InlineKeyboardButton(
                f"№{order['id']} — {order['product_name']}",
                callback_data=f"order:{order['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="admin_home")
    ])

    await query.edit_message_text(
        "📦 Заказы на проверке:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def order_detail(query, order_id):
    order = get_order(order_id)

    if not order:
        await query.edit_message_text(
            "Заказ не найден.",
            reply_markup=admin_keyboard()
        )
        return

    text = (
        f"Заказ №{order['id']}\n"
        f"Товар: {order['product_name']}\n"
        f"Пользователь: {order['user_id']}\n"
        f"Сумма: {order['amount']:g} {order['currency']}\n"
        f"Способ: {order['payment_method']}\n"
        f"Статус: {order['status']}"
    )

    buttons = []

    if order["status"] == "Чек отправлен":
        buttons.append([
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"approve:{order_id}"
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject:{order_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ К заказам", callback_data="admin_orders")
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def set_order_status(query, context, order_id, status):
    order = get_order(order_id)

    if not order or order["status"] != "Чек отправлен":
        await query.answer(
            "Чек уже обработан или заказ не найден.",
            show_alert=True
        )
        r
        return

    if status not in ("Оплачен", "Отклонён"):
        await query.answer("Неизвестный статус.", show_alert=True)
        return

    with db() as conn:
        conn.execute(
            "UPDATE orders SET status=? WHERE id=?",
            (status, order_id)
        )

    await query.answer("Статус заказа обновлён.")

    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=(
                f"📦 Заказ №{order_id}\n"
                f"Товар: {order['product_name']}\n"
                f"Статус: {status}"
            )
        )
    except Exception:
        logger.exception("Не удалось уведомить пользователя")

    await query.edit_message_text(
        f"✅ Заказ №{order_id}\n"
        f"Новый статус: {status}",
        reply_markup=admin_keyboard()
    )


# =============== УПРАВЛЕНИЕ ТОВАРАМИ ===============

async def manage_products(query):
    with db() as conn:
        products = conn.execute(
            "SELECT * FROM products ORDER BY id DESC LIMIT 30"
        ).fetchall()

    if not products:
        await query.edit_message_text(
            "Товаров пока нет.",
            reply_markup=admin_keyboard()
        )
        return

    buttons = []

    for product in products:
        state = "🟢" if product["active"] else "🔴"
        buttons.append([
            InlineKeyboardButton(
                f"{state} {product['name']}",
                callback_data=f"toggle:{product['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="admin_home")
    ])

    await query.edit_message_text(
        "🛍 Управление товарами:\n"
        "Нажми на товар, чтобы включить или отключить его.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def toggle_product(query, product_id):
    product = get_product(product_id)

    if not product:
        await query.answer("Товар не найден.", show_alert=True)
        return

    new_status = 0 if product["active"] else 1

    with db() as conn:
        conn.execute(
            "UPDATE products SET active=? WHERE id=?",
            (new_status, product_id)
        )

    await query.answer(
        "Товар включён." if new_status else "Товар отключён."
    )
    await manage_products(query)


# =============== ДОБАВЛЕНИЕ ТОВАРА ===============

async def add_product_start(query, context):
    context.user_data["adding_product"] = True

    await query.edit_message_text(
        "➕ Добавление товара\n\n"
        "Отправь данные одной строкой через символ | :\n\n"
        "Категория | Название | Описание | Цена карты | Цена BC | Цена Stars\n\n"
        "Пример:\n"
        "Вирта | 100 BC | Игровая валюта | 100 | 90 | 50\n\n"
        "Для отмены напиши /cancel"
    )


async def add_product_message(update, context):
    if not context.user_data.get("adding_product"):
        return

    if update.effective_user.id != ADMIN_ID:
        return

    if update.message.text.strip() == "/cancel":
        context.user_data.pop("adding_product", None)
        await update.message.reply_text("Добавление товара отменено.")
        return

    parts = [part.strip() for part in update.message.text.split("|")]

    if len(parts) != 6:
        await update.message.reply_text(
            "❌ Нужно указать ровно 6 полей через |.\n"
            "Попробуй ещё раз или напиши /cancel."
        )
        return

    category, name, description, card, bc, stars = parts

    try:
        card_price = float(card.replace(",", "."))
        bc_price = float(bc.replace(",", "."))
        stars_price = int(stars)

        if card_price < 0 or bc_price < 0 or stars_price < 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ Цены должны быть неотрицательными числами."
        )
        return

    with db() as conn:
        conn.execute("""
            INSERT INTO products(
                name, description, category,
                card_price, bc_price, stars_price, active
            )
            VALUES(?,?,?,?,?,?,1)
        """, (
            name,
            description,
            category,
            card_price,
            bc_price,
            stars_price
        ))

    context.user_data.pop("adding_product", None)

    await update.message.reply_text(
        f"✅ Товар добавлен!\n\n"
        f"Название: {name}\n"
        f"Категория: {category}\n"
        f"Карта: {card_price:g} ₽\n"
        f"BC: {bc_price:g}\n"
        f"Stars: {stars_price}",
        reply_markup=admin_keyboard()
    )


# =============== СТАТИСТИКА ===============

async def show_stats(query):
    with db() as conn:
        users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        products = conn.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        active_products = conn.execute(
            "SELECT COUNT(*) FROM products WHERE active=1"
        ).fetchone()[0]

        orders = conn.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

        pending = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='Чек отправлен'"
        ).fetchone()[0]

        paid = conn.execute("""
            SELECT currency, SUM(amount) AS total
            FROM orders
            WHERE status='Оплачен' OR status='Оплачено'
            GROUP BY currency
        """).fetchall()

    revenue = "\n".join(
        f"• {row['total']:g} {row['currency']}"
        for row in paid
    ) or "Пока нет оплат."

    await query.edit_message_text(
        "📊 Статистика FELIKS RUSSIA DONATE\n\n"
        f"👥 Пользователей: {users}\n"
        f"🛍 Всего товаров: {products}\n"
        f"🟢 Активных товаров: {active_products}\n"
        f"📦 Всего заказов: {orders}\n"
        f"🧾 Чеков на проверке: {pending}\n\n"
        f"💰 Подтверждённые оплаты:\n{revenue}",
        reply_markup=admin_keyboard()
    )


# =============== РАССЫЛКА ===============

async def broadcast_start(query, context):
    context.user_data["broadcast_mode"] = True

    await query.edit_message_text(
        "📢 Отправь текст рассылки.\n\n"
        "Он будет отправлен пользователям бота.\n"
        "Для отмены напиши /cancel."
    )


async def broadcast_message(update, context):
    if not context.user_data.get("broadcast_mode"):
        return

    if update.effective_user.id != ADMIN_ID:
        return

    message_text = update.message.text.strip()

    if message_text == "/cancel":
        context.user_data.pop("broadcast_mode", None)
        await update.message.reply_text("Рассылка отменена.")
        return

    if not message_text:
        await update.message.reply_text("Текст рассылки пуст.")
        return

    context.user_data.pop("broadcast_mode", None)

    with db() as conn:
        users = conn.execute(
            "SELECT user_id FROM users"
        ).fetchall()

    sent = 0
    failed = 0

    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=message_text
            )
            sent += 1
        except Exception:
            failed += 1
            logger.exception(
                "Ошибка рассылки пользователю %s",
                user["user_id"]
            )

    await update.message.reply_text(
        "📢 Рассылка завершена.\n\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Не доставлено: {failed}",
        reply_markup=admin_keyboard()
    )


# =============== ОБРАБОТКА КНОПОК ===============

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith((
        "admin_", "add_product", "manage_products",
        "stats", "broadcast", "approve:",
        "reject:", "order:", "toggle:"
    )):
        if query.from_user.id != ADMIN_ID or not ADMIN_ID:
            await query.answer("Нет доступа.", show_alert=True)
            return

    await query.answer()

    if data == "home":
        await query.edit_message_text(
            "👋 FELIKS RUSSIA DONATE\n\nВыберите раздел:",
            reply_markup=home_keyboard(query.from_user.id)
        )

    elif data == "catalog":
        await show_catalog(query)

    elif data.startswith("cat:"):
        category = data.split(":", 1)[1]
        await show_category(query, category)

    elif data.startswith("product:"):
        product_id = int(data.split(":", 1)[1])
        await show_product(query, product_id)

    elif data.startswith("pay:"):
        _, method, product_id = data.split(":", 2)
        await create_payment(
            query, context, method, int(product_id)
        )

    elif data == "my_orders":
        await my_orders(query, query.from_user.id)

    elif data == "support":
        await query.edit_message_text(
            f"🆘 Поддержка\n\n{SUPPORT_TEXT}",
            reply_markup=back_keyboard()
        )

    elif data == "admin_home":
        await query.edit_message_text(
            "⚙️ Админ-панель",
            reply_markup=admin_keyboard()
        )

    elif data == "admin_orders":
        await admin_orders(query)

    elif data.startswith("order:"):
        order_id = int(data.split(":", 1)[1])
        await order_detail(query, order_id)

    elif data.startswith("approve:"):
        order_id = int(data.split(":", 1)[1])
        await set_order_status(
            query, context, order_id, "Оплачен"
        )

    elif data.startswith("reject:"):
        order_id = int(data.split(":", 1)[1])
        await set_order_status(
            query, context, order_id, "Отклонён"
        )

    elif data == "add_product":
        await add_product_start(query, context)

    elif data == "manage_products":
        await manage_products(query)

    elif data.startswith("toggle:"):
        product_id = int(data.split(":", 1)[1])
        await toggle_product(query, product_id)

    elif data == "stats":
        await show_stats(query)

    elif data == "broadcast":
        await broadcast_start(query, context)


# =============== ОШИБКИ ===============

async def error_handler(update, context):
    logger.exception(
        "Ошибка при обработке обновления",
        exc_info=context.error
    )


# =============== ЗАПУСК ===============

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная BOT_TOKEN.")

    if not ADMIN_ID:
        raise RuntimeError("Не задана переменная ADMIN_ID.")

    init_db()

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(
        CommandHandler("cancel", cancel_command)
    )

    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    application.add_handler(
        PreCheckoutQueryHandler(precheckout)
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment
        )
    )

    application.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
            receipt_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            add_product_message,
            block=False
        ),
        group=1
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            broadcast_message,
            block=False
        ),
        group=2
    )

    application.add_error_handler(error_handler)

    logger.info("FELIKS RUSSIA DONATE запущен.")

    application.run_polling(drop_pending_updates=False)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("adding_product", None)
    context.user_data.pop("broadcast_mode", None)
    context.user_data.pop("awaiting_receipt_order", None)

    await update.message.reply_text("Действие отменено.")


if __name__ == "__main__":
    main()

