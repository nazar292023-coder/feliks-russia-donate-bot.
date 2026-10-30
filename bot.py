
import os
import sqlite3
import logging
from datetime import datetime

from flask import Flask
from threading import Thread

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, ContextTypes, filters
)

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CARD_DETAILS = os.getenv("CARD_DETAILS", "Реквизиты уточняются у администратора.")
BC_DETAILS = os.getenv("BC_DETAILS", "Уточните оплату BC у администратора.")
DB_PATH = os.getenv("DB_PATH", "donate.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app_web = Flask(__name__)


@app_web.route("/")
def home():
    return "FELIKS RUSSIA DONATE BOT ONLINE"


def keep_web_alive():
    port = int(os.getenv("PORT", "10000"))
    app_web.run(host="0.0.0.0", port=port)


# ================= БАЗА ДАННЫХ =================

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
                created_at TEXT NOT NULL
            )
        """)


def get_product(product_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE id=? AND active=1",
            (product_id,)
        ).fetchone()


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


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


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


# ================= КОМАНДЫ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в магазин FELIKS RUSSIA DONATE!\n\n"
        "Выберите нужный раздел:",
        reply_markup=main_keyboard()
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить товар", callback_data="admin:add")],
        [InlineKeyboardButton("📋 Список товаров", callback_data="admin:products")],
        [InlineKeyboardButton("📦 Последние заказы", callback_data="admin:orders")]
    ])
    await update.message.reply_text("⚙️ Панель администратора", reply_markup=keyboard)


async def addproduct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    context.user_data["adding_product"] = {"step": 0}
    await update.message.reply_text(
        "➕ Добавление товара.\n\n"
        "Напиши категорию: Транспорт, Скины или Вирта."
    )


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
                "SELECT * FROM products WHERE category=? AND active=1",
                (category,)
            ).fetchall()

        if not products:
            await query.edit_message_text(
                "В этой категории пока нет товаров.",
                reply_markup=category_keyboard()
            )
            return

        buttons = []
        for product in products:
            buttons.append([
                InlineKeyboardButton(
                    f"{product['name']} — {product['price']}",
                    callback_data=f"product:{product['id']}"
                )
            ])
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="catalog")])

        await query.edit_message_text(
            f"📂 Категория: {category}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("product:"):
        product_id = int(data.split(":")[1])
        product = get_product(product_id)

        if not product:
            await query.edit_message_text("Товар больше недоступен.")
            return

        context.user_data["selected_product"] = product_id

        text = (
            f"🛍 {product['name']}\n\n"
            f"{product['description']}\n\n"
            f"💳 Карта: {product['price']}\n"
            f"💰 BC: {product['bc_price']}\n"
            f"⭐ Stars: {product['stars_price']}"
        )

        buttons = [
            [InlineKeyboardButton("💳 Оплата картой", callback_data=f"pay:card:{product_id}")],
            [InlineKeyboardButton("💰 Оплата BC", callback_data=f"pay:bc:{product_id}")],
        ]

        if int(product["stars_price"] or 0) > 0:
            buttons.append([
                InlineKeyboardButton("⭐ Оплата Stars", callback_data=f"pay:stars:{product_id}")
            ])

        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"cat:{product['category']}")])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("pay:"):
        _, method, product_id = data.split(":")
        product = get_product(int(product_id))

        if not product:
            await query.edit_message_text("Товар недоступен.")
            return

        if method == "card":
            order_id = create_order(user, product, "Карта", product["price"])
            await query.edit_message_text(
                f"💳 **Оплата картой**\n\n"
                f"Заказ №{order_id}\n"
                f"Товар: {product['name']}\n\n"
                f"Реквизиты для оплаты:\n{CARD_DETAILS}\n\n"
                "После перевода отправьте сюда фото или файл чека.\n"
                "Администратор проверит оплату вручную.",
                parse_mode="Markdown"
            )
            return

        if method == "bc":
            order_id = create_order(user, product, "BC", product["bc_price"])
            await query.edit_message_text(
                f"💰 **Оплата BC**\n\n"
                f"Заказ №{order_id}\n"
                f"Товар: {product['name']}\n\n"
                f"{BC_DETAILS}\n\n"
                "После оплаты отправьте сюда скриншот подтверждения.\n"
                "Администратор проверит его вручную.",
                parse_mode="Markdown"
            )
            return

        if method == "stars":
            stars = int(product["stars_price"] or 0)
            if stars <= 0:
                await query.edit_message_text("Оплата Stars для этого товара недоступна.")
                return

            order_id = create_order(user, product, "Telegram Stars", f"{stars} Stars")
            await context.bot.send_invoice(
                chat_id=user.id,
                title=product["name"][:32],
                description=f"Заказ №{order_id}: {product['description'][:200]}",
                payload=f"feliks_order_{order_id}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(product["name"][:32], stars)]
            )
            await query.edit_message_text(
                f"⭐ Счёт на оплату заказа №{order_id} отправлен отдельным сообщением."
            )
            return

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

        text = "📦 Ваши заказы:\n\n"
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
            "Если возникли вопросы по заказу, отправьте сообщение администратору.",
            reply_markup=main_keyboard()
        )
        return

    # Админские кнопки
    if data.startswith("admin:"):
        if not is_admin(user.id):
            return

        action = data.split(":")[1]

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
                await query.edit_message_text("Товаров пока нет.")
                return

            text = "📋 Товары:\n\n"
            for p in products:
                text += f"#{p['id']} — {p['name']} ({p['category']})\n"
            await query.edit_message_text(text)
            return

        if action == "orders":
            with db() as conn:
                orders = conn.execute(
                    "SELECT * FROM orders ORDER BY id DESC LIMIT 15"
                ).fetchall()

            if not orders:
                await query.edit_message_text("Заказов пока нет.")
                return

            text = "📦 Последние заказы:\n\n"
            for o in orders:
                text += (
                    f"№{o['id']} — {o['product_name']}\n"
                    f"Пользователь: {o['username']} ({o['user_id']})\n"
                    f"Оплата: {o['payment_method']}\n"
                    f"Статус: {o['status']}\n\n"
                )
            await query.edit_message_text(text)
            return

    if data.startswith("order:"):
        if not is_admin(user.id):
            return

        _, action, order_id = data.split(":")
        order_id = int(order_id)

        new_status = "Оплачено" if action == "paid" else "Отклонено"

        with db() as conn:
            order = conn.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if not order:
                await query.edit_message_text("Заказ не найден.")
                return
            conn.execute(
                "UPDATE orders SET status=? WHERE id=?",
                (new_status, order_id)
            )

        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"Заказ №{order_id}: {new_status}."
            )
        except Exception:
            pass

        await query.edit_message_caption(
            caption=f"Заказ №{order_id}: {new_status}."
        )
        return


# ================= ДОБАВЛЕНИЕ ТОВАРА =================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_admin(user.id):
        return

    state = context.user_data.get("adding_product")
    if not state:
        return

    value = update.message.text.strip()
    step = state["step"]

    prompts = [
        "Напиши название товара.",
        "Напиши описание товара.",
        "Напиши цену картой (например: 100 грн).",
        "Напиши цену BC.",
        "Напиши цену Stars числом. Если Stars не нужны — напиши 0."
    ]

    if step == 0:
        if value not in ["Транспорт", "Скины", "Вирта"]:
            await update.message.reply_text(
                "Укажи одну из категорий: Транспорт, Скины или Вирта."
            )
            return
        state["category"] = value

    elif step == 1:
        state["name"] = value

    elif step == 2:
        state["description"] = value

    elif step == 3:
        state["price"] = value

    elif step == 4:
        state["bc_price"] = value

    elif step == 5:
        try:
            stars = int(value)
            if stars < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введи целое число, например 0 или 50.")
            return

        with db() as conn:
            conn.execute("""
                INSERT INTO products
                (category, name, description, price, bc_price, stars_price)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                state["category"],
                state["name"],
                state["description"],
                state["price"],
                state["bc_price"],
                stars
            ))

        context.user_data.pop("adding_product", None)
        await update.message.reply_text("✅ Товар добавлен в каталог.")
        return

    state["step"] += 1
    await update.message.reply_text(prompts[state["step"] - 1])


