
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

# ---------- НАСТРОЙКИ ----------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CARD_DETAILS = os.getenv(
    "CARD_DETAILS", "Реквизиты уточняйте у администрации"
)
BC_DETAILS = os.getenv(
    "BC_DETAILS", "Оплата через владельца @crmp_feliks"
)
DB_PATH = os.getenv("DB_PATH", "donate.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

web = Flask(__name__)


@web.route("/")
def health():
    return "FELIKS RUSSIA DONATE is running", 200


def run_web():
    web.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))


# ---------- БАЗА ДАННЫХ ----------

def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT,
                card_price REAL DEFAULT 0,
                bc_price REAL DEFAULT 0,
                stars_price INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                product_id INTEGER,
                product_name TEXT,
                payment TEXT,
                amount REAL,
                status TEXT DEFAULT 'Ожидает оплаты',
                proof_file_id TEXT,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        """)


def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO users (user_id, username)
            VALUES (?, ?)
        """, (user.id, user.username or ""))


def get_product(product_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE id = ?",
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
            product[0],
            product[1],
            payment,
            amount,
            "Ожидает оплаты" if payment == "Stars" else "Ожидает чек",
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ))
        return cur.lastrowid


# ---------- КЛАВИАТУРЫ ----------

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Каталог", callback_data="catalog")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("💬 Поддержка", callback_data="support")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить товар", callback_data="add_product")],
        [InlineKeyboardButton("📦 Заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("🛠 Управление товарами", callback_data="manage_products")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="broadcast")]
    ])


# ---------- КОМАНДЫ ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    await update.message.reply_text(
        "👋 Добро пожаловать в магазин доната FELIKS RUSSIA!\n\n"
        "Выберите нужный раздел:",
        reply_markup=main_keyboard()
    )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "⚙️ Панель администратора",
        reply_markup=admin_keyboard()
    )


# ---------- КАТАЛОГ ----------

