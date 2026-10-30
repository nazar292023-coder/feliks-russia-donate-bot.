
import os
import logging
import threading

import psycopg
from psycopg.rows import dict_row
from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# ==================================
# НАСТРОЙКИ
# ==================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

CARD_DETAILS = os.getenv("CARD_DETAILS", "Реквизиты уточняются")
BC_DETAILS = os.getenv("BC_DETAILS", "Реквизиты BC уточняются")
SUPPORT_TEXT = os.getenv("SUPPORT_TEXT", "Обратитесь в поддержку проекта.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ==================================
# FLASK ДЛЯ RENDER
# ==================================

flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "FELIKS RUSSIA DONATE ONLINE", 200


@flask_app.route("/health")
def health():
    return "OK", 200


def run_flask():
    port = int(os.getenv("PORT", "10000"))
    flask_app.run(host="0.0.0.0", port=port)


# ==================================
# БАЗА ДАННЫХ
# ==================================

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не установлен")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=15,
    )


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT NOT NULL,
                card_price DOUBLE PRECISION DEFAULT 0,
                bc_price DOUBLE PRECISION DEFAULT 0,
                stars_price INTEGER DEFAULT 0,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                product_id BIGINT,
                product_name TEXT NOT NULL,
                amount DOUBLE PRECISION DEFAULT 0,
                currency TEXT DEFAULT '',
                payment_method TEXT DEFAULT '',
                status TEXT DEFAULT 'Создан',
                receipt_file_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)


# ==================================
# ПОЛЬЗОВАТЕЛИ
# ==================================

def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name
        """, (user.id, user.username, user.first_name))


# ==================================
# ТОВАРЫ
# ==================================

def get_products(category=None):
    with db() as conn:
        if category:
            result = conn.execute("""
                SELECT * FROM products
                WHERE active = TRUE AND category = %s
                ORDER BY id DESC
            """, (category,))
        else:
            result = conn.execute("""
                SELECT * FROM products
                WHERE active = TRUE
                ORDER BY id DESC
            """)

        return result.fetchall()


def get_product(product_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM products
            WHERE id = %s AND active = TRUE
        """, (product_id,)).fetchone()


# ==================================
# ЗАКАЗЫ
# ==================================