# ================= ЧЕКИ =================

async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    with db() as conn:
        order = conn.execute("""
            SELECT * FROM orders
            WHERE user_id=? AND status='Ожидает оплаты'
            ORDER BY id DESC LIMIT 1
        """, (user.id,)).fetchone()

    if not order:
        await update.message.reply_text(
            "Не найден заказ, ожидающий подтверждения оплаты."
        )
        return

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        return

    with db() as conn:
        conn.execute(
            "UPDATE orders SET proof_file_id=?, status='Чек отправлен' WHERE id=?",
            (file_id, order["id"])
        )

    caption = (
        f"📥 Новый чек на проверку\n\n"
        f"Заказ №{order['id']}\n"
        f"Товар: {order['product_name']}\n"
        f"Способ оплаты: {order['payment_method']}\n"
        f"Сумма: {order['amount']}\n"
        f"Пользователь: {user.full_name}\n"
        f"ID: {user.id}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"order:paid:{order['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"order:reject:{order['id']}")
        ]
    ])

    if update.message.photo:
        await context.bot.send_photo(
            ADMIN_ID, file_id, caption=caption, reply_markup=keyboard
        )
    else:
        await context.bot.send_document(
            ADMIN_ID, file_id, caption=caption, reply_markup=keyboard
        )

    await update.message.reply_text(
        "✅ Чек отправлен администратору. Ожидайте проверки."
    )


# ================= TELEGRAM STARS =================

async def precheckout_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    payment = update.message.successful_payment
    payload = payment.invoice_payload

    if not payload.startswith("feliks_order_"):
        return

    order_id = int(payload.replace("feliks_order_", ""))

    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()

        if order:
            conn.execute(
                "UPDATE orders SET status='Оплачено' WHERE id=?",
                (order_id,)
            )

    await update.message.reply_text(
        f"⭐ Оплата заказа №{order_id} успешно получена!"
    )

    if order:
        await context.bot.send_message(
            ADMIN_ID,
            f"⭐ Получена оплата Stars!\n"
            f"Заказ №{order_id}\n"
            f"Товар: {order['product_name']}\n"
            f"Пользователь: {update.effective_user.id}"
        )


# ================= ЗАПУСК =================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Добавь BOT_TOKEN в Environment на Render.")

    if ADMIN_ID == 0:
        raise RuntimeError("Добавь ADMIN_ID в Environment на Render.")

    init_db()

    Thread(target=keep_web_alive, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("addproduct", addproduct_command))

    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(PreCheckoutQueryHandler(precheckout_handler))

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_handler
        )
    )

    application.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
            proof_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    logger.info("FELIKS RUSSIA DONATE BOT STARTED")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
        
