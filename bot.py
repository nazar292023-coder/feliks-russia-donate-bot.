
import os
import sqlite3
import logging
from threading import Thread
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PAYMENT_DETAILS = os.getenv(
    "PAYMENT_DETAILS",
    "Реквизиты уточни у администратора."
)
DB = "donate_orders.db"

app_web = Flask(__name__)

@app_web.get("/")
def home():
    return "FELIKS RUSSIA DONATE BOT is running"

# Транспорт: цена в рублях и BC за одну единицу
VEHICLES = [
    ("V Minibus Halloween", 670, 19900),
    ("BMW M5 F90 PPS", 890, 2300),
    ("Smart Fortwo 2", 4300, 6700),
    ("SCG 003", 1200, 5200),
    ("Ferrari 812 SuperFast", 430, 1340),
    ("УРАЛ ИМЗ 8 Блиц", 1900, 8900),
    ("ГАЗ ТИГР", 50, 1900),
    ("ЗИЛ 4104", 129, 1239),
    ("BMW M5 CS", 500, 4300),
    ("KIA CARNEVAL", 670, 5700),
]

# Черновики заказа в текущем сеансе
user_states = {}


def db():
    conn = sqlite3.connect(DB)
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
                kind TEXT NOT NULL,
                product TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price_rub INTEGER,
                price_bc INTEGER,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Купить BC", callback_data="buy_bc")],
        [InlineKeyboardButton("🚘 Купить транспорт", callback_data="buy_vehicle")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
    ])


def admin_order_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Оформить заказ", callback_data=f"approve:{order_id}"),
        InlineKeyboardButton("❌ Не оформлять", callback_data=f"reject:{order_id}"),
    ]])


def payment_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Я оплатил", callback_data=f"paid:{order_id}")
    ]])


def payment_review_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"confirm:{order_id}"),
        InlineKeyboardButton("❌ Отклонить оплату", callback_data=f"unpaid:{order_id}"),
    ]])


