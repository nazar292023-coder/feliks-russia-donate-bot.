
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
# ПОДКЛЮЧЕНИЕ SUPABASE
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
        """, (
            user.id,
            user.username,
            user.first_name,
        ))


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
                user_id,
                product_id,
                product_name,
                amount,
                currency,
                payment_method,
                status
            )
            VALUES (%s, %s, %s, 0, '', '', 'Создан')
            RETURNING id
        """, (
            user_id,
            product["id"],
            product["name"],
        )).fetchone()

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


# ==================================
# СТАРТ
# ==================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

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
# КАТАЛОГ И ЗАКАЗЫ
# ==================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    # Проверка администратора до обработки админских кнопок
    if (
        data.startswith("admin_")
        or data.startswith("approve:")
        or data.startswith("reject:")
        or data.startswith("orderadmin:")
    ) and user_id != ADMIN_ID:
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
            f"Выбери способ оплаты:",
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

            context.user_data["receipt_order"] = order_id

            await query.edit_message_text(
                f"💳 Заказ №{order_id}\n\n"
                f"Сумма: {amount} ₽\n\n"
                f"Реквизиты:\n{CARD_DETAILS}\n\n"
                f"После оплаты отправь фото или файл чека.\n"
                f"Для отмены: /cancel"
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

            context.user_data["receipt_order"] = order_id

            await query.edit_message_text(
                f"🪙 Заказ №{order_id}\n\n"
                f"Сумма: {amount} BC\n\n"
                f"Реквизиты:\n{BC_DETAILS}\n\n"
                f"После оплаты отправь фото или файл чека.\n"
                f"Для отмены: /cancel"
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

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order_id}")],
            [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")],
            [InlineKeyboardButton("⬅️ Заказы", callback_data="admin_orders")],
        ])

        await query.edit_message_text(
            f"Заказ №{order_id}\n"
            f"Пользователь: {order['user_id']}\n"
            f"Товар: {order['product_name']}\n"
            f"Сумма: {order['amount']} {order['currency']}\n"
            f"Оплата: {order['payment_method']}\n"
            f"Статус: {order['status']}",
            reply_markup=keyboard,
        )
        return

    if data.startswith("approve:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)

        if not order:
            await query.edit_message_text("Заказ не найден.")
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

        update_order_status(order_id, "Отклонён")

        await context.bot.send_message(
            order["user_id"],
            f"❌ Заказ №{order_id} отклонён. Обратись в поддержку, если возникла ошибка.",
        )

        await query.edit_message_text(
            f"Заказ №{order_id} отклонён."
        )
        return

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

    if data == "admin_add":
        context.user_data["admin_action"] = "add_name"
        await query.edit_message_text("Введи название товара:")
        return

    if data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await query.edit_message_text("Отправь текст рассылки:")
        return
      # ==================================
# ОПЛАТА И ЧЕКИ
# ==================================

async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user:
        return

    order_id = context.user_data.get("waiting_receipt")

    if not order_id:
        return

    file_id = None

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Отправь фото или файл с чеком.")
        return

    order = get_order(order_id)

    if not order or order["user_id"] != user.id:
        context.user_data.pop("waiting_receipt", None)
        await update.message.reply_text("Заказ не найден.")
        return

    save_receipt(order_id, file_id)
    context.user_data.pop("waiting_receipt", None)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")
        ]
    ])

    caption = (
        f"🧾 Новый чек\n\n"
        f"Заказ: #{order_id}\n"
        f"Пользователь: {user.full_name}\n"
        f"ID: {user.id}"
    )

    if update.message.photo:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=caption,
            reply_markup=keyboard
        )
    else:
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=file_id,
            caption=caption,
            reply_markup=keyboard
        )

    await update.message.reply_text(
        "✅ Чек отправлен администратору. Дождись проверки."
    )


# ==================================
# TELEGRAM STARS
# ==================================

async def precheckout_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query

    if not query:
        return

    try:
        order_id = int(query.invoice_payload)
        order = get_order(order_id)

        valid = (
            order is not None
            and order["user_id"] == query.from_user.id
            and order["stars"] == query.total_amount
            and query.currency == "XTR"
            and order["status"] == "Создан"
        )

        if not valid:
            await query.answer(
                ok=False,
                error_message="Не удалось проверить заказ. Создай новый заказ."
            )
            return

        await query.answer(ok=True)

    except Exception:
        logging.exception("Ошибка проверки платежа Stars")
        await query.answer(
            ok=False,
            error_message="Ошибка проверки платежа."
        )


