import os
import sqlite
import logging
import threading
from datetime import datetime

from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler,
    ContextTypes, filters
)

# ---------- НАСТРОЙКИ ----------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CARD_DETAILS = os.getenv("CARD_DETAILS", "Реквизиты уточняйте у администрации")
BC_DETAILS = os.getenv("BC_DETAILS", "Оплата через владельца @crmp_feliks")
DB_PATH = os.getenv("DB_PATH", "donate.db")

CATEGORIES = ["Транспорт", "Скины", "Вирта"]

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ---------- БАЗА ДАННЫХ ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT NOT NULL,
                card_price TEXT DEFAULT '',
                bc_price TEXT DEFAULT '',
                stars_price INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT DEFAULT '',
                product_id INTEGER,
                product_name TEXT NOT NULL,
                payment TEXT NOT NULL,
                amount TEXT DEFAULT '',
                status TEXT DEFAULT 'Ожидает оплаты',
                proof_file_id TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)


def get_product(product_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE id=?",
            (product_id,)
        ).fetchone()


def create_order(user, product, payment, amount):
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO orders
            (user_id, username, product_id, product_name,
             payment, amount, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username or "",
            product["id"],
            product["name"],
            payment,
            str(amount),
            "Ожидает оплаты",
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ))
        return cur.lastrowid


def get_order(order_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE id=?",
            (order_id,)
        ).fetchone()


