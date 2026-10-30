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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

CARD_DETAILS = os.getenv("CARD_DETAILS", "Реквизиты уточняются")
BC_DETAILS = os.getenv("BC_DETAILS", "Реквизиты BC уточняются")
SUPPORT_TEXT = os.getenv("SUPPORT_TEXT", "Обратитесь в поддержку проекта.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return "FELIKS RUSSIA DONATE ONLINE", 200


@app.route("/health")
def health():
    return "OK", 200


def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))


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
                photo_file_id TEXT,
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
                quantity INTEGER DEFAULT 1,
                amount DOUBLE PRECISION DEFAULT 0,
                currency TEXT DEFAULT '',
                payment_method TEXT DEFAULT '',
                status TEXT DEFAULT 'Создан',
                receipt_file_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS photo_file_id TEXT
        """)

        conn.execute("""
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1
        """)


def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET username = EXCLUDED.username,
                          first_name = EXCLUDED.first_name
        """, (user.id, user.username, user.first_name))


def get_products(category=None, all_products=False):
    with db() as conn:
        if category:
            result = conn.execute("""
                SELECT * FROM products
                WHERE category = %s AND (%s OR active = TRUE)
                ORDER BY id DESC
            """, (category, all_products))
        else:
            result = conn.execute("""
                SELECT * FROM products
                WHERE %s OR active = TRUE
                ORDER BY id DESC
            """, (all_products,))
        return result.fetchall()


def get_product(product_id, all_products=False):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM products
            WHERE id = %s AND (%s OR active = TRUE)
        """, (product_id, all_products)).fetchone()


def add_product(data):
    with db() as conn:
        result = conn.execute("""
            INSERT INTO products
            (name, description, category, card_price,
             bc_price, stars_price, photo_file_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            data["name"],
            data["description"],
            data["category"],
            data["card_price"],
            data["bc_price"],
            data["stars_price"],
            data.get("photo_file_id"),
        )).fetchone()
        return result["id"]


def edit_product(product_id, field, value):
    allowed = (
        "name", "description", "category",
        "card_price", "bc_price", "stars_price",
        "photo_file_id",
    )
    if field not in allowed:
        raise ValueError("Недопустимое поле")

    with db() as conn:
        conn.execute(
            f"UPDATE products SET {field} = %s WHERE id = %s",
            (value, product_id),
        )


def delete_product(product_id):
    with db() as conn:
        conn.execute("""
            UPDATE products SET active = FALSE
            WHERE id = %s
        """, (product_id,))


def create_order(user_id, product, quantity):
    with db() as conn:
        result = conn.execute("""
            INSERT INTO orders
            (user_id, product_id, product_name, quantity)
            VALUES (%s,%s,%s,%s)
            RETURNING id
        """, (
            user_id,
            product["id"],
            product["name"],
            quantity,
        )).fetchone()
        return result["id"]