async def show_catalog(query):
    keyboard = [
        [InlineKeyboardButton("🚗 Транспорт", callback_data="category:Транспорт")],
        [InlineKeyboardButton("👕 Скины", callback_data="category:Скины")],
        [InlineKeyboardButton("💰 Вирта", callback_data="category:Вирта")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
    ]

    await query.edit_message_text(
        "🛒 Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_products(query, category):
    with db() as conn:
        products = conn.execute("""
            SELECT id, name, card_price, bc_price, stars_price
            FROM products
            WHERE category = ? AND active = 1
        """, (category,)).fetchall()

    keyboard = []

    for p in products:
        keyboard.append([
            InlineKeyboardButton(
                f"{p[1]}",
                callback_data=f"product:{p[0]}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="catalog")
    ])

    await query.edit_message_text(
        f"📦 Категория: {category}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_product(query, product_id):
    product = get_product(product_id)

    if not product or not product[7]:
        await query.answer("Товар недоступен", show_alert=True)
        return

    text = (
        f"📦 {product[1]}\n\n"
        f"📝 {product[2] or 'Описание отсутствует'}\n\n"
        f"💳 Карта: {product[4]} ₽\n"
        f"💎 BC: {product[5]}\n"
        f"⭐ Stars: {product[6]}"
    )

    keyboard = [
        [InlineKeyboardButton("💳 Оплатить картой", callback_data=f"pay:Карта:{product_id}")],
        [InlineKeyboardButton("💎 Оплатить BC", callback_data=f"pay:BC:{product_id}")],
        [InlineKeyboardButton("⭐ Оплатить Stars", callback_data=f"pay:Stars:{product_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"category:{product[3]}")]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ---------- ОПЛАТА ----------

async def create_payment(query, context, method, product_id):
    product = get_product(product_id)

    if not product or not product[7]:
        await query.answer("Товар недоступен", show_alert=True)
        return

    prices = {
        "Карта": product[4],
        "BC": product[5],
        "Stars": product[6]
    }
    amount = prices[method]

    if not amount or amount <= 0:
        await query.answer("Этот способ оплаты недоступен", show_alert=True)
        return

    order_id = create_order(
        query.from_user, product, method, amount
    )

    if method == "Stars":
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=product[1],
            description=product[2] or "Покупка в FELIKS RUSSIA",
            payload=f"order_{order_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(product[1], int(amount))]
        )

        await query.answer()
        return

    details = CARD_DETAILS if method == "Карта" else BC_DETAILS

    await query.message.reply_text(
        f"🧾 Заказ №{order_id}\n\n"
        f"📦 Товар: {product[1]}\n"
        f"💰 Сумма: {amount}\n"
        f"💳 Способ оплаты: {method}\n\n"
        f"Реквизиты:\n{details}\n\n"
        "После оплаты отправьте сюда фото или файл чека."
    )

    await query.answer("Заказ создан")


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query

    try:
        order_id = int(query.invoice_payload.replace("order_", ""))
    except ValueError:
        await query.answer(ok=False, error_message="Неверный заказ")
        return

    with db() as conn:
        order = conn.execute(
            "SELECT user_id, status FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

    if not order or order[0] != query.from_user.id:
        await query.answer(ok=False, error_message="Заказ не найден")
        return

    if order[1] != "Ожидает оплаты":
        await query.answer(ok=False, error_message="Заказ уже обработан")
        return

    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment

    try:
        order_id = int(payment.invoice_payload.replace("order_", ""))
    except ValueError:
        await update.message.reply_text("Не удалось определить заказ. Обратитесь в поддержку.")
        return

    with db() as conn:
        order = conn.execute(
            "SELECT user_id, status FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if not order or order[0] != update.effective_user.id:
            await update.message.reply_text("Заказ не найден. Обратитесь в поддержку.")
            return

        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?",
            ("Оплачен", order_id)
        )

    await update.message.reply_text(
        f"✅ Оплата заказа №{order_id} прошла успешно!"
    )

    if ADMIN_ID:
        await context.bot.send_message(
            ADMIN_ID,
            f"⭐ Новая оплата Stars!\nЗаказ №{order_id}\n"
            f"Пользователь: {update.effective_user.id}"
        )


# ---------- ЧЕКИ ----------

async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        return

    with db() as conn:
        order = conn.execute("""
            SELECT id FROM orders
            WHERE user_id = ? AND status = 'Ожидает чек'
            ORDER BY id DESC LIMIT 1
        """, (user.id,)).fetchone()

        if not order:
            await update.message.reply_text("У вас нет заказа, ожидающего чек.")
            return

        order_id = order[0]

        conn.execute("""
            UPDATE orders
            SET proof_file_id = ?, status = 'Чек отправлен'
            WHERE id = ?
        """, (file_id, order_id))

    await update.message.reply_text(
        f"✅ Чек по заказу №{order_id} отправлен администрации на проверку."
    )

    if ADMIN_ID:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")
        ]])

        caption = (
            f"🧾 Чек по заказу №{order_id}\n"
            f"Пользователь: {user.id} (@{user.username or 'нет'})"
        )

        if update.message.photo:
            await context.bot.send_photo(
                ADMIN_ID, file_id, caption=caption,
                reply_markup=keyboard
            )
        else:
            await context.bot.send_document(
                ADMIN_ID, file_id, caption=caption,
                reply_markup=keyboard
            )


# ---------- ЗАКАЗЫ ----------

async def my_orders(query):
    with db() as conn:
        orders = conn.execute("""
            SELECT id, product_name, amount, payment, status, created_at
            FROM orders WHERE user_id = ?
            ORDER BY id DESC LIMIT 15
        """, (query.from_user.id,)).fetchall()

    if not orders:
        await query.edit_message_text(
            "У вас пока нет заказов.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
            ])
        )
        return

    text = "📦 Ваши заказы:\n\n"

    for o in orders:
        text += (
            f"Заказ №{o[0]}\n"
            f"Товар: {o[1]}\n"
            f"Сумма: {o[2]}\n"
            f"Оплата: {o[3]}\n"
            f"Статус: {o[4]}\n"
            f"Дата: {o[5]}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
        ])
    )