# ---------- КЛАВИАТУРЫ ----------

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 Каталог товаров", callback_data="catalog")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить товар", callback_data="add_product")],
        [InlineKeyboardButton("📦 Все заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("🛍 Управление товарами", callback_data="manage_products")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="broadcast")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Добро пожаловать в FELIKS RUSSIA DONATE!\n\n"
        "Выберите нужный раздел:",
        reply_markup=main_keyboard()
    )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data.clear()
    await update.message.reply_text(
        "⚙️ Панель администратора",
        reply_markup=admin_keyboard()
    )


# ---------- КАТАЛОГ ----------

async def show_catalog(query):
    keyboard = [
        [InlineKeyboardButton(
            f"📁 {category}",
            callback_data=f"category:{category}"
        )]
        for category in CATEGORIES
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="home")])

    await query.edit_message_text(
        "🛍 Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_products(query, category):
    with db() as conn:
        products = conn.execute("""
            SELECT * FROM products
            WHERE category=? AND active=1
            ORDER BY id DESC
        """, (category,)).fetchall()

    keyboard = []
    for product in products:
        keyboard.append([InlineKeyboardButton(
            f"{product['name']}",
            callback_data=f"product:{product['id']}"
        )])

    keyboard.append([InlineKeyboardButton("⬅️ Категории", callback_data="catalog")])

    text = f"📁 Категория: {category}\n\n"
    if not products:
        text += "Пока товаров нет."
    else:
        text += "Выберите товар:"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_product(query, product_id):
    product = get_product(product_id)

    if not product or not product["active"]:
        await query.edit_message_text("❌ Товар больше недоступен.")
        return

    text = (
        f"🛍 {product['name']}\n\n"
        f"{product['description'] or 'Описание отсутствует.'}\n\n"
        f"💳 Карта: {product['card_price'] or 'Уточнить'}\n"
        f"💎 BC: {product['bc_price'] or 'Уточнить'}\n"
        f"⭐ Stars: {product['stars_price'] or 'Недоступно'}"
    )

    keyboard = [
        [InlineKeyboardButton("💳 Оплатить картой", callback_data=f"pay:card:{product_id}")],
        [InlineKeyboardButton("💎 Оплатить BC", callback_data=f"pay:bc:{product_id}")],
    ]

    if product["stars_price"] > 0:
        keyboard.append([
            InlineKeyboardButton("⭐ Оплатить Stars", callback_data=f"pay:stars:{product_id}")
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"category:{product['category']}")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ---------- ОПЛАТА ----------

async def create_payment(query, context, method, product_id):
    product = get_product(product_id)

    if not product or not product["active"]:
        await query.edit_message_text("❌ Товар недоступен.")
        return

    if method == "card":
        amount = product["card_price"]
        if not amount:
            await query.edit_message_text("❌ Цена картой пока не указана.")
            return

        order_id = create_order(query.from_user, product, "Карта", amount)

        await query.edit_message_text(
            f"💳 **Оплата картой Сбербанка**\n\n"
            f"Товар: {product['name']}\n"
            f"Сумма: {amount}\n\n"
            f"{CARD_DETAILS}\n\n"
            f"После оплаты отправьте сюда фото или файл чека.\n"
            f"Номер заказа: #{order_id}",
            parse_mode="Markdown"
        )

    elif method == "bc":
        amount = product["bc_price"]
        if not amount:
            await query.edit_message_text("❌ Цена BC пока не указана.")
            return

        order_id = create_order(query.from_user, product, "BC", amount)

        await query.edit_message_text(
            f"💎 **Оплата через BC**\n\n"
            f"Товар: {product['name']}\n"
            f"Стоимость: {amount}\n\n"
            f"{BC_DETAILS}\n\n"
            f"После оплаты отправьте подтверждение в этот чат.\n"
            f"Номер заказа: #{order_id}",
            parse_mode="Markdown"
        )

    elif method == "stars":
        stars = int(product["stars_price"])
        if stars <= 0:
            await query.edit_message_text("❌ Оплата Stars недоступна.")
            return

        order_id = create_order(query.from_user, product, "Stars", stars)

        await context.bot.send_invoice(
            chat_id=query.from_user.id,
            title=product["name"][:32],
            description=(product["description"] or "Покупка в FELIKS RUSSIA")[:255],
            payload=f"order_{order_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(product["name"][:32], stars)]
        )

        await query.edit_message_text(
            f"⭐ Счёт на {stars} Stars отправлен.\nЗаказ #{order_id}"
        )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query

    try:
        order_id = int(query.invoice_payload.replace("order_", ""))
        order = get_order(order_id)

        valid = (
            order
            and order["user_id"] == query.from_user.id
            and order["payment"] == "Stars"
            and order["status"] == "Ожидает оплаты"
            and int(order["amount"]) == query.total_amount
            and query.currency == "XTR"
        )
    except Exception:
        valid = False

    if valid:
        await query.answer(ok=True)
    else:
        await query.answer(
            ok=False,
            error_message="Счёт недействителен. Откройте каталог и попробуйте снова."
        )


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    order_id = int(payment.invoice_payload.replace("order_", ""))
    order = get_order(order_id)

    if not order or order["user_id"] != update.effective_user.id:
        await update.message.reply_text("Оплата получена, но заказ нужно проверить через поддержку.")
        return

    with db() as conn:
        conn.execute(
            "UPDATE orders SET status=? WHERE id=?",
            ("Оплачено", order_id)
        )

    await update.message.reply_text(
        f"✅ Оплата Stars прошла успешно!\nЗаказ #{order_id} передан администрации."
    )

    await context.bot.send_message(
        ADMIN_ID,
        f"⭐ Новая оплата Stars!\n"
        f"Заказ: #{order_id}\n"
        f"Покупатель: {update.effective_user.id}\n"
        f"Товар: {order['product_name']}\n"
        f"Сумма: {payment.total_amount} Stars"
    )


# ---------- ЧЕКИ ----------

async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    with db() as conn:
        order = conn.execute("""
            SELECT * FROM orders
            WHERE user_id=? AND status='Ожидает оплаты'
              AND payment IN ('Карта', 'BC')
            ORDER BY id DESC LIMIT 1
        """, (user.id,)).fetchone()

    if not order:
        await update.message.reply_text(
            "❌ У вас нет заказа, ожидающего подтверждения оплаты."
        )
        return

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        kind = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        kind = "document"
    else:
        return

    with db() as conn:
        conn.execute(
            "UPDATE orders SET proof_file_id=? WHERE id=?",
            (f"{kind}:{file_id}", order["id"])
        )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order['id']}")
        ]
    ])

    caption = (
        f"🧾 Новый чек на проверку\n\n"
        f"Заказ: #{order['id']}\n"
        f"Игрок: {user.full_name}\n"
        f"ID: {user.id}\n"
        f"Товар: {order['product_name']}\n"
        f"Оплата: {order['payment']}\n"
        f"Сумма: {order['amount']}"
    )

    if kind == "photo":
        await context.bot.send_photo(
            ADMIN_ID, file_id, caption=caption, reply_markup=keyboard
        )
    else:
        await context.bot.send_document(
            ADMIN_ID, file_id, caption=caption, reply_markup=keyboard
        )

    await update.message.reply_text(
        f"✅ Чек отправлен администрации.\nЗаказ #{order['id']} ожидает проверки."
    )


# ---------- МОИ ЗАКАЗЫ ----------

async def my_orders(query):
    with db() as conn:
        orders = conn.execute("""
            SELECT * FROM orders WHERE user_id=?
            ORDER BY id DESC LIMIT 10
        """, (query.from_user.id,)).fetchall()

    if not orders:
        await query.edit_message_text(
            "📦 У вас пока нет заказов.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
            ])
        )
        return

    text = "📦 Ваши последние заказы:\n\n"
    for order in orders:
        text += (
            f"Заказ #{order['id']}\n"
            f"Товар: {order['product_name']}\n"
            f"Оплата: {order['payment']}\n"
            f"Статус: {order['status']}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
        ])
    )


# ---------- АДМИНКА ----------