def get_order(order_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM orders
            WHERE id = %s
        """, (order_id,)).fetchone()


def create_order(user_id, product):
    with db() as conn:
        result = conn.execute("""
            INSERT INTO orders (
                user_id, product_id, product_name,
                amount, currency, payment_method, status
            )
            VALUES (%s, %s, %s, 0, '', '', 'Создан')
            RETURNING id
        """, (user_id, product["id"], product["name"])).fetchone()

        return result["id"]


def update_order_status(order_id, status):
    with db() as conn:
        conn.execute("""
            UPDATE orders
            SET status = %s
            WHERE id = %s
        """, (status, order_id))


def save_receipt(order_id, file_id):
    with db() as conn:
        conn.execute("""
            UPDATE orders
            SET receipt_file_id = %s,
                status = 'Чек отправлен'
            WHERE id = %s
        """, (file_id, order_id))


# ==================================
# КЛАВИАТУРЫ
# ==================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Каталог", callback_data="catalog")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
    ])


def categories_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Транспорт", callback_data="cat:Транспорт")],
        [InlineKeyboardButton("👕 Скины", callback_data="cat:Скины")],
        [InlineKeyboardButton("💰 Вирта", callback_data="cat:Вирта")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="home")],
    ])


def product_keyboard(product_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Купить", callback_data=f"buy:{product_id}")],
        [InlineKeyboardButton("⬅️ Каталог", callback_data="catalog")],
    ])


def payment_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Банковская карта", callback_data=f"paycard:{order_id}")],
        [InlineKeyboardButton("🪙 BC", callback_data=f"paybc:{order_id}")],
        [InlineKeyboardButton("⭐ Telegram Stars", callback_data=f"paystars:{order_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="catalog")],
    ])


def admin_order_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order_id}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")],
        [InlineKeyboardButton("⬅️ Заказы", callback_data="admin_orders")],
    ])


# ==================================
# СТАРТ И ОТМЕНА
# ==================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    await update.message.reply_text(
        "👋 Добро пожаловать в магазин доната FELIKS RUSSIA!\n\n"
        "Выбери нужный раздел:",
        reply_markup=main_keyboard(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=main_keyboard(),
    )


# ==================================
# АДМИН-ПАНЕЛЬ
# ==================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("➕ Добавить товар", callback_data="admin_add")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
    ])

    await update.message.reply_text(
        "⚙️ Панель администратора FELIKS RUSSIA",
        reply_markup=keyboard,
    )


# ==================================
# КАТАЛОГ, ОПЛАТА И АДМИН-КНОПКИ
# ==================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    admin_actions = (
        data.startswith("admin_")
        or data.startswith("approve:")
        or data.startswith("reject:")
        or data.startswith("orderadmin:")
    )

    if admin_actions and user_id != ADMIN_ID:
        await query.answer("Нет доступа.", show_alert=True)
        return

    await query.answer()

    if data == "home":
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=main_keyboard(),
        )
        return

    if data == "catalog":
        await query.edit_message_text(
            "🛒 Выбери категорию:",
            reply_markup=categories_keyboard(),
        )
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        products = get_products(category)

        if not products:
            await query.edit_message_text(
                "В этой категории пока нет товаров.",
                reply_markup=categories_keyboard(),
            )
            return

        keyboard = []

        for product in products:
            keyboard.append([
                InlineKeyboardButton(
                    f"{product['name']} — {product['card_price']} ₽",
                    callback_data=f"product:{product['id']}",
                )
            ])

        keyboard.append([
            InlineKeyboardButton("⬅️ Назад", callback_data="catalog")
        ])

        await query.edit_message_text(
            f"Категория: {category}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("product:"):
        product_id = int(data.split(":")[1])
        product = get_product(product_id)

        if not product:
            await query.edit_message_text("Товар не найден.")
            return

        await query.edit_message_text(
            f"📦 {product['name']}\n\n"
            f"{product['description']}\n\n"
            f"💳 Карта: {product['card_price']} ₽\n"
            f"🪙 BC: {product['bc_price']}\n"
            f"⭐ Stars: {product['stars_price']}",
            reply_markup=product_keyboard(product_id),
        )
        return

    if data.startswith("buy:"):
        product_id = int(data.split(":")[1])
        product = get_product(product_id)

        if not product:
            await query.edit_message_text("Товар недоступен.")
            return

        order_id = create_order(user_id, product)

        await query.edit_message_text(
            f"Выбран товар: {product['name']}\n"
            f"Заказ №{order_id}\n\n"
            "Выбери способ оплаты:",
            reply_markup=payment_keyboard(order_id),
        )
        return

    if data.startswith(("paycard:", "paybc:", "paystars:")):
        action, order_id_text = data.split(":")
        order_id = int(order_id_text)
        order = get_order(order_id)

        if not order or order["user_id"] != user_id:
            await query.edit_message_text("Заказ не найден.")
            return

        if order["status"] in ("Оплачен", "Отклонён"):
            await query.edit_message_text("Этот заказ уже закрыт.")
            return

        product = get_product(order["product_id"])

        if not product:
            await query.edit_message_text("Товар недоступен.")
            return

        if action == "paycard":
            amount = float(product["card_price"])

            if amount <= 0:
                await query.edit_message_text("Оплата картой недоступна.")
                return

            with db() as conn:
                conn.execute("""
                    UPDATE orders
                    SET amount = %s,
                        currency = 'RUB',
                        payment_method = 'Карта',
                        status = 'Ожидает чек'
                    WHERE id = %s
                """, (amount, order_id))

            context.user_data["waiting_receipt"] = order_id

            await query.edit_message_text(
                f"💳 Заказ №{order_id}\n\n"
                f"Сумма: {amount} ₽\n\n"
                f"Реквизиты:\n{CARD_DETAILS}\n\n"
                "После оплаты отправь фото или файл чека.\n"
                "Для отмены: /cancel"
            )
            return

        if action == "paybc":
            amount = float(product["bc_price"])

            if amount <= 0:
                await query.edit_message_text("Оплата BC недоступна.")
                return

            with db() as conn:
                conn.execute("""
                    UPDATE orders
                    SET amount = %s,
                        currency = 'BC',
                        payment_method = 'BC',
                        status = 'Ожидает чек'
                    WHERE id = %s
                """, (amount, order_id))

            context.user_data["waiting_receipt"] = order_id

            await query.edit_message_text(
                f"🪙 Заказ №{order_id}\n\n"
                f"Сумма: {amount} BC\n\n"
                f"Реквизиты:\n{BC_DETAILS}\n\n"
                "После оплаты отправь фото или файл чека.\n"
                "Для отмены: /cancel"
            )
            return

        if action == "paystars":
            amount = int(product["stars_price"])

            if amount <= 0:
                await query.edit_message_text("Оплата Stars недоступна.")
                return

            with db() as conn:
                conn.execute("""
                    UPDATE orders
                    SET amount = %s,
                        currency = 'XTR',
                        payment_method = 'Stars',
                        status = 'Ожидает оплаты'
                    WHERE id = %s
                """, (amount, order_id))

            await context.bot.send_invoice(
                chat_id=user_id,
                title=product["name"][:32],
                description=f"Заказ FELIKS RUSSIA №{order_id}"[:255],
                payload=f"order_{order_id}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(product["name"][:32], amount)],
            )
            return

    if data == "my_orders":
        with db() as conn:
            orders = conn.execute("""
                SELECT * FROM orders
                WHERE user_id = %s
                ORDER BY id DESC
                LIMIT 10
            """, (user_id,)).fetchall()

        if not orders:
            await query.edit_message_text("📦 У тебя пока нет заказов.")
            return

        result = "📦 Твои последние заказы:\n\n"

        for order in orders:
            result += (
                f"№{order['id']} — {order['product_name']}\n"
                f"Сумма: {order['amount']} {order['currency']}\n"
                f"Статус: {order['status']}\n\n"
            )

        await query.edit_message_text(result[:4000])
        return

    if data == "support":
        await query.edit_message_text(
            f"🆘 Поддержка FELIKS RUSSIA\n\n{SUPPORT_TEXT}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
            ]),
        )
        return

    # ----- АДМИН: ЗАКАЗЫ -----

    if data == "admin_orders":
        with db() as conn:
            orders = conn.execute("""
                SELECT * FROM orders
                ORDER BY id DESC
                LIMIT 15
            """).fetchall()

        if not orders:
            await query.edit_message_text("Заказов пока нет.")
            return

        keyboard = []

        for order in orders:
            keyboard.append([
                InlineKeyboardButton(
                    f"№{order['id']} — {order['status']}",
                    callback_data=f"orderadmin:{order['id']}",
                )
            ])

        await query.edit_message_text(
            "📦 Последние заказы:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("orderadmin:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)

        if not order:
            await query.edit_message_text("Заказ не найден.")
            return

        await query.edit_message_text(
            f"Заказ №{order_id}\n"
            f"Пользователь: {order['user_id']}\n"
            f"Товар: {order['product_name']}\n"
            f"Сумма: {order['amount']} {order['currency']}\n"
            f"Оплата: {order['payment_method']}\n"
            f"Статус: {order['status']}",
            reply_markup=admin_order_keyboard(order_id),
        )
        return

    if data.startswith("approve:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)

        if not order:
            await query.edit_message_text("Заказ не найден.")
            return

        if order["status"] in ("Оплачен", "Отклонён"):
            await query.edit_message_text("Заказ уже закрыт.")
            return

        update_order_status(order_id, "Оплачен")

        await context.bot.send_message(
            order["user_id"],
            f"✅ Оплата заказа №{order_id} подтверждена администрацией FELIKS RUSSIA.",
        )

        await query.edit_message_text(
            f"Заказ №{order_id} подтверждён."
        )
        return

    if data.startswith("reject:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)

        if not order:
            await query.edit_message_text("Заказ не найден.")
            return

        if order["status"] == "Оплачен":
            await query.edit_message_text("Оплаченный заказ нельзя отклонить.")
            return

        update_order_status(order_id, "Отклонён")

        await context.bot.send_message(
            order["user_id"],
            f"❌ Заказ №{order_id} отклонён. Обратись в поддержку, если возникла ошибка.",
        )

        await query.edit_message_text(
            f"Заказ №{order_id} отклонён."
        )
        return

    # ----- АДМИН: СТАТИСТИКА -----

    if data == "admin_stats":
        with db() as conn:
            users = conn.execute(
                "SELECT COUNT(*) AS n FROM users"
            ).fetchone()["n"]

            products = conn.execute(
                "SELECT COUNT(*) AS n FROM products"
            ).fetchone()["n"]

            orders = conn.execute(
                "SELECT COUNT(*) AS n FROM orders"
            ).fetchone()["n"]

            paid = conn.execute("""
                SELECT COUNT(*) AS n FROM orders
                WHERE status = 'Оплачен'
            """).fetchone()["n"]

        await query.edit_message_text(
            f"📊 Статистика\n\n"
            f"Пользователей: {users}\n"
            f"Товаров: {products}\n"
            f"Заказов: {orders}\n"
            f"Оплачено: {paid}"
        )
        return

    # ----- АДМИН: ДОБАВЛЕНИЕ ТОВАРА -----

    if data == "admin_add":
        context.user_data["admin_action"] = "add_name"
        context.user_data["new_product"] = {}

        await query.edit_message_text("Введи название товара:")
        return

    # ----- АДМИН: РАССЫЛКА -----

    if data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await query.edit_message_text("Отправь текст рассылки:")
        return


# ==================================
# ОБРАБОТКА ЧЕКОВ
# ==================================

async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user:
        return

    order_id = context.user_data.get("waiting_receipt")

    if not order_id:
        await update.message.reply_text(
            "Сначала выбери заказ и способ оплаты в каталоге."
        )
        return

    order = get_order(order_id)
    if not order or order["user_id"] != user.id:
        context.user_data.pop("waiting_receipt", None)
        await update.message.reply_text("Заказ не найден.")
        return

    if order["status"] != "Ожидает чек":
        context.user_data.pop("waiting_receipt", None)
        await update.message.reply_text("Этот заказ больше не ожидает чек.")
        return
        save_receipt(order_id, file_id)
    context.user_data.pop("waiting_receipt", None)

    caption = (
        f"🧾 Чек по заказу №{order_id}\n"
        f"Пользователь: {user.id}\n"
        f"Товар: {order['product_name']}\n"
        f"Сумма: {order['amount']} {order['currency']}\n"
        f"Способ оплаты: {order['payment_method']}"
    )
    keyboard = admin_order_keyboard(order_id)

    try:
        if update.message.photo:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=file_id,
                caption=caption,
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=file_id,
                caption=caption,
               reply_markup=keyboard,
            )
    except Exception:
        logger.exception("Не удалось отправить чек администратору")
        await update.message.reply_text(
            "Не удалось передать чек администратору. Попробуй позже."
        )
        return

    await update.message.reply_text(
        "✅ Чек отправлен администратору.\n"
        "Дождись проверки."
    )
    # ==================================
# TELEGRAM STARS
# ==================================

async def precheckout_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.pre_checkout_query

    try:
        if not query.invoice_payload.startswith("order_"):
            await query.answer(
                ok=False,
                error_message="Неизвестный заказ.",
        )
            return

        order_id = int(query.invoice_payload.split("_", 1)[1])
        order = get_order(order_id)

        if (
            not order
            or order["user_id"] != query.from_user.id
            or order["status"] != "Ожидает оплаты"
            or order["currency"] != "XTR"
            or int(order["amount"]) != query.total_amount
            or query.currency != "XTR"
        ):
            await query.answer(
                ok=False,
                error_message="Заказ недействителен. Создай новый заказ.",
            )
            return

        await query.answer(ok=True)
        


async def successful_payment_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    payment = update.message.successful_payment

    try:
        if not payment.invoice_payload.startswith("order_"):
            return

        order_id = int(payment.invoice_payload.split("_", 1)[1])
        order = get_order(order_id)

        if not order or order["user_id"] != update.effective_user.id:
            logger.warning("Не найден заказ для платежа Stars: %s", order_id)
            return

        if (
            payment.currency != "XTR"
            or payment.total_amount != int(order["amount"])
            or order["currency"] != "XTR"
        ):
            logger.error("Несовпадение суммы или валюты Stars: %s", order_id)
            return

        if order["status"] == "Оплачен":
            return

        update_order_status(order_id, "Оплачен")

        await update.message.reply_text(
            f"⭐ Оплата заказа №{order_id} прошла успешно!\n"
            "Администрация получила уведомление."
        )

        await context.bot.send_message(
            ADMIN_ID,
            f"⭐ Новый оплаченный заказ Stars!\n"
            f"Заказ №{order_id}\n"
            f"Пользователь: {update.effective_user.id}\n"
            f"Товар: {order['product_name']}\n"
            f"Сумма: {payment.total_amount} Stars",
        )

    except Exception:
        logger.exception("Ошибка обработки успешной оплаты Stars")


# ==================================
# ТЕКСТОВЫЕ ДЕЙСТВИЯ АДМИНА
# ==================================
async def admin_text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    action = context.user_data.get("admin_action")

    if not action:
        return

    text = update.message.text.strip()

    if action == "broadcast":
        with db() as conn:
            users = conn.execute(
                "SELECT user_id FROM users"
            ).fetchall()

        sent = 0
        failed = 0

        await update.message.reply_text("📢 Начинаю рассылку...")

        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=text,
                )
                sent += 1
            except Exception:
                failed += 1

        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            f"✅ Рассылка завершена.\n"
            f"Отправлено: {sent}\n"
            f"Не доставлено: {failed}"
        )
        return

    product = context.user_data.setdefault("new_product", {})

    if action == "add_name":
        product["name"] = text
        context.user_data["admin_action"] = "add_description"
        await update.message.reply_text("Введи описание товара:")
        return

    if action == "add_description":
        product["description"] = text
        context.user_data["admin_action"] = "add_category"
        await update.message.reply_text(
            "Введи категорию товара:\n"
            "Транспорт, Скины или Вирта"
        )
        return

    if action == "add_category":
        allowed = ["Транспорт", "Скины", "Вирта"]

        if text not in allowed:
            await update.message.reply_text(
                "Укажи одну из категорий: Транспорт, Скины или Вирта."
            )
            return

        product["category"] = text
        context.user_data["admin_action"] = "add_card"
        await update.message.reply_text("Введи цену в рублях:")
        return

    if action == "add_card":
        try:
            price = float(text.replace(",", "."))
            if price < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введи корректную цену числом.")
            return

        product["card_price"] = price
        context.user_data["admin_action"] = "add_bc"
        await update.message.reply_text("Введи цену в BC:")
        return

    if action == "add_bc":
        try:
            price = float(text.replace(",", "."))
            if price < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введи цену в BC:")
        return
        if action == "add_bc":
        try:
            price = float(text.replace(",", "."))
            if price < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введи корректную цену числом.")
            return

        product["bc_price"] = price
        context.user_data["admin_action"] = "add_stars"
        await update.message.reply_text("Введи цену в Telegram Stars (целое число):")
        return

    if action == "add_stars":
        try:
            price = int(text)
            if price < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Введи целое число для цены Stars."
        )
            return

        product["stars_price"] = price

        with db() as conn:
            conn.execute("""
                INSERT INTO products (
                    name, description, category,
                    card_price, bc_price, stars_price, active
                )
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            """, (
                product["name"],
                product["description"],
                product["category"],
                product["card_price"],
                product["bc_price"],
                product["stars_price"],
            ))

        context.user_data.pop("admin_action", None)
        context.user_data.pop("new_product", None)

        await update.message.reply_text(
            "✅ Товар успешно добавлен в каталог."
        )

# ==================================
# ОШИБКИ
# ==================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception("Ошибка во время обработки обновления:", exc_info=context.error)


# ==================================
# ЗАПУСК
# ==================================
  def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не установлен BOT_TOKEN")

    if ADMIN_ID == 0:
        raise RuntimeError("Не установлен ADMIN_ID")

    init_db()

    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(
        PreCheckoutQueryHandler(precheckout_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_handler,
        )
    )

    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.ALL,
            receipt_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text_handler,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("FELIKS RUSSIA DONATE запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
            

