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
        "👋 Добро пожаловать в FELIKS RUSSIA DONATE!\n\n"
        "Выберите нужный раздел:",
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
