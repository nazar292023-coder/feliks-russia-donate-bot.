import os
import sqlite3
import logging
from threading import Thread
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
DB_PATH = os.getenv("DB_PATH", "donate_orders.db")
OWNER_URL = "https://t.me/crmp_feliks"
DISCOUNT_URL = "https://t.me/qelzee"
VERSION = "2.0.0"

# Цены указаны пользователем. Цена в БС согласуется с владельцем.
VEHICLES = [
    ("Mustang Hoonigan", 150, 100),
    ("Квадроцикл", 300, 200),
    ("Лимузин", 150, 100),
    ("GAZ 2402 Halloween", 100, 50),
    ("BMW M5 CS", 250, 200),
    ("Marussia B1", 150, 100),
    ("Mercedes-AMG E63 S Wagon", 200, 150),
    ("Lamborghini Countach", 230, 150),
    ("Mercedes-Benz AMG Project One", 150, 150),
    ("BMW M5 F90 ППС", 200, 150),
    ("Porsche Spyder", 200, 200),
    ("SSG 033", 200, 150),
]

web = Flask(__name__)

@web.get("/")
def health():
    return "FELIKS RUSSIA DONATE is running", 200

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                nickname TEXT NOT NULL,
                product TEXT NOT NULL,
                price_rub INTEGER NOT NULL,
                price_stars INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vehicle_photos (
                vehicle_index INTEGER PRIMARY KEY,
                file_id TEXT NOT NULL
            )
        """)

def main_keyboard():
    return ReplyKeyboardMarkup(
        [["🚘 Каталог ТС"], ["📦 Мои заказы", "🎁 Скидки"], ["ℹ️ Помощь"]],
        resize_keyboard=True
    )

def admin_order_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{order_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")
    ]])

def payment_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплата картой", callback_data=f"paycard:{order_id}")],
        [InlineKeyboardButton("⭐ Оплата звёздами", callback_data=f"paystars:{order_id}")],
        [InlineKeyboardButton("💰 Оплата БС", callback_data=f"paybs:{order_id}")],
        [InlineKeyboardButton("⬅️ В каталог", callback_data="catalog")]
    ])

def vehicle_keyboard(index):
    next_index = (index + 1) % len(VEHICLES)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Заказать ТС", callback_data=f"order:{index}")],
        [InlineKeyboardButton("➡️ Следующее ТС", callback_data=f"vehicle:{next_index}")],
        [InlineKeyboardButton("📋 Весь каталог", callback_data="catalog")]
    ])

def photo_for_vehicle(index):
    with db() as conn:
        row = conn.execute(
            "SELECT file_id FROM vehicle_photos WHERE vehicle_index=?", (index,)
        ).fetchone()
    return row["file_id"] if row else None

async def send_vehicle(chat_id, context, index):
    name, rub, stars = VEHICLES[index]
    caption = (
        f"🚘 {name}\n\n"
        f"💵 Цена: {rub} ₽\n"
        f"⭐ Цена: {stars} звёзд\n\n"
        "Нажми «Заказать ТС», чтобы оформить заявку."
    )
    photo = photo_for_vehicle(index)
    if photo:
        await context.bot.send_photo(
            chat_id=chat_id, photo=photo, caption=caption,
            reply_markup=vehicle_keyboard(index)
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption + "\n\n📸 Фото для этой карточки ещё не добавлено.",
            reply_markup=vehicle_keyboard(index)
        )

async def show_catalog(chat_id, context):
    await context.bot.send_message(
        chat_id=chat_id,
        text="🚘 Каталог FELIKS RUSSIA DONATE\nКаждый транспорт отправляется отдельной карточкой."
    )
    for index in range(len(VEHICLES)):
        await send_vehicle(chat_id, context, index)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в FELIKS RUSSIA DONATE!\n\n"
        "Выбирай транспорт в каталоге, нажми «Заказать ТС» и введи игровой ник.\n"
        "После одобрения заказа появятся способы оплаты.",
        reply_markup=main_keyboard()
    )

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text("У тебя пока нет заказов.")
        return
    lines = ["📦 Твои последние заказы:"]
    for row in rows:
        lines.append(
            f"#{row['id']} — {row['product']}\n"
            f"Цена: {row['price_rub']} ₽ / {row['price_stars']} ⭐\n"
            f"Статус: {row['status']}"
        )
    await update.effective_message.reply_text("\n\n".join(lines))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text.strip()
    uid = update.effective_user.id

    # Режим добавления фото администратором
    if context.user_data.get("awaiting_vehicle_photo") is not None:
        await update.message.reply_text("Отправь именно фотографию транспорта, не текст.")
        return

    if message_text == "🚘 Каталог ТС":
        await show_catalog(update.effective_chat.id, context)
        return
    if message_text == "📦 Мои заказы":
        await my_orders(update, context)
        return
    if message_text == "🎁 Скидки":
        await update.message.reply_text(
            "🎁 Есть скидки!\nПо вопросам скидок: @qelzee",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Связаться по скидкам", url=DISCOUNT_URL)
            ]])
        )
        return
    if message_text == "ℹ️ Помощь":
        await update.message.reply_text(
            "1. Открой каталог и выбери ТС.\n"
            "2. Нажми «Заказать ТС» и введи игровой ник.\n"
            "3. Дождись одобрения администратора.\n"
            "4. Выбери способ оплаты.\n\n"
            "Оплата картой пока в разработке. Оплату звёздами и БС согласовывай с владельцем @crmp_feliks.",
            reply_markup=main_keyboard()
        )
        return

    state = context.user_data.get("state")
    if state and state.get("step") == "nickname":
        nickname = message_text
        if len(nickname) > 40:
            await update.message.reply_text("Ник слишком длинный. Введи ник до 40 символов.")
            return
        product = state["product"]
        rub = state["rub"]
        stars = state["stars"]
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO orders(user_id, username, nickname, product, price_rub, price_stars, status) "
                "VALUES(?,?,?,?,?,?,?)",
                (uid, update.effective_user.username or "", nickname, product, rub, stars, "pending")
            )
            order_id = cur.lastrowid
        context.user_data.pop("state", None)
        admin_text = (
            f"🛒 Новый заказ #{order_id}\n"
            f"Пользователь: @{update.effective_user.username or 'без username'}\n"
            f"Telegram ID: {uid}\n"
            f"Игровой ник: {nickname}\n"
            f"Товар: {product}\n"
            f"Цена: {rub} ₽ / {stars} ⭐\n"
            f"Статус: ожидает решения"
        )
        if ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID, text=admin_text,
                reply_markup=admin_order_keyboard(order_id)
            )
        await update.message.reply_text(
            f"📦 Заказ #{order_id} создан!\n"
            f"Товар: {product}\nИгровой ник: {nickname}\n"
            f"Стоимость: {rub} ₽ / {stars} ⭐\n\n"
            "Ожидай решения администратора. Пока заказ не одобрен, оплачивать его не нужно.",
            reply_markup=main_keyboard()
        )
        return

    await update.message.reply_text("Выбери действие в меню:", reply_markup=main_keyboard())

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Фото от владельца сохраняется как фото карточки выбранного ТС.
    index = context.user_data.get("awaiting_vehicle_photo")
    if index is not None:
        if uid != ADMIN_ID:
            await update.message.reply_text("Добавлять фото может только администратор.")
            return
        file_id = update.message.photo[-1].file_id
        with db() as conn:
            conn.execute(
                "INSERT INTO vehicle_photos(vehicle_index, file_id) VALUES(?, ?) "
                "ON CONFLICT(vehicle_index) DO UPDATE SET file_id=excluded.file_id",
                (index, file_id)
            )
        context.user_data.pop("awaiting_vehicle_photo", None)
        await update.message.reply_text(
            f"✅ Фото сохранено для ТС: {VEHICLES[index][0]}.\n"
            "Чтобы добавить следующее фото, используй /setphoto."
        )
        return
    await update.message.reply_text(
        "Чтобы добавить фото карточки, администратор должен сначала использовать /setphoto."
    )

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "catalog":
        await show_catalog(query.message.chat_id, context)
        return

    if data.startswith("vehicle:"):
        index = int(data.split(":")[1])
        await send_vehicle(query.message.chat_id, context, index)
        return

    if data.startswith("order:"):
        index = int(data.split(":")[1])
        name, rub, stars = VEHICLES[index]
        context.user_data["state"] = {
            "step": "nickname", "product": name, "rub": rub, "stars": stars
        }
        await query.message.reply_text(
            f"🛒 Заказ: {name}\n💵 {rub} ₽ / ⭐ {stars}\n\nВведи свой игровой ник:"
        )
        return

    if data.startswith("setphoto:"):
        if query.from_user.id != ADMIN_ID:
            await query.answer("Нет доступа.", show_alert=True)
            return
        index = int(data.split(":")[1])
        context.user_data["awaiting_vehicle_photo"] = index
        await query.message.reply_text(
            f"📸 Теперь отправь фотографию для карточки «{VEHICLES[index][0]}»."
        )
        return

    if data.startswith("approve:"):
        if query.from_user.id != ADMIN_ID:
            await query.answer("Нет доступа.", show_alert=True)
            return
        order_id = int(data.split(":")[1])
        with db() as conn:
            conn.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if row:
            await context.bot.send_message(
                row["user_id"],
                f"✅ Заказ #{order_id} одобрен!\n"
                f"🚘 ТС: {row['product']}\n"
                f"💵 Цена: {row['price_rub']} ₽\n"
                f"⭐ Цена: {row['price_stars']} звёзд\n\n"
                "Выбери способ оплаты:",
                reply_markup=payment_keyboard(order_id)
            )
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if data.startswith("reject:"):
        if query.from_user.id != ADMIN_ID:
            await query.answer("Нет доступа.", show_alert=True)
            return
        order_id = int(data.split(":")[1])
        with db() as conn:
            row = conn.execute("SELECT user_id FROM orders WHERE id=?", (order_id,)).fetchone()
            conn.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
        if row:
            await context.bot.send_message(
                row["user_id"], f"❌ Заказ #{order_id} отклонён администрацией."
            )
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if data.startswith("paycard:"):
        await query.message.reply_text(
            "❌ Оплата картой временно недоступна.\nФункция находится в разработке."
        )
        return

    if data.startswith("paystars:"):
        await query.message.reply_text(
            "⭐ Оплата звёздами\n\n"
            "Можешь отправить звёзды владельцу или договориться с ним о покупке заказа.\n"
            "Нажми кнопку ниже, чтобы связаться с владельцем.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👤 Написать владельцу @crmp_feliks", url=OWNER_URL)
            ]])
        )
        return

    if data.startswith("paybs:"):
        order_id = int(data.split(":")[1])
        with db() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            await query.message.reply_text("Заказ не найден.")
            return
        await query.message.reply_text(
            f"💰 Оплата БС — заказ #{order_id}\n\n"
            f"🚘 ТС: {row['product']}\n"
            f"💵 Цена: {row['price_rub']} ₽\n"
            f"⭐ Цена: {row['price_stars']} звёзд\n"
            "💰 Цена в БС: нужно согласовать с владельцем.\n\n"
            "Чтобы купить ТС за БС, напиши владельцу и согласуй стоимость.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👤 Согласовать БС с владельцем", url=OWNER_URL)
            ]])
        )
        return

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    buttons = []
    for i, (name, _, _) in enumerate(VEHICLES):
        buttons.append([InlineKeyboardButton(f"📸 {name}", callback_data=f"setphoto:{i}")])
    await update.message.reply_text(
        "🛠 Админ-панель\n\n"
        "/orders — последние заказы\n"
        "/stats — статистика\n"
        "/setphoto — добавить или заменить фото карточки ТС\n\n"
        "Нажми /setphoto и выбери ТС, затем отправь фотографию.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def setphoto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    buttons = [
        [InlineKeyboardButton(f"📸 {name}", callback_data=f"setphoto:{i}")]
        for i, (name, _, _) in enumerate(VEHICLES)
    ]
    await update.message.reply_text(
        "Выбери ТС, для которого хочешь добавить фото:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    with db() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await update.message.reply_text("Заказов пока нет.")
        return
    lines = ["📋 Последние заказы:"]
    for row in rows:
        lines.append(
            f"#{row['id']} | {row['product']} | {row['nickname']} | "
            f"{row['price_rub']} ₽ / {row['price_stars']} ⭐ | {row['status']}"
        )
    await update.message.reply_text("\n".join(lines))

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM orders WHERE status='approved'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM orders WHERE status='rejected'").fetchone()[0]
    await update.message.reply_text(
        f"📊 Статистика\nВсего заказов: {total}\n"
        f"Ожидают решения: {pending}\nОдобрено: {approved}\nОтклонено: {rejected}"
    )

def run_web():
    port = int(os.getenv("PORT", "10000"))
    web.run(host="0.0.0.0", port=port, use_reloader=False)

def main():
    if not TOKEN:
        raise RuntimeError("Добавь BOT_TOKEN в переменные окружения Render.")
    if ADMIN_ID == 0:
        raise RuntimeError("Добавь числовой ADMIN_ID в переменные окружения Render.")
    init_db()
    Thread(target=run_web, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("setphoto", setphoto_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