async def admin_orders(query):
    if query.from_user.id != ADMIN_ID:
        return

    with db() as conn:
        orders = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 15"
        ).fetchall()

    if not orders:
        await query.edit_message_text("Заказов пока нет.")
        return

    keyboard = []
    for order in orders:
        keyboard.append([InlineKeyboardButton(
            f"#{order['id']} | {order['product_name']} | {order['status']}",
            callback_data=f"order:{order['id']}"
        )])

    keyboard.append([InlineKeyboardButton("⬅️ В админку", callback_data="admin_home")])

    await query.edit_message_text(
        "📦 Последние заказы:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def order_detail(query, order_id):
    if query.from_user.id != ADMIN_ID:
        return

    order = get_order(order_id)
    if not order:
        await query.edit_message_text("Заказ не найден.")
        return

    text = (
        f"📦 Заказ #{order['id']}\n\n"
        f"Игрок: {order['username'] or order['user_id']}\n"
        f"ID: {order['user_id']}\n"
        f"Товар: {order['product_name']}\n"
        f"Оплата: {order['payment']}\n"
        f"Сумма: {order['amount']}\n"
        f"Статус: {order['status']}\n"
        f"Дата: {order['created_at']}"
    )

    keyboard = []
    if order["status"] not in ("Одобрено", "Отклонено"):
        keyboard.append([
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Заказы", callback_data="admin_orders")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def set_order_status(query, context, order_id, status):
    if query.from_user.id != ADMIN_ID:
        return

    order = get_order(order_id)
    if not order:
        await query.edit_message_text("Заказ не найден.")
        return

    if order["status"] in ("Одобрено", "Отклонено"):
        await query.answer("Этот заказ уже обработан.", show_alert=True)
        return

    with db() as conn:
        conn.execute(
            "UPDATE orders SET status=? WHERE id=?",
            (status, order_id)
        )

    await context.bot.send_message(
        order["user_id"],
        f"{'✅' if status == 'Одобрено' else '❌'} "
        f"Ваш заказ #{order_id} получил статус: {status}."
    )

    await query.edit_message_text(
        f"Заказ #{order_id} обработан.\nСтатус: {status}"
    )


# ---------- ДОБАВЛЕНИЕ ТОВАРА ----------

async def add_product_start(query, context):
    if query.from_user.id != ADMIN_ID:
        return

    context.user_data["add_step"] = "name"
    await query.edit_message_text("Введите название товара:")


async def add_product_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    step = context.user_data.get("add_step")
    if not step:
        return

    value = update.message.text.strip()

    if step == "name":
        context.user_data["new_product"] = {"name": value}
        context.user_data["add_step"] = "description"
        await update.message.reply_text("Введите описание товара:")
    elif step == "description":
        context.user_data["new_product"]["description"] = value
        context.user_data["add_step"] = "category"
        keyboard = [[InlineKeyboardButton(c, callback_data=f"newcat:{c}")] for c in CATEGORIES]
        await update.message.reply_text(
            "Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif step == "card":
        context.user_data["new_product"]["card_price"] = value
        context.user_data["add_step"] = "bc"
        await update.message.reply_text("Введите цену BC (или напишите —):")
    elif step == "bc":
        context.user_data["new_product"]["bc_price"] = "" if value == "—" else value
        context.user_data["add_step"] = "stars"
        await update.message.reply_text("Введите цену в Stars числом (0 — отключить):")
    elif step == "stars":
        try:
            stars = int(value)
            if stars < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введите целое число, например 0 или 100.")
            return

        data = context.user_data["new_product"]
        with db() as conn:
            conn.execute("""
                INSERT INTO products
                (name, description, category, card_price, bc_price, stars_price)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                data["name"], data["description"], data["category"],
                data["card_price"], data["bc_price"], stars
            ))

        context.user_data.clear()
        await update.message.reply_text(
            "✅ Товар добавлен!",
            reply_markup=admin_keyboard()
        )


# ---------- УПРАВЛЕНИЕ ТОВАРАМИ ----------

async def manage_products(query):
    if query.from_user.id != ADMIN_ID:
        return

    with db() as conn:
        products = conn.execute(
            "SELECT * FROM products ORDER BY id DESC"
        ).fetchall()

    keyboard = []
    for product in products:
        state = "🟢" if product["active"] else "🔴"
        keyboard.append([InlineKeyboardButton(
            f"{state} {product['name']}",
            callback_data=f"toggle:{product['id']}"
        )])

    keyboard.append([InlineKeyboardButton("⬅️ В админку", callback_data="admin_home")])

    await query.edit_message_text(
        "Нажмите на товар, чтобы скрыть или восстановить его:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def toggle_product(query, product_id):
    if query.from_user.id != ADMIN_ID:
        return

    product = get_product(product_id)
    if not product:
        await query.answer("Товар не найден.")
        return

    new_state = 0 if product["active"] else 1
    with db() as conn:
        conn.execute(
            "UPDATE products SET active=? WHERE id=?",
            (new_state, product_id)
        )

    await query.answer("Статус товара изменён.")
    await manage_products(query)


# ---------- СТАТИСТИКА ----------

async def stats(query):
    if query.from_user.id != ADMIN_ID:
        return

    with db() as conn:
        products = conn.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        orders = conn.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

    await query.edit_message_text(
        f"📊 Статистика\n\n"
        f"🛍 Товаров: {products}\n"
        f"📦 Заказов: {orders}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ В админку", callback_data="admin_home")]
        ])
    )