def get_order(order_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM orders WHERE id = %s
        """, (order_id,)).fetchone()


def update_order(order_id, amount, currency, method, status):
    with db() as conn:
        conn.execute("""
            UPDATE orders
            SET amount = %s,
                currency = %s,
                payment_method = %s,
                status = %s
            WHERE id = %s
        """, (amount, currency, method, status, order_id))


def update_status(order_id, status):
    with db() as conn:
        conn.execute("""
            UPDATE orders SET status = %s WHERE id = %s
        """, (status, order_id))


def save_receipt(order_id, file_id):
    with db() as conn:
        conn.execute("""
            UPDATE orders
            SET receipt_file_id = %s,
                status = 'Чек отправлен'
            WHERE id = %s
        """, (file_id, order_id))
        


CATEGORIES = ["Транспорт", "Скины", "Вирта"]


def is_admin(user_id):
    return user_id == ADMIN_ID


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Магазин", callback_data="shop")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")]
    ])


def shop_menu():
    keyboard = [
        [InlineKeyboardButton(category, callback_data=f"category:{category}")]
        for category in CATEGORIES
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="home")])
    return InlineKeyboardMarkup(keyboard)


def product_keyboard(product_id, quantity=1):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"qty:{product_id}:-1"),
            InlineKeyboardButton(str(quantity), callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"qty:{product_id}:+1")
        ],
        [InlineKeyboardButton("💳 Оплата картой", callback_data=f"paycard:{product_id}")],
        [InlineKeyboardButton("🪙 Оплата BC", callback_data=f"paybc:{product_id}")],
        [InlineKeyboardButton("⭐ Telegram Stars", callback_data=f"paystars:{product_id}")],
        [InlineKeyboardButton("⬅️ К товарам", callback_data="shop")]
    ])


def money(value):
    return f"{value:g}"


def product_text(product, quantity):
    total_card = float(product["card_price"]) * quantity
    total_bc = float(product["bc_price"]) * quantity
    total_stars = int(product["stars_price"]) * quantity

    return (
        f"🛍 **{product['name']}**\n\n"
        f"{product['description'] or 'Описание отсутствует'}\n\n"
        f"Количество: **{quantity}**\n\n"
        f"💳 Картой: **{money(total_card)} ₽**\n"
        f"🪙 BC: **{money(total_bc)} BC**\n"
        f"⭐ Stars: **{total_stars} ⭐**"
    )


async def start(update, context):
    register_user(update.effective_user.id)

    await update.message.reply_text(
        "👋 Добро пожаловать в магазин FELIKS RUSSIA!\n\n"
        "Выберите нужный раздел:",
        reply_markup=main_menu()
    )


async def show_category(query, category):
    products = get_products(category=category)

    if not products:
        await query.edit_message_text(
            "В этой категории пока нет товаров.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="shop")]
            ])
        )
        return

    keyboard = []

    for product in products:
        keyboard.append([
            InlineKeyboardButton(
                f"{product['name']} — {money(float(product['card_price']))} ₽",
                callback_data=f"product:{product['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="shop")
    ])

    await query.edit_message_text(
        f"📂 Категория: {category}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_product(query, context, product_id):
    product = get_product(product_id)

    if not product:
        await query.answer("Товар не найден.", show_alert=True)
        return

    quantities = context.user_data.setdefault("quantities", {})
    quantity = quantities.get(product_id, 1)

    text = product_text(product, quantity)
    keyboard = product_keyboard(product_id, quantity)

    if product.get("photo_file_id"):
        await query.message.reply_photo(
            photo=product["photo_file_id"],
            caption=text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )


async def callback_router(update, context):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data == "noop":
        return

    if data == "home":
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=main_menu()
        )
        return

    if data == "shop":
        await query.edit_message_text(
            "🛒 Выберите категорию:",
            reply_markup=shop_menu()
        )
        return

    if data.startswith("category:"):
        category = data.split(":", 1)[1]
        await show_category(query, category)
        return

    if data.startswith("product:"):
        product_id = int(data.split(":")[1])
        await show_product(query, context, product_id)
        return

    if data.startswith("qty:"):
        _, product_id, change = data.split(":")
        product_id = int(product_id)

        quantities = context.user_data.setdefault("quantities", {})
        current = quantities.get(product_id, 1)

        if change == "+1":
            current = min(99, current + 1)
        else:
            current = max(1, current - 1)

        quantities[product_id] = current
        await show_product(query, context, product_id)
        return

    if data == "my_orders":
        await show_my_orders(query)
        return

    if data == "support":
        await query.edit_message_text(
            SUPPORT_TEXT,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="home")]
            ])
        )
        return

    if data.startswith("paycard:"):
        await create_manual_payment(query, context, "card")
        return

    if data.startswith("paybc:"):
        await create_manual_payment(query, context, "bc")
        return

    if data.startswith("paystars:"):
        await create_stars_payment(query, context)
        return

    if data == "admin_products":
        if not is_admin(user_id):
            return
        await admin_products(query)
        return

    if data == "admin_orders":
        if not is_admin(user_id):
            return
        await admin_orders(query)
        return

    if data == "admin_stats":
        if not is_admin(user_id):
            return
        await admin_stats(query)
        return

    if data == "admin_add":
        if not is_admin(user_id):
            return
        context.user_data["admin_state"] = {"step": "name"}
        await query.message.reply_text("Введите название нового товара:")
        return

    if data.startswith("manage:"):
        if not is_admin(user_id):
            return
        await manage_product(query, int(data.split(":")[1]))
        return

    if data.startswith("edit:"):
        if not is_admin(user_id):
            return
        _, product_id, field = data.split(":")
        context.user_data["admin_state"] = {
            "step": "edit",
            "product_id": int(product_id),
            "field": field
        }
        await query.message.reply_text("Отправьте новое значение:")
        return

    if data.startswith("delete:"):
        if not is_admin(user_id):
            return
        product_id = int(data.split(":")[1])
        delete_product(product_id)
        await query.edit_message_text("✅ Товар отключён.")
        return

    if data.startswith("approve:"):
        if not is_admin(user_id):
            return
        await approve_order(query, int(data.split(":")[1]))
        return

    if data.startswith("reject:"):
        if not is_admin(user_id):
            return
        await reject_order(query, int(data.split(":")[1]))
        return

    if data.startswith("order:"):
        if not is_admin(user_id):
            return
        await admin_order_detail(query, int(data.split(":")[1]))
        return
        async def create_manual_payment(query, context, method):
    data = query.data
    product_id = int(data.split(":")[1])

    product = get_product(product_id)
    if not product:
        await query.message.reply_text("❌ Товар не найден.")
        return

    quantity = context.user_data.get("quantities", {}).get(product_id, 1)
    order_id = create_order(query.from_user.id, product, quantity)

    if not order_id:
        await query.message.reply_text("❌ Не удалось создать заказ.")
        return

    if method == "card":
        amount = float(product["card_price"]) * quantity
        details = CARD_DETAILS
        currency = "₽"
        title = "💳 Оплата банковской картой"
    else:
        amount = float(product["bc_price"]) * quantity
        details = BC_DETAILS
        currency = "BC"
        title = "🪙 Оплата BC"

    context.user_data["waiting_receipt"] = order_id

    await query.message.reply_text(
        f"{title}\n\n"
        f"📦 Товар: {product['name']}\n"
        f"🔢 Количество: {quantity}\n"
        f"💰 Сумма: {money(amount)} {currency}\n\n"
        f"Реквизиты для оплаты:\n{details}\n\n"
        "После оплаты отправьте сюда фотографию или файл чека.\n"
        f"Номер заказа: #{order_id}"
    )


async def create_stars_payment(query, context):
    product_id = int(query.data.split(":")[1])
    product = get_product(product_id)

    if not product:
        await query.message.reply_text("❌ Товар не найден.")
        return

    quantity = context.user_data.get("quantities", {}).get(product_id, 1)
    stars_price = int(product["stars_price"]) * quantity

    if stars_price <= 0:
        await query.message.reply_text("❌ Оплата Stars для этого товара недоступна.")
        return

    order_id = create_order(query.from_user.id, product, quantity)

    if not order_id:
        await query.message.reply_text("❌ Не удалось создать заказ.")
        return

    payload = f"order_{order_id}_{query.from_user.id}"

    context.bot_data.setdefault("stars_orders", {})[payload] = {
        "order_id": order_id,
        "user_id": query.from_user.id
    }

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=product["name"][:32],
        description=f"Покупка {product['name']} x{quantity}"[:255],
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=product["name"][:32],
                amount=stars_price
            )
        ]
    )


async def pre_checkout_handler(update, context):
    query = update.pre_checkout_query
    payload = query.invoice_payload

    order = context.bot_data.get("stars_orders", {}).get(payload)

    if not order or order["user_id"] != query.from_user.id:
        await query.answer(
            ok=False,
            error_message="Заказ не найден. Создайте новый заказ."
        )
        return

    await query.answer(ok=True)


async def successful_payment_handler(update, context):
    payment = update.message.successful_payment
    payload = payment.invoice_payload

    order = context.bot_data.get("stars_orders", {}).get(payload)

    if not order:
        await update.message.reply_text(
            "Оплата получена, но заказ не найден. Обратитесь в поддержку."
        )
        return

    order_id = order["order_id"]

    update_status(order_id, "Оплачен")

    await update.message.reply_text(
        f"✅ Оплата успешно получена!\n"
        f"Номер заказа: #{order_id}\n\n"
        "Заказ передан на обработку."
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "⭐ Получена оплата Telegram Stars!\n"
                f"Заказ: #{order_id}\n"
                f"Пользователь: {update.effective_user.id}\n"
                f"Сумма: {payment.total_amount} ⭐"
            )
        )
    except Exception:
        logging.exception("Не удалось отправить уведомление админу")

    context.bot_data.get("stars_orders", {}).pop(payload, None)


async def media_handler(update, context):
    user_id = update.effective_user.id
    waiting_order = context.user_data.get("waiting_receipt")

    if not waiting_order:
        return

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    else:
        await update.message.reply_text(
            "Отправьте фотографию или файл чека."
        )
        return

    save_receipt(waiting_order, file_id)

    update_status(waiting_order, "Чек отправлен")
    context.user_data.pop("waiting_receipt", None)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"approve:{waiting_order}"
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject:{waiting_order}"
            )
        ]
    ])

    order = get_order(waiting_order)

    caption = (
        f"🧾 Новый чек!\n"
        f"Заказ: #{waiting_order}\n"
        f"Пользователь: {user_id}\n"
        f"Статус: Чек отправлен"
    )

    if order:
        caption += (
            f"\nТовар: {order.get('product_name', 'Неизвестно')}"
            f"\nКоличество: {order.get('quantity', 1)}"
        )

    if file_type == "photo":
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
        "✅ Чек отправлен администратору!\n"
        "Ожидайте проверки оплаты."
    )


async def approve_order(query, order_id):
    order = get_order(order_id)

    if not order:
        await query.message.reply_text("❌ Заказ не найден.")
        return

    update_status(order_id, "Оплачен")

    try:
        await context_send_message(
            query,
            order["user_id"],
            f"✅ Ваш заказ #{order_id} подтверждён!\n"
            "Спасибо за покупку FELIKS RUSSIA."
        )
    except Exception:
        logging.exception("Ошибка уведомления пользователя")

    await query.edit_message_caption(
        caption=f"✅ Заказ #{order_id} подтверждён."
    )


async def reject_order(query, order_id):
    order = get_order(order_id)

    if not order:
        await query.message.reply_text("❌ Заказ не найден.")
        return

    update_status(order_id, "Отклонён")

    try:
        await context_send_message(
            query,
            order["user_id"],
            f"❌ Оплата заказа #{order_id} отклонена.\n"
            "Если произошла ошибка, обратитесь в поддержку."
        )
    except Exception:
        logging.exception("Ошибка уведомления пользователя")

    await query.edit_message_caption(
        caption=f"❌ Заказ #{order_id} отклонён."
    )


async def context_send_message(query, user_id, text):
    await query.get_bot().send_message(
        chat_id=user_id,
        text=text
    )