async def admin_orders(query):
    with db() as conn:
        orders = conn.execute("""
            SELECT id, user_id, product_name, amount, status
            FROM orders ORDER BY id DESC LIMIT 20
        """).fetchall()

    if not orders:
        await query.edit_message_text("Заказов пока нет.")
        return

    keyboard = []

    for o in orders:
        keyboard.append([
            InlineKeyboardButton(
                f"№{o[0]} | {o[2]} | {o[4]}",
                callback_data=f"order:{o[0]}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="admin_home")
    ])

    await query.edit_message_text(
        "📦 Последние заказы:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def order_detail(query, order_id):
    with db() as conn:
        order = conn.execute("""
            SELECT id, user_id, username, product_name,
                   payment, amount, status, created_at
            FROM orders WHERE id = ?
        """, (order_id,)).fetchone()

    if not order:
        await query.answer("Заказ не найден")
        return

    text = (
        f"Заказ №{order[0]}\n"
        f"Пользователь: {order[1]} (@{order[2]})\n"
        f"Товар: {order[3]}\n"
        f"Оплата: {order[4]}\n"
        f"Сумма: {order[5]}\n"
        f"Статус: {order[6]}\n"
        f"Дата: {order[7]}"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order_id}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")],
        [InlineKeyboardButton("⬅️ К заказам", callback_data="admin_orders")]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def set_order_status(query, context, order_id, status):
    with db() as conn:
        order = conn.execute(
            "SELECT user_id, status FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if not order:
            await query.answer("Заказ не найден", show_alert=True)
            return

        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?",
            (status, order_id)
        )

    await context.bot.send_message(
        order[0],
        f"📦 Статус заказа №{order_id}: {status}"
    )

    await query.answer("Статус изменён")
    await query.edit_message_text(
        f"Заказ №{order_id}: {status}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ К заказам", callback_data="admin_orders")]
        ])
    )


# ---------- ДОБАВЛЕНИЕ ТОВАРА ----------

async def add_product_start(query, context):
    context.user_data["add_product"] = {}
    context.user_data["add_step"] = "name"

    await query.edit_message_text("Введите название товара:")


async def add_product_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    step = context.user_data.get("add_step")
    if not step:
        return

    data = context.user_data.setdefault("add_product", {})
    value = update.message.text.strip()

    if step == "name":
        data["name"] = value
        context.user_data["add_step"] = "description"
        await update.message.reply_text("Введите описание товара:")

    elif step == "description":
        data["description"] = value
        context.user_data["add_step"] = "category"

        keyboard = [
            [InlineKeyboardButton("Транспорт", callback_data="newcat:Транспорт")],
            [InlineKeyboardButton("Скины", callback_data="newcat:Скины")],
            [InlineKeyboardButton("Вирта", callback_data="newcat:Вирта")]
        ]

        await update.message.reply_text(
            "Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step in ("card", "bc", "stars"):
        try:
            number = float(value.replace(",", "."))
            if number < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введите корректное число.")
            return

        data[step] = int(number) if step == "stars" else number

        next_steps = {
            "card": ("bc", "Введите цену в BC:"),
            "bc": ("stars", "Введите цену в Telegram Stars:")
        }

        if step in next_steps:
            next_step, prompt = next_steps[step]
            context.user_data["add_step"] = next_step
            await update.message.reply_text(prompt)
            return

        with db() as conn:
            conn.execute("""
                INSERT INTO products
                (name, description, category, card_price,
                 bc_price, stars_price, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (
                data["name"], data["description"], data["category"],
                data["card"], data["bc"], data["stars"]
            ))

        context.user_data.pop("add_step", None)
        context.user_data.pop("add_product", None)

        await update.message.reply_text("✅ Товар добавлен!")


# ---------- УПРАВЛЕНИЕ ТОВАРАМИ ----------

async def manage_products(query):
    with db() as conn:
        products = conn.execute("""
            SELECT id, name, active FROM products
            ORDER BY id DESC LIMIT 30
        """).fetchall()

    keyboard = []

    for p in products:
        state = "🟢" if p[2] else "🔴"
        keyboard.append([
            InlineKeyboardButton(
                f"{state} {p[1]}",
                callback_data=f"toggle:{p[0]}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="admin_home")
    ])

    await query.edit_message_text(
        "Нажмите на товар, чтобы изменить его доступность:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def toggle_product(query, product_id):
    with db() as conn:
        conn.execute("""
            UPDATE products SET active = CASE
                WHEN active = 1 THEN 0 ELSE 1 END
            WHERE id = ?
        """, (product_id,))

    await query.answer("Доступность изменена")
    await manage_products(query)


# ---------- СТАТИСТИКА ----------

async def stats(query):
    with db() as conn:
        products = conn.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        orders = conn.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

        paid = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'Оплачен'"
        ).fetchone()[0]

        users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        revenue = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'Оплачен'"
        ).fetchone()[0]

    await query.edit_message_text(
        "📊 Статистика FELIKS RUSSIA\n\n"
        f"👥 Пользователей: {users}\n"
        f"📦 Товаров: {products}\n"
        f"🧾 Всего заказов: {orders}\n"
        f"✅ Оплачено заказов: {paid}\n"
        f"💰 Сумма оплаченных заказов: {revenue}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_home")]
