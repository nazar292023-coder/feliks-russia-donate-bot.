
def get_quantity_keyboard(product_id, quantity):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [
            InlineKeyboardButton("➖", callback_data=f"qty:{product_id}:{max(1, quantity - 1)}"),
            InlineKeyboardButton(str(quantity), callback_data="qty:noop"),
            InlineKeyboardButton("➕", callback_data=f"qty:{product_id}:{quantity + 1}")
        ],
        [
            InlineKeyboardButton("🛒 Оформить заказ", callback_data=f"buy_qty:{product_id}:{quantity}")
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


def calculate_total(price, quantity):
    return price * quantity
  
