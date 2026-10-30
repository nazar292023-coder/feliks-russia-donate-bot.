import os
import sqlite3
import logging
from datetime import datetime
from threading import Thread

from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler,
    ContextTypes, filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CARD_DETAILS = os.getenv("CARD_DETAILS", "Реквизиты уточняются у администратора.")
BC_DETAILS = os.getenv("BC_DETAILS", "Уточните оплату BC у администратора.")
DB_PATH = os.getenv("DB_PATH", "donate.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

web = Flask(__name__)


@web.route("/")
def home():
    return "FELIKS RUSSIA DONATE BOT ONLINE"


def keep_web_alive():
    web.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                price TEXT DEFAULT '',
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
                payment_method TEXT NOT NULL,
                amount TEXT DEFAULT '',
                status TEXT DEFAULT 'Ожидает оплаты',
                proof_file_id TEXT DEFAULT '',
                proof_type TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        columns = {
            r["name"] for r in conn.execute(
                "PRAGMA table_info(products)"
            ).fetchall()
        }
        if "active" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN active INTEGER DEFAULT 1"
            )

        columns = {
            r["name"] for r in conn.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        }
        migrations = {
            "username": "TEXT DEFAULT ''",
            "product_id": "INTEGER",
            "payment_method": "TEXT DEFAULT ''",
            "amount": "TEXT DEFAULT ''",
            "status": "TEXT DEFAULT 'Ожидает оплаты'",
            "proof_file_id": "TEXT DEFAULT ''",
            "proof_type": "TEXT DEFAULT ''",
            "created_at": "TEXT DEFAULT ''"
        }

        for name, definition in migrations.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE orders ADD COLUMN {name} {definition}"
                )


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def get_product(product_id, active_only=True):
    with db() as conn:
        sql = "SELECT * FROM products WHERE id=?"
        if active_only:
            sql += " AND active=1"
        return conn.execute(sql, (product_id,)).fetchone()


def create_order(user, product, method, amount):
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO orders
            (user_id, username, product_id, product_name,
             payment_method, amount, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username or user.first_name,
            product["id"],
            product["name"],
            method,
            str(amount),
            "Ожидает оплаты",
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ))
        return cur.lastrowid


# ================= КЛАВИАТУРЫ =================

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Каталог товаров", callback_data="catalog")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="myorders")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ])


def category_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Транспорт", callback_data="cat:Транспорт")],
        [InlineKeyboardButton("🎨 Скины", callback_data="cat:Скины")],
        [InlineKeyboardButton("💰 Вирта", callback_data="cat:Вирта")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить товар", callback_data="admin:add")],
        [InlineKeyboardButton("📋 Управление товарами", callback_data="admin:products")],
        [InlineKeyboardButton("📦 Управление заказами", callback_data="admin:orders")]
    ])


def product_manage_keyboard(pid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Название", callback_data=f"edit:{pid}:name")],
        [InlineKeyboardButton("✏️ Описание", callback_data=f"edit:{pid}:description")],
        [InlineKeyboardButton("💳 Цена", callback_data=f"edit:{pid}:price")],
        [InlineKeyboardButton("💰 Цена BC", callback_data=f"edit:{pid}:bc_price")],
        [InlineKeyboardButton("⭐ Цена Stars", callback_data=f"edit:{pid}:stars_price")],
        [InlineKeyboardButton("📂 Категория", callback_data=f"editcat:{pid}")],
        [InlineKeyboardButton("🗑 Удалить товар", callback_data=f"delete:{pid}")],
        [InlineKeyboardButton("⬅️ К товарам", callback_data="admin:products")]
    ])


def order_keyboard(order):
    rows = []

    if order["proof_file_id"]:
        rows.append([
            InlineKeyboardButton(
                "🧾 Посмотреть чек",
                callback_data=f"proof:{order['id']}"
            )
        ])

    if order["status"] not in ("Оплачено", "Отклонено"):
        rows.append([
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"order:paid:{order['id']}"
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"order:reject:{order['id']}"
            )
        ])

    rows.append([
        InlineKeyboardButton("⬅️ К заказам", callback_data="admin:orders")
    ])

    return InlineKeyboardMarkup(rows)


# ================= КОМАНДЫ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в магазин FELIKS RUSSIA DONATE!",
        reply_markup=main_keyboard()
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text(
        "⚙️ Панель администратора",
        reply_markup=admin_keyboard()
    )