def get_order(order_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 Добро пожаловать в FELIKS RUSSIA | DONATE!\n\n"
        "Здесь можно оформить заказ BC или транспорта.\n"
        "Оплата и выдача проверяются администратором вручную.",
        reply_markup=main_keyboard()
    )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or ADMIN_ID == 0:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN "
            "('pending','payment_wait','paid_pending')"
        ).fetchone()[0]
    await update.message.reply_text(
        f"🛠 Панель администратора\n"
        f"Всего заказов: {total}\n"
        f"Ожидают обработки: {pending}\n\n"
        f"/orders — последние заказы\n/stats — статистика"
    )


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or ADMIN_ID == 0:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 10"
        ).fetchall()
    if not rows:
        await update.message.reply_text("Заказов пока нет.")
        return
    for o in rows:
        await update.message.reply_text(
            f"Заказ #{o['id']}\n"
            f"Игровой ник: {o['nickname']}\n"
            f"Товар: {o['product']}\n"
            f"Количество: {o['quantity']}\n"
            f"Статус: {o['status']}\n"
            f"Telegram ID: {o['user_id']}"
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or ADMIN_ID == 0:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM orders GROUP BY status"
        ).fetchall()
    result = "\n".join(f"{r['status']}: {r['n']}" for r in rows)
    await update.message.reply_text("📊 Статистика заказов\n" + (result or "Заказов нет."))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data == "buy_bc":
        user_states[uid] = {"step": "nickname", "kind": "bc"}
        await q.message.reply_text("💎 Напиши свой игровой ник:")
        return

    if data == "buy_vehicle":
        buttons = []
        for i, (name, rub, bc) in enumerate(VEHICLES):
            buttons.append([InlineKeyboardButton(
                f"{name} — {rub} ₽ / {bc:,} BC".replace(",", " "),
                callback_data=f"vehicle:{i}"
            )])
        buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="back")])
        await q.message.reply_text(
            "🚘 Выбери транспорт:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data == "back":
        await q.message.reply_text("Главное меню:", reply_markup=main_keyboard())
        return

    if data == "my_orders":
        with db() as conn:
            rows = conn.execute(
                "SELECT id, product, quantity, status, created_at "
                "FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (uid,)
            ).fetchall()
        if not rows:
            await q.message.reply_text("У тебя пока нет заказов.")
        else:
            text = "\n\n".join(
                f"Заказ #{r['id']}: {r['product']}\n"
                f"Количество: {r['quantity']}\n"
                f"Статус: {r['status']}\nДата: {r['created_at']}"
                for r in rows
            )
            await q.message.reply_text(text)
        return

    if data.startswith("vehicle:"):
        i = int(data.split(":")[1])
        if i < 0 or i >= len(VEHICLES):
            await q.message.reply_text("Неизвестный транспорт.")
            return
        name, rub, bc = VEHICLES[i]
        user_states[uid] = {
            "step": "nickname", "kind": "vehicle",
            "product": name, "rub": rub, "bc": bc
        }
        await q.message.reply_text(
            f"Выбран: {name}\nЦена за 1 шт.: {rub} ₽ или {bc:,} BC\n\n"
            "Напиши свой игровой ник:".replace(",", " ")
        )
        return

    # Действия администратора
    if data.startswith(("approve:", "reject:", "confirm:", "unpaid:")):
        if uid != ADMIN_ID or ADMIN_ID == 0:
            await q.message.reply_text("⛔ Нет доступа.")
            return

        action, order_id_str = data.split(":")
        order_id = int(order_id_str)
        order = get_order(order_id)
        if not order:
            await q.message.reply_text("Заказ не найден.")
            return

        if action == "approve":
            if order["status"] != "pending":
                await q.message.reply_text("Этот заказ уже обработан.")
                return
            with db() as conn:
                conn.execute(
                    "UPDATE orders SET status='payment_wait' WHERE id=?",
                    (order_id,)
                )
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"✅ Заказ #{order_id} одобрен!\n\n"
                     f"Товар: {order['product']}\n"
                     f"Количество: {order['quantity']}\n"
                     f"Сумма: {order['price_rub']} ₽"
                     f"{' / ' + format(order['price_bc'], ',').replace(',', ' ') + ' BC' if order['price_bc'] else ''}\n\n"
                     f"Информация об оплате:\n{PAYMENT_DETAILS}\n\n"
                     "После оплаты нажми кнопку ниже. Оплату проверит администратор.",
                reply_markup=payment_keyboard(order_id)
            )
            await q.message.reply_text(f"Заказ #{order_id} одобрен.")
            return

        if action == "reject":
            if order["status"] != "pending":
                await q.message.reply_text("Этот заказ уже обработан.")
                return
            with db() as conn:
                conn.execute(
                    "UPDATE orders SET status='rejected' WHERE id=?",
                    (order_id,)
                )
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"❌ Заказ #{order_id} не был оформлен. Если это ошибка, свяжись с администратором."
            )
            await q.message.reply_text(f"Заказ #{order_id} отклонён.")
            return

        if action == "confirm":
            if order["status"] != "paid_pending":
                await q.message.reply_text("Заказ не ожидает подтверждения оплаты.")
                return
            with db() as conn:
                conn.execute(
                    "UPDATE orders SET status='paid_confirmed' WHERE id=?",
                    (order_id,)
                )
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"✅ Оплата по заказу #{order_id} подтверждена!\n"
                     "Выдача BC или транспорта выполняется вручную. Ожидай сообщение администратора."
            )
            await q.message.reply_text(
                f"Оплата заказа #{order_id} подтверждена. Не забудь выдать товар вручную."
            )
            return

        if action == "unpaid":
            with db() as conn:
                conn.execute(
                    "UPDATE orders SET status='payment_review' WHERE id=?",
                    (order_id,)
                )
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"⚠️ Оплата по заказу #{order_id} пока не подтверждена. Свяжись с администратором."
            )
            await q.message.reply_text(f"Оплата заказа #{order_id} не подтверждена.")
            return

    if data.startswith("quantity:"):
        quantity = int(data.split(":")[1])
        state = user_states.get(uid)
        if not state or state.get("step") != "quantity":
            await q.message.reply_text("Начни оформление заказа заново: /start")
            return
        if quantity < 1 or quantity > 5:
            await q.message.reply_text("Можно заказать от 1 до 5 единиц.")
            return
        await create_order(update, context, uid, state, quantity)
        user_states.pop(uid, None)
        return

    if data.startswith("paid:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)
        if not order or order["user_id"] != uid:
            await q.message.reply_text("Заказ не найден.")
            return
        if order["status"] != "payment_wait":
            await q.message.reply_text("Этот заказ не ожидает оплаты.")
            return
        with db() as conn:
            conn.execute(
                "UPDATE orders SET status='paid_pending' WHERE id=?",
                (order_id,)
            )
        await q.message.reply_text(
            "📨 Администратор получил уведомление. Оплата будет проверена вручную."
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💳 Покупатель отметил оплату!\n"
                 f"Заказ: #{order_id}\n"
                 f"Пользователь: @{order['username'] or 'без username'}\n"
                 f"Telegram ID: {order['user_id']}\n"
                 f"Игровой ник: {order['nickname']}\n"
                 f"Товар: {order['product']}\n"
                 f"Сумма: {order['price_rub']} ₽",
            reply_markup=payment_review_keyboard(order_id)
        )


async def create_order(update, context, uid, state, quantity):
    user = update.effective_user
    if state["kind"] == "vehicle":
        rub = state["rub"] * quantity
        bc = state["bc"] * quantity
        product = state["product"]
    else:
        rub = None
        bc = int(state["bc_amount"])
        product = f"{bc:,} BC".replace(",", " ")

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO orders "
            "(user_id, username, nickname, kind, product, quantity, "
            "price_rub, price_bc, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (uid, user.username, state["nickname"], state["kind"],
             product, quantity, rub, bc if state["kind"] == "vehicle" else None)
        )
        order_id = cur.lastrowid

    total_text = f"{rub} ₽ / {bc:,} BC".replace(",", " ") if rub is not None else "Цена BC уточняется администратором"
    admin_text = (
        f"🛒 Новый заказ #{order_id}\n"
        f"Пользователь: @{user.username or 'без username'}\n"
        f"Telegram ID: {uid}\n"
        f"Игровой ник: {state['nickname']}\n"
        f"Товар: {product}\n"
        f"Количество: {quantity}\n"
        f"Итого: {total_text}\n"
        f"Статус: ожидает решения"
    )
    await context.bot.send_message(
        chat_id=ADMIN_ID, text=admin_text,
        reply_markup=admin_order_keyboard(order_id)
    )
    await context.bot.send_message(
        chat_id=uid,
        text=f"📦 Заказ #{order_id} создан!\n"
             f"Товар: {product}\n"
             f"Количество: {quantity}\n"
             f"Стоимость: {total_text}\n\n"
             "Ожидай решения администратора. Пока заказ не одобрен, оплачивать его не нужно.",
        reply_markup=main_keyboard()
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = user_states.get(uid)
    if not state:
        await update.message.reply_text("Выбери действие в меню:", reply_markup=main_keyboard())
        return

    text = update.message.text.strip()

    if state["step"] == "nickname":
        if len(text) > 40:
            await update.message.reply_text("Ник слишком длинный. Введи ник до 40 символов.")
            return
        state["nickname"] = text
        if state["kind"] == "vehicle":
            state["step"] = "quantity"
            buttons = [[
                InlineKeyboardButton(str(n), callback_data=f"quantity:{n}")
                for n in range(1, 6)
            ]]
            await update.message.reply_text(
                "Выбери количество (от 1 до 5):",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            state["step"] = "bc_amount"
            await update.message.reply_text("Сколько BC хочешь купить? Напиши число, например 5000.")
        return

    if state["step"] == "bc_amount":
        try:
            amount = int(text.replace(" ", ""))
            if amount < 1 or amount > 1_000_000_000:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Введи количество BC числом, например 5000.")
            return
        state["bc_amount"] = amount
        state["step"] = "quantity"
        await create_order(update, context, uid, state, 1)
        user_states.pop(uid, None)
        return


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    caption = update.message.caption or "Без комментария"
    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE user_id=? AND status='payment_wait' "
            "ORDER BY id DESC LIMIT 1", (uid,)
        ).fetchone()
    if not order:
        await update.message.reply_text(
            "Сейчас нет заказа, ожидающего оплату. Оформи заказ через /start."
        )
        return
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=update.message.photo[-1].file_id,
        caption=f"🧾 Чек по заказу #{order['id']}\n"
                f"Игровой ник: {order['nickname']}\n"
                f"Telegram ID: {uid}\nКомментарий: {caption}",
        reply_markup=payment_review_keyboard(order["id"])
    )
    with db() as conn:
        conn.execute(
            "UPDATE orders SET status='paid_pending' WHERE id=?",
            (order["id"],)
        )
    await update.message.reply_text("Чек отправлен администратору на проверку.")


def run_web():
    port = int(os.getenv("PORT", "10000"))
    app_web.run(host="0.0.0.0", port=port, use_reloader=False)


def main():
    if not TOKEN:
        raise RuntimeError("Добавь BOT_TOKEN в переменные окружения Render.")
    if ADMIN_ID == 0:
        raise RuntimeError("Добавь ADMIN_ID в переменные окружения Render.")

    init_db()
    Thread(target=run_web, daemon=True).start()

    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("admin", admin))
    bot_app.add_handler(CommandHandler("orders", orders_command))
    bot_app.add_handler(CommandHandler("stats", stats_command))
    bot_app.add_handler(CallbackQueryHandler(callback))
    bot_app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    bot_app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
