
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

# ================= CONFIG =================

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "donate.db")
CARD_DETAILS = os.getenv("CARD_DETAILS", "Реквизиты уточняются у владельца.")
OWNER = "crmp_feliks"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

web = Flask(__name__)


@web.route("/")
def home():
    return "FELIKS RUSSIA DONATE: online", 200


def run_web():
    web.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        use_reloader=False
    )


# ================= DATABASE =================

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
                price TEXT NOT NULL,
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
                status TEXT DEFAULT 'Новая',
                payment_method TEXT DEFAULT '',
                proof_file_id TEXT DEFAULT ''
            )
        """)

        count = c.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        if count == 0:
            products = [
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
                ("Скины", "София", "150 ₽"),
                ("Скины", "Лео", "230 ₽"),
                ("Скины", "Хиш Мил", "129 ₽"),
                ("Скины", "Максимка", "349 ₽")
            ]

            c.executemany(
                "INSERT INTO products(category,name,price) VALUES(?,?,?)",
                products
            )


# ================= MENUS =================

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
            "SELECT * FROM products WHERE category=? AND active=1 ORDER BY id",
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


# ================= NOTIFICATIONS =================

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
            f"💳 Оплата: {method}\n"
            f"👤 Игрок: {user.full_name}\n"
            f"🆔 ID: {user.id}\n"
            f"Статус: Ожидает обработки",
            reply_markup=keyboard
        )
    except Exception:
        log.exception("Не удалось уведомить администратора")


# ================= START / COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🎮 Добро пожаловать в FELIKS RUSSIA | DONATE!\n\n"
        "Выбери нужный раздел:",
        reply_markup=main_menu()
    )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    context.user_data.clear()
    await update.message.reply_text(
        "🛠 Админ-панель:",
        reply_markup=admin_menu()
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=main_menu()
    )


# ================= TEXT HANDLER =================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = (msg.text or "").strip()
    action = context.user_data.get("action")

    if action and is_admin(update):
        if action == "add_name":
            context.user_data["new_name"] = text
            context.user_data["action"] = "add_price"
            await msg.reply_text("Введи цену товара:")
            return

        if action == "add_price":
            context.user_data["new_price"] = text
            context.user_data["action"] = "add_category"
            await msg.reply_text("Введи категорию: Транспорт, Скины или Вирта.")
            return

        if action == "add_category":
            category = text.capitalize()

            if category not in ("Транспорт", "Скины", "Вирта"):
                await msg.reply_text("Укажи: Транспорт, Скины или Вирта.")
                return

            with db() as c:
                c.execute(
                    "INSERT INTO products(category,name,price) VALUES(?,?,?)",
                    (
                        category,
                        context.user_data["new_name"],
                        context.user_data["new_price"]
                    )
                )

            context.user_data.clear()
            await msg.reply_text("✅ Товар добавлен!", reply_markup=admin_menu())
            return

        fields = {
            "edit_name": "name",
            "edit_price": "price",
            "edit_description": "description",
            "edit_category": "category"
        }

        if action in fields:
            column = fields[action]
            pid = context.user_data.get("product_id")

            with db() as c:
                c.execute(
                    f"UPDATE products SET {column}=? WHERE id=?",
                    (text, pid)
                )

            context.user_data.clear()
            await msg.reply_text("✅ Изменения сохранены.", reply_markup=admin_menu())
            return

    if text == "🚘 Транспорт":
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
            "💰 Выбери товар категории «Вирта»:",
            reply_markup=keyboard
        )

    elif text == "🎁 Скидки":
        await msg.reply_text(
            "🎁 СКИДКИ И НОВОСТИ\n\n"
            "Информация о новых товарах и предложениях.\n"
            "По вопросам: @crmp_feliks"
        )

    elif text == "ℹ️ Помощь":
        await msg.reply_text(
            "ℹ️ ПОМОЩЬ\n\n"
            "🚘 Транспорт — каталог транспорта.\n"
            "👕 Скины — каталог скинов.\n"
            "💰 Покупка вирты — игровая валюта.\n"
            "📦 Мои заказы — история заказов.\n\n"
            "После оплаты картой отправь фото или файл чека.\n"
            "BC и звёзды согласовываются с владельцем."
        )

    elif text == "📦 Мои заказы":
        with db() as c:
            orders = c.execute(
                "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
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


# ================= PRODUCT / ORDER CALLBACKS =================

async def product_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    pid = int(q.data.split(":")[1])

    with db() as c:
        p = c.execute(
            "SELECT * FROM products WHERE id=? AND active=1",
            (pid,)
        ).fetchone()

    if not p:
        await q.message.reply_text("Товар недоступен.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Оформить заказ", callback_data=f"order:{pid}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back:{p['category']}")]
    ])

    caption = (
        f"📦 {p['name']}\n"
        f"💵 Цена: {p['price']}\n"
        f"📝 {p['description'] or 'Описание пока не добавлено.'}"
    )

    if p["photo"]:
        try:
            await q.message.reply_photo(
                p["photo"],
                caption=caption,
                reply_markup=keyboard
            )
        except Exception:
            log.exception("Ошибка отправки фото товара")
            await q.message.reply_text(caption, reply_markup=keyboard)
    else:
        await q.message.reply_text(caption, reply_markup=keyboard)


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

    pid = int(q.data.split(":")[1])

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


def get_ruble_amount(price):
    match = re.search(r"(\d+)\s*₽", price or "")
    return match.group(1) if match else None


async def payment_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    method, pid_text = q.data.split(":")
    pid = int(pid_text)

    with db() as c:
        p = c.execute(
            "SELECT * FROM products WHERE id=? AND active=1",
            (pid,)
        ).fetchone()

    if not p:
        await q.message.reply_text("Товар недоступен.")
        return

    user = q.from_user
    product_text = f"{p['name']} — {p['price']}"

    if method == "paycard":
        amount = get_ruble_amount(p["price"])

        if not amount:
            await q.message.reply_text(
                "Не удалось определить цену в рублях. Обратись к владельцу."
            )
            return

        status = "Ожидается чек"
        payment_name = "Карта"

    else:
        status = "Ожидает связи с владельцем"
        payment_name = "BC" if method == "paybc" else "Звёзды"

    with db() as c:
        cur = c.execute(
            """
            INSERT INTO orders(user_id,username,product,status,payment_method)
            VALUES(?,?,?,?,?)
            """,
            (
                user.id,
                user.username or user.full_name,
                product_text,
                status,
                payment_name
            )
        )
        order_id = cur.lastrowid

    if method == "paycard":
        context.user_data["proof_order_id"] = order_id

        await q.message.reply_text(
            f"💳 ОПЛАТА КАРТОЙ\n\n"
            f"📦 Товар: {p['name']}\n"
            f"💵 Сумма: {amount} ₽\n\n"
            f"Реквизиты:\n{CARD_DETAILS}\n\n"
            "После оплаты отправь сюда фото или файл чека.\n"
            f"Номер заказа: #{order_id}\n"
            "Для отмены напиши /cancel."
        )
    else:
        await q.message.reply_text(
            f"💎 ОПЛАТА {payment_name.upper()}\n\n"
            f"📦 Товар: {p['name']}\n"
            f"Цена: {p['price']}\n\n"
            f"Свяжись с владельцем: @{OWNER}\n"
            f"Номер заказа: #{order_id}"
        )

    await notify_admin(
        context, order_id, p["name"], user, payment_name, p["price"]
    )


# ================= PAYMENT PROOF =================

async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    order_id = context.user_data.get("proof_order_id")

    if not message or not order_id:
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
        await message.reply_text("Заказ не найден или чек уже отправлен.")
        return

    if not ADMIN_ID:
        await message.reply_text("⚠️ Администратор не настроен.")
        return

    file_id = (
        message.photo[-1].file_id
        if message.photo
        else message.document.file_id
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"paid:{order_id}")],
        [InlineKeyboardButton("❌ Отклонить чек", callback_data=f"reject:{order_id}")]
    ])

    caption = (
        f"🧾 ЧЕК ПО ЗАКАЗУ #{order_id}\n\n"
        f"📦 Товар: {order['product']}\n"
        f"👤 Покупатель: {order['username']}\n"
        f"🆔 ID: {order['user_id']}\n"
        "📌 Статус: Проверка"
    )

    try:
        if message.photo:
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

        with db() as c:
            c.execute(
                "UPDATE orders SET status=?, proof_file_id=? WHERE id=?",
                ("Чек отправлен на проверку", file_id, order_id)
            )

        context.user_data.pop("proof_order_id", None)
        await message.reply_text("✅ Чек отправлен администратору на проверку.")

    except Exception:
        log.exception("Ошибка отправки чека")
        await message.reply_text("❌ Не удалось отправить чек. Попробуй позже.")


# ================= ADMIN PHOTO =================

async def admin_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if context.user_data.get("action") != "edit_photo":
        return

    if not update.message.photo:
        await update.message.reply_text("Отправь изображение.")
        return

    pid = context.user_data.get("product_id")
    file_id = update.message.photo[-1].file_id

    with db() as c:
        c.execute(
            "UPDATE products SET photo=? WHERE id=?",
            (file_id, pid)
        )

    context.user_data.clear()
    await update.message.reply_text("✅ Фото обновлено.", reply_markup=admin_menu())


# ================= ADMIN CALLBACKS =================

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(update):
        await q.message.reply_text("⛔ Нет доступа.")
        return

    data = q.data

    if data == "admin:close":
        await q.message.reply_text("Админ-панель закрыта.")
        return

    if data == "admin:add":
        context.user_data.clear()
        context.user_data["action"] = "add_name"
        await q.message.reply_text("Введи название нового товара:")
        return

    if data in ("admin:products", "admin:editlist", "admin:photolist", "admin:deletelist"):
        with db() as c:
            products = c.execute(
                "SELECT * FROM products WHERE active=1 ORDER BY id"
            ).fetchall()

        if not products:
            await q.message.reply_text("Товаров пока нет.")
            return

        rows = []
        for p in products:
            if data == "admin:editlist":
                callback = f"editproduct:{p['id']}"
            elif data == "admin:photolist":
                callback = f"photoproduct:{p['id']}"
            elif data == "admin:deletelist":
                callback = f"deleteproduct:{p['id']}"
            else:
                callback = f"orderinfo:0"

            if data == "admin:products":
                label = f"#{p['id']} {p['name']} — {p['price']}"
            else:
                label = f"#{p['id']} {p['name']}"

            if data == "admin:products":
                continue

     