async def addproduct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    context.user_data["adding_product"] = {"step": 0}
    await update.message.reply_text(
        "Напиши категорию: Транспорт, Скины или Вирта."
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in (
        "adding_product",
        "editing_product",
        "editing_category_product"
    ):
        context.user_data.pop(key, None)

    await update.message.reply_text("Действие отменено.")


# ================= CALLBACKS =================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    if data == "home":
        await query.edit_message_text(
            "Главное меню FELIKS RUSSIA DONATE",
            reply_markup=main_keyboard()
        )
        return

    if data == "catalog":
        await query.edit_message_text(
            "🛒 Выберите категорию:",
            reply_markup=category_keyboard()
        )
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]

        with db() as conn:
            products = conn.execute(
                "SELECT * FROM products WHERE category=? AND active=1 ORDER BY id",
                (category,)
            ).fetchall()

        if not products:
            await query.edit_message_text(
                "В этой категории пока нет товаров.",
                reply_markup=category_keyboard()
            )
            return

        buttons = [
            [InlineKeyboardButton(
                f"{p['name'][:35]} — {p['price']}",
                callback_data=f"product:{p['id']}"
            )]
            for p in products
        ]

        buttons.append([
            InlineKeyboardButton("⬅️ Назад", callback_data="catalog")
        ])

        await query.edit_message_text(
            f"📂 {category}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("product:"):
        pid = int(data.split(":")[1])
        product = get_product(pid)

        if not product:
            await query.edit_message_text("Товар больше недоступен.")
            return

        text = (
            f"🛍 {product['name']}\n\n"
            f"{product['description']}\n\n"
            f"💳 Карта: {product['price']}\n"
            f"💰 BC: {product['bc_price']}\n"
            f"⭐ Stars: {product['stars_price']}"
        )

        buttons = [
            [InlineKeyboardButton(
                "💳 Оплата картой",
                callback_data=f"pay:card:{pid}"
            )],
            [InlineKeyboardButton(
                "💰 Оплата BC",
                callback_data=f"pay:bc:{pid}"
            )]
        ]

        if int(product["stars_price"] or 0) > 0:
            buttons.append([
                InlineKeyboardButton(
                    "⭐ Оплата Stars",
                    callback_data=f"pay:stars:{pid}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"cat:{product['category']}"
            )
        ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # ОПЛАТА
    if data.startswith("pay:"):
        _, method, pid = data.split(":")
        product = get_product(int(pid))

        if not product:
            await query.edit_message_text("Товар недоступен.")
            return

        if method == "card":
            oid = create_order(user, product, "Карта", product["price"])

            await query.edit_message_text(
                f"💳 ОПЛАТА КАРТОЙ\n\n"
                f"Заказ №{oid}\n"
                f"Товар: {product['name']}\n"
                f"Сумма: {product['price']}\n\n"
                f"Реквизиты:\n{CARD_DETAILS}\n\n"
                "После перевода отправьте фото или файл чека."
            )
            return

        if method == "bc":
            oid = create_order(user, product, "BC", product["bc_price"])

            await query.edit_message_text(
                f"💰 ОПЛАТА BC\n\n"
                f"Заказ №{oid}\n"
                f"Товар: {product['name']}\n"
                f"Сумма: {product['bc_price']}\n\n"
                f"{BC_DETAILS}\n\n"
                "После оплаты отправьте скриншот."
            )
            return

        if method == "stars":
            stars = int(product["stars_price"] or 0)

            if stars <= 0:
                await query.edit_message_text("Оплата Stars недоступна.")
                return

            oid = create_order(
                user, product, "Telegram Stars", f"{stars} Stars"
            )

            await context.bot.send_invoice(
                chat_id=user.id,
                title=product["name"][:32],
                description=f"Заказ №{oid}",
                payload=f"feliks_order_{oid}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(product["name"][:32], stars)]
            )

            await query.edit_message_text(
                f"⭐ Счёт на оплату заказа №{oid} отправлен."
            )
            return

    # МОИ ЗАКАЗЫ
    if data == "myorders":
        with db() as conn:
            orders = conn.execute(
                "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (user.id,)
            ).fetchall()

        if not orders:
            await query.edit_message_text(
                "У вас пока нет заказов.",
                reply_markup=main_keyboard()
            )
            return

        text = "📦 ВАШИ ЗАКАЗЫ\n\n"

        for order in orders:
            text += (
                f"Заказ №{order['id']}\n"
                f"Товар: {order['product_name']}\n"
                f"Оплата: {order['payment_method']}\n"
                f"Статус: {order['status']}\n\n"
            )

        await query.edit_message_text(text, reply_markup=main_keyboard())
        return

    if data == "help":
        await query.edit_message_text(
            "По вопросам заказа обратитесь к администратору.",
            reply_markup=main_keyboard()
        )
        return

    # АДМИНКА
    if data.startswith("admin:"):
        if not is_admin(user.id):
            return

        action = data.split(":", 1)[1]

        if action == "menu":
            await query.edit_message_text(
                "⚙️ Панель администратора",
                reply_markup=admin_keyboard()
            )
            return

        if action == "add":
            context.user_data["adding_product"] = {"step": 0}
            await query.edit_message_text(
                "Напиши категорию: Транспорт, Скины или Вирта."
            )
            return

        if action == "products":
            with db() as conn:
                products = conn.execute(
                    "SELECT * FROM products ORDER BY id DESC"
                ).fetchall()

            if not products:
                await query.edit_message_text(
                    "Товаров пока нет.",
                    reply_markup=admin_keyboard()
                )
                return

            buttons = []

            for p in products:
                status = "🟢" if p["active"] else "⚪"
                buttons.append([
                    InlineKeyboardButton(
                        f"{status} #{p['id']} {p['name'][:30]}",
                        callback_data=f"admprod:{p['id']}"
                    )
                ])

            buttons.append([
                InlineKeyboardButton("➕ Добавить товар", callback_data="admin:add")
            ])
            buttons.append([
                InlineKeyboardButton("⬅️ В админку", callback_data="admin:menu")
            ])

            await query.edit_message_text(
                "📋 Управление товарами:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        if action == "orders":
            with db() as conn:
                orders = conn.execute(
                    "SELECT * FROM orders ORDER BY id DESC LIMIT 20"
                ).fetchall()

            if not orders:
                await query.edit_message_text(
                    "Заказов пока нет.",
                    reply_markup=admin_keyboard()
                )
                return

            buttons = []

            for o in orders:
                buttons.append([
                    InlineKeyboardButton(
                        f"№{o['id']} · {o['product_name'][:20]} · {o['status'][:15]}",
                        callback_data=f"admorder:{o['id']}"
                    )
                ])

            buttons.append([
                InlineKeyboardButton("⬅️ В админку", callback_data="admin:menu")
            ])

            await query.edit_message_text(
                "📦 Выбери заказ:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

    # ВЫБОР ТОВАРА
    if data.startswith("admprod:"):
        if not is_admin(user.id):
            return

        pid = int(data.split(":")[1])
        p = get_product(pid, active_only=False)

        if not p:
            await query.edit_message_text("Товар не найден.")
            return

        text = (
            f"🛠 ТОВАР №{pid}\n\n"
            f"Название: {p['name']}\n"
            f"Категория: {p['category']}\n"
            f"Описание: {p['description']}\n"
            f"Цена: {p['price']}\n"
            f"BC: {p['bc_price']}\n"
            f"Stars: {p['stars_price']}\n"
            f"Активен: {'Да' if p['active'] else 'Нет'}"
        )

        await query.edit_message_text(
            text,
            reply_markup=product_manage_keyboard(pid)
        )
        return

    # РЕДАКТИРОВАНИЕ
    if data.startswith("edit:"):
        if not is_admin(user.id):
            return

        _, pid, field = data.split(":")
        pid = int(pid)

        if not get_product(pid, active_only=False):
            await query.edit_message_text("Товар не найден.")
            return

        context.user_data["editing_product"] = {
            "product_id": pid,
            "field": field
        }

        prompts = {
            "name": "Введи новое название товара.",
            "description": "Введи новое описание.",
            "price": "Введи новую цену картой.",
            "bc_price": "Введи новую цену BC.",
            "stars_price": "Введи новое количество Stars числом."
        }

        await query.edit_message_text(
            prompts[field] + "\n\nОтмена: /cancel"
        )
        return

    if data.startswith("editcat:"):
        if not is_admin(user.id):
            return

        pid = int(data.split(":")[1])
        context.user_data["editing_category_product"] = pid

        await query.edit_message_text(
            "Выбери новую категорию:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚗 Транспорт", callback_data="setcat:Транспорт")],
                [InlineKeyboardButton("🎨 Скины", callback_data="setcat:Скины")],
                [InlineKeyboardButton("💰 Вирта", callback_data="setcat:Вирта")]
            ])
        )
        return

    if data.startswith("setcat:"):
        if not is_admin(user.id):
            return

        pid = context.user_data.get("editing_category_product")

        if not pid:
            await query.edit_message_text("Сессия изменения категории истекла.")
            return

        category = data.split(":", 1)[1]

        with db() as conn:
            conn.execute(
                "UPDATE products SET category=? WHERE id=?",
                (category, pid)
            )

        context.user_data.pop("editing_category_product", None)

        await query.edit_message_text(
            f"✅ Категория изменена: {category}",
            reply_markup=product_manage_keyboard(pid)
        )
        return

    # УДАЛЕНИЕ
    if data.startswith("delete:"):
        if not is_admin(user.id):
            return

        pid = int(data.split(":")[1])
        p = get_product(pid, active_only=False)

        if not p:
            await query.edit_message_text("Товар не найден.")
            return

        await query.edit_message_text(
            f"Удалить товар «{p['name']}» из каталога?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Да, удалить", callback_data=f"deleteyes:{pid}")],
                [InlineKeyboardButton("Отмена", callback_data=f"admprod:{pid}")]
            ])
        )
        return

    if data.startswith("deleteyes:"):
        if not is_admin(user.id):
            return