async def successful_payment_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    payment = update.message.successful_payment
    user_id = update.effective_user.id

    try:
        order_id = int(payment.invoice_payload)
        order = get_order(order_id)

        if (
            not order
            or order["user_id"] != user_id
            or order["stars"] != payment.total_amount
            or payment.currency != "XTR"
            or order["status"] != "Создан"
        ):
            await update.message.reply_text(
                "⚠️ Не удалось подтвердить платёж. Обратись в поддержку."
            )
            return

        update_order_status(order_id, "Оплачен")

        await update.message.reply_text(
            f"✅ Оплата заказа #{order_id} прошла успешно!\n"
            "Администратор проверит заказ и свяжется с тобой."
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⭐ Новый оплаченный заказ!\n\n"
                f"Заказ: #{order_id}\n"
                f"Пользователь: {user_id}\n"
                f"Сумма: {payment.total_amount} Stars"
            )
        )

    except Exception:
        logging.exception("Ошибка обработки Stars")
        await update.message.reply_text(
            "Произошла ошибка. Обратись в поддержку."
        )


# ==================================
# АДМИН: ДОБАВЛЕНИЕ ТОВАРА И РАССЫЛКА
# ==================================

async def admin_text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    message = update.message

    if not user or user.id != ADMIN_ID or not message:
        return

    action = context.user_data.get("admin_action")

    if not action:
        return

    value = message.text.strip()

    if action == "broadcast":
        context.user_data.pop("admin_action", None)

        with db() as conn:
            users = conn.execute(
                "SELECT user_id FROM users"
            ).fetchall()

        sent = 0

        for item in users:
            try:
                await context.bot.send_message(
                    chat_id=item["user_id"],
                    text=value
                )
                sent += 1
            except Exception:
                logging.exception(
                    "Не удалось отправить сообщение пользователю %s",
                    item["user_id"]
                )

        await message.reply_text(
            f"📢 Рассылка завершена.\nОтправлено: {sent}"
        )
        return

    if action == "add_name":
        context.user_data["product_name"] = value
        context.user_data["admin_action"] = "add_description"
        await message.reply_text("Напиши описание товара:")
        return

    if action == "add_description":
        context.user_data["product_description"] = value
        context.user_data["admin_action"] = "add_category"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚗 Транспорт", callback_data="addcat:Транспорт")],
            [InlineKeyboardButton("👕 Скины", callback_data="addcat:Скины")],
            [InlineKeyboardButton("💰 Вирта", callback_data="addcat:Вирта")]
        ])
        await message.reply_text(
            "Выбери категорию товара:",
            reply_markup=keyboard
        )
        return

    if action == "add_card":
        context.user_data["product_card"] = value
        context.user_data["admin_action"] = "add_bc"
        await message.reply_text("Укажи цену в BC (числом):")
        return

    if action == "add_bc":
        try:
            context.user_data["product_bc"] = int(value)
            context.user_data["admin_action"] = "add_stars"
            await message.reply_text("Укажи цену в Telegram Stars (числом):")
        except ValueError:
            await message.reply_text("Введи целое число.")
        return

    if action == "add_stars":
        try:
            stars = int(value)

            if stars < 1:
                raise ValueError

            name = context.user_data["product_name"]
            description = context.user_data["product_description"]
            category = context.user_data["product_category"]
            card_price = context.user_data["product_card"]
            bc_price = context.user_data["product_bc"]

            with db() as conn:
                conn.execute("""
                    INSERT INTO products
                    (name, description, category, card_price, bc_price, stars, active)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                """, (
                    name,
                    description,
                    category,
                    card_price,
                    bc_price,
                    stars
                ))

            context.user_data.clear()

            await message.reply_text(
                "✅ Товар успешно добавлен в каталог!"
            )

        except ValueError:
            await message.reply_text(
                "Укажи корректную цену в Stars — целое число больше нуля."
            )
        except Exception:
            logging.exception("Ошибка добавления товара")
            await message.reply_text(
                "Не удалось добавить товар. Проверь настройки базы данных."
            )


# ==================================
# ВЫБОР КАТЕГОРИИ НОВОГО ТОВАРА
# ==================================

async def add_category_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    category = query.data.split(":", 1)[1]

    if category not in ("Транспорт", "Скины", "Вирта"):
        await query.edit_message_text("Неизвестная категория.")
        return

    context.user_data["product_category"] = category
    context.user_data["admin_action"] = "add_card"

    await query.edit_message_text(
        f"Категория: {category}\n"
        "Теперь укажи цену в гривнах для оплаты картой:"
    )


# ==================================
# ЗАПУСК БОТА
# ==================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных Render.")

    if not ADMIN_ID:
        raise RuntimeError("Не задан ADMIN_ID в переменных Render.")

    if not DATABASE_URL:
        raise RuntimeError("Не задан DATABASE_URL в переменных Render.")

    init_db()

    threading.Thread(
        target=run_flask,
        daemon=True
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("admin", admin))

    application.add_handler(
        CallbackQueryHandler(add_category_handler, pattern=r"^addcat:")
    )

    application.add_handler(
        PreCheckoutQueryHandler(precheckout_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_handler
        )
    )

    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.ALL,
            receipt_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text_handler
        )
    )

    logging.info("FELIKS RUSSIA DONATE запущен.")
    application.run_polling()


if __name__ == "__main__":
    main()
      
  
