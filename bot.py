
import os
import sqlite3
import logging
from flask import Flask
from threading import Thread
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "donate.db")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "FELIKS RUSSIA DONATE: online"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                price TEXT NOT NULL DEFAULT 'Уточнить',
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
                status TEXT DEFAULT 'Новая'
            )
        """)
        count = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count == 0:
            vehicles = [
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
            ]
            skins = [
                ("Скины", "София", "150 ₽"),
                ("Скины", "Лео", "230 ₽"),
                ("Скины", "Хиш Мил", "129 ₽"),
                ("Скины", "Максимка", "349 ₽"),
            ]
            for category, name, price in vehicles + skins:
                c.execute(
                    "INSERT INTO products(category,name,price) VALUES(?,?,?)",
                    (category, name, price)
                )

def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🚘 Транспорт"), KeyboardButton("👕 Скины")],
        [KeyboardButton("💰 Покупка вирты"), KeyboardButton("🎁 Скидки")],
        [KeyboardButton("📦 Мои заказы"), KeyboardButton("ℹ️ Помощь")]
    ], resize_keyboard=True)

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("action", None)
    context.user_data.pop("product_id", None)
    await update.message.reply_text(
        "🎮 Добро пожаловать в FELIKS RUSSIA | DONATE!\n\n"
        "Выбери нужный раздел в меню ниже.",
        reply_markup=main_menu()
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    context.user_data.pop("action", None)
    await update.message.reply_text("🛠 Админ-панель:", reply_markup=admin_menu())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("action", None)
    context.user_data.pop("product_id", None)
    await update.message.reply_text(
        "Действие отменено. Выбери раздел меню.",
        reply_markup=main_menu()
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = (msg.text or "").strip()
    action = context.user_data.get("action")

    # Режим редактирования принимает текст только после явного действия в админке.
    if action and is_admin(update):
        if action == "add_name":
            context.user_data["new_name"] = text
            context.user_data["action"] = "add_price"
            await msg.reply_text("Введи цену товара, например 150 ₽.")
            return
        if action == "add_price":
            context.user_data["new_price"] = text
            context.user_data["action"] = "add_category"
            await msg.reply_text("Введи категорию: Транспорт, Скины или Вирта.")
            return
        if action == "add_category":
            category = text
            if category.lower() in ("вирта", "валюта", "игровая валюта"):
                category = "Вирта"
            if category.lower() in ("транспорт", "машины", "тс"):
                category = "Транспорт"
            if category.lower() in ("скины", "скин"):
                category = "Скины"
            with db() as c:
                c.execute(
                    "INSERT INTO products(category,name,price) VALUES(?,?,?)",
                    (category, context.user_data["new_name"],
                     context.user_data["new_price"])
                )
            context.user_data.pop("action", None)
            await msg.reply_text("✅ Товар добавлен!", reply_markup=admin_menu())
            return

        if action == "edit_name":
            with db() as c:
                c.execute("UPDATE products SET name=? WHERE id=?",
                          (text, context.user_data["product_id"]))
            context.user_data.pop("action", None)
            await msg.reply_text("✅ Название изменено.", reply_markup=admin_menu())
            return
        if action == "edit_price":
            with db() as c:
                c.execute("UPDATE products SET price=? WHERE id=?",
                          (text, context.user_data["product_id"]))
            context.user_data.pop("action", None)
            await msg.reply_text("✅ Цена изменена.", reply_markup=admin_menu())
            return
        if action == "edit_description":
            with db() as c:
                c.execute("UPDATE products SET description=? WHERE id=?",
                          (text, context.user_data["product_id"]))
            context.user_data.pop("action", None)
            await msg.reply_text("✅ Описание изменено.", reply_markup=admin_menu())
            return

    # Кнопки обычного меню работают даже если пользователь ранее отправлял фото.
    if text in ("/start", "🏠 Главное меню"):
        await start(update, context)
    elif text == "🚘 Транспорт":
        await msg.reply_text("🚘 Выбери транспорт:", reply_markup=product_keyboard("Транспорт"))
    elif text == "👕 Скины":
        await msg.reply_text("👕 Выбери скин:", reply_markup=product_keyboard("Скины"))
    elif text == "💰 Покупка вирты":
        await msg.reply_text(
            "💰 Покупка игровой валюты\n\n"
            "Выбери доступный товар в категории «Вирта». "
            "Если список пуст, уточни количество и цену у владельца: @crmp_feliks.",
            reply_markup=product_keyboard("Вирта")
        )
    elif text == "🎁 Скидки":
        await msg.reply_text(
            "🎁 СКИДКИ И НОВОСТИ\n\n"
            "Здесь публикуется информация о новых товарах, изменениях ассортимента, "
            "актуальных ценах и специальных предложениях.\n\n"
            "По вопросам скидок: @qelzee"
        )
    elif text == "ℹ️ Помощь":
        await msg.reply_text(
            "ℹ️ ПОМОЩЬ — FELIKS RUSSIA | DONATE\n\n"
            "🚘 Транспорт — просмотр транспорта и оформление заявки.\n"
            "👕 Скины — выбор скина и оформление заявки.\n"
            "💰 Покупка вирты — игровая валюта; количество и стоимость уточняются.\n"
            "🎁 Скидки — новости, новые товары и актуальные цены.\n"
            "📦 Мои заказы — просмотр твоих заявок.\n\n"
            "После выбора товара нажми «Оформить заказ». "
            "Заявка отправится администратору на подтверждение.\n"
            "Оплата картой может быть недоступна. Не отправляй данные банковской карты в чат.\n\n"
            "Владелец по вопросам BC/⭐: @crmp_feliks\n"
            "Скидки: @qelzee\n"
            "Для отмены текущего действия напиши /cancel."
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
                f"#{o['id']} — {o['product']} — {o['status']}" for o in orders
            )
            await msg.reply_text("📦 Твои заказы:\n\n" + body)
    elif text == "/admin":
        await admin(update, context)
    elif text == "/cancel":
        await cancel(update, context)
    else:
        await msg.reply_text("Выбери действие кнопками меню.", reply_markup=main_menu())

async def product_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        pid = int(q.data.split(":")[1])
    except (IndexError, ValueError):
        return
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not p:
        await q.edit_message_text("Этот товар больше недоступен.")
        return
    context.user_data["selected_product"] = pid
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
                photo=p["photo"], caption=caption, reply_markup=keyboard
            )
        except Exception:
            await q.message.reply_text(caption, reply_markup=keyboard)
    else:
        await q.message.reply_text(caption, reply_markup=keyboard)

async def order_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":")[1])
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            await q.message.reply_text("Товар недоступен.")
            return
        u = q.from_user
        cur = c.execute(
            "INSERT INTO orders(user_id,username,product) VALUES(?,?,?)",
            (u.id, u.username or u.full_name, f"{p['name']} — {p['price']}")
        )
        order_id = cur.lastrowid
    await q.message.reply_text(f"✅ Заявка #{order_id} создана. Ожидай подтверждения.")
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"🛒 Новый заказ #{order_id}\n"
                f"Товар: {p['name']}\nЦена: {p['price']}\n"
                f"Пользователь: {q.from_user.full_name} (@{q.from_user.username or 'нет'})",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve:{order_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{order_id}")
                ]])
            )
        except Exception:
            log.exception("Не удалось уведомить администратора")

async def admin_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update):
        await q.message.reply_text("⛔ Нет доступа.")
        return
    data = q.data

    if data == "admin:close":
        await q.edit_message_text("Админ-панель закрыта.")
        return
    if data == "admin:products":
        with db() as c:
            products = c.execute("SELECT * FROM products ORDER BY id").fetchall()
        text = "\n".join(
            f"#{p['id']} [{p['category']}] {p['name']} — {p['price']}"
            for p in products
        ) or "Товаров нет."
        await q.message.reply_text(text, reply_markup=admin_menu())
        return
    if data == "admin:add":
        context.user_data["action"] = "add_name"
        await q.message.reply_text("Введи название нового товара. /cancel — отмена.")
        return
    if data in ("admin:editlist", "admin:photolist", "admin:deletelist"):
        with db() as c:
            products = c.execute("SELECT * FROM products ORDER BY id").fetchall()
        buttons = []
        for p in products:
            if data == "admin:editlist":
                callback = f"edit:{p['id']}"
            elif data == "admin:photolist":
                callback = f"photo:{p['id']}"
            else:
                callback = f"delete:{p['id']}"
            buttons.append([InlineKeyboardButton(
                f"#{p['id']} {p['name']}", callback_data=callback
            )])
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:home")])
        await q.message.reply_text("Выбери товар:", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data == "admin:orders":
        with db() as c:
            orders = c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 20").fetchall()
        text = "\n".join(
            f"#{o['id']} {o['product']} | {o['status']} | {o['username']}"
            for o in orders
        ) or "Заказов пока нет."
        await q.message.reply_text(text, reply_markup=admin_menu())
        return
    if data == "admin:home":
        await q.message.reply_text("🛠 Админ-панель:", reply_markup=admin_menu())
        return

    if data.startswith("edit:"):
        pid = int(data.split(":")[1])
        context.user_data["product_id"] = pid
        await q.message.reply_text(
            "Что изменить?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Название", callback_data="field:name")],
                [InlineKeyboardButton("Цена", callback_data="field:price")],
                [InlineKeyboardButton("Описание", callback_data="field:description")],
                [InlineKeyboardButton("Категория", callback_data="field:category")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="admin:editlist")]
            ])
        )
        return
    if data.startswith("field:"):
        field = data.split(":")[1]
        context.user_data["action"] = "edit_" + field
        if field == "category":
            await q.message.reply_text("Введи новую категорию: Транспорт, Скины или Вирта.")
        else:
            await q.message.reply_text("Введи новое значение. Для отмены — /cancel.")
        return
    if data.startswith("photo:"):
        pid = int(data.split(":")[1])
        context.user_data["action"] = "photo"
        context.user_data["product_id"] = pid
        await q.message.reply_text("Теперь отправь фотографию этого товара. /cancel — отмена.")
        return
    if data.startswith("delete:"):
        pid = int(data.split(":")[1])
        with db() as c:
            c.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        await q.message.reply_text("🗑 Товар скрыт из каталога.", reply_markup=admin_menu())
        return
    if data.startswith(("approve:", "reject:")):
        order_id = int(data.split(":")[1])
        status = "Подтверждён" if data.startswith("approve:") else "Отклонён"
        with db() as c:
            order = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            c.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        if order:
            try:
                await context.bot.send_message(
                    int(order["user_id"]), f"Статус заказа #{order_id}: {status}."
                )
            except Exception:
                pass
        await q.message.reply_text(f"Заказ #{order_id}: {status}.")
        return

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if context.user_data.get("action") != "photo":
        return
    if not update.message.photo:
        await update.message.reply_text("Нужно отправить фотографию. Или /cancel для отмены.")
        return
    pid = context.user_data.get("product_id")
    photo_id = update.message.photo[-1].file_id
    with db() as c:
        c.execute("UPDATE products SET photo=? WHERE id=?", (photo_id, pid))
    context.user_data.pop("action", None)
    context.user_data.pop("product_id", None)
    await update.message.reply_text("✅ Фото карточки обновлено!", reply_markup=admin_menu())

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Ошибка бота:", exc_info=context.error)

def run_web():
    port = int(os.getenv("PORT", "10000"))
    app_web.run(host="0.0.0.0", port=port)

def main():
    if not TOKEN:
        raise RuntimeError("Добавь переменную BOT_TOKEN в Environment на Render.")
    if ADMIN_ID == 0:
        raise RuntimeError("Добавь ADMIN_ID — свой числовой Telegram ID — в Environment.")
    init_db()
    Thread(target=run_web, daemon=True).start()

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(product_click, pattern=r"^product:\d+$"))
    application.add_handler(CallbackQueryHandler(order_click, pattern=r"^order:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_click, pattern=r"^(admin:|edit:|field:|photo:|delete:|approve:|reject:)"))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
                
