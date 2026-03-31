import json
import os
import random
from datetime import date
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

DATA_FILE = "data.json"

FUNNY_REASONS = [
    "Звёзды сошлись именно сегодня для {name} ⭐",
    "Древние пророчества указывали на {name} с момента его рождения 📜",
    "{name} слишком долго смотрел на свой телефон во время еды 📱",
    "Рандом был подкуплен {name} ещё в прошлом году 💸",
    "Сам Илон Маск указал на {name} как на достойного кандидата 🚀",
    "ИИ просчитал 17 миллионов вариантов будущего — во всех пидор {name} 🤖",
    "{name} забыл поставить лайк под последним постом группы 👎",
    "Карты Таро не оставили сомнений — {name} 🃏",
    "Меркурий в ретрограде, а значит — {name} 🪐",
    "{name} ел пиццу с ананасом — отсюда и результат 🍕",
    "Судьба давно готовила {name} к этому моменту, и вот он настал 🎭",
    "ChatGPT тоже проголосовал за {name} 🗳️",
    "{name} — выбор сердца, души и вселенной ❤️",
    "Монетка упала орлом, а значит {name} — пидор дня 🪙",
    "По результатам тайного голосования: {name} набрал 146% голосов 📊",
]

WELCOME_MESSAGES = [
    "Добро пожаловать в рулетку, {name}! 🎰 Теперь ты участник ежедневного розыгрыша звания пидора дня. Удачи... она тебе понадобится.",
    "{name} вошёл в чат и автоматически добавлен в список кандидатов на звание пидора дня 🏆 Поздравляем!",
    "О, {name}! Ты как раз вовремя — у нас тут ежедневная лотерея 🎟️ Выигрыш гарантирован каждому... рано или поздно.",
    "Привет, {name}! 👋 Добро пожаловать в наш уютный чат, где каждый день кто-то становится пидором. Сегодня это можешь быть ты!",
    "{name} присоединился к игре ☠️ Барабан крутится, шарик катится... Добро пожаловать в рулетку пидора дня!",
]


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_chat_data(data: dict, chat_id: str) -> dict:
    if chat_id not in data:
        data[chat_id] = {"members": {}, "history": {}, "stats": {}}
    return data[chat_id]


def get_display_name(user) -> str:
    if user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name}"
    return user.first_name or user.username or str(user.id)


async def register_member(chat, user_id: str, name: str, username) -> bool:
    """Добавляет участника. Возвращает True если участник новый."""
    is_new = user_id not in chat["members"]
    chat["members"][user_id] = {"name": name, "username": username}
    return is_new


async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_chat:
        return
    if update.effective_chat.type == "private":
        return

    user = update.effective_user
    if user.is_bot:
        return

    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)

    is_new = await register_member(chat, str(user.id), get_display_name(user), user.username)
    save_data(data)

    if is_new:
        msg = random.choice(WELCOME_MESSAGES).format(name=get_display_name(user))
        await update.message.reply_text(msg)


async def new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Срабатывает когда кто-то вступает в группу."""
    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)

    for user in update.message.new_chat_members:
        if user.is_bot:
            continue
        is_new = await register_member(chat, str(user.id), get_display_name(user), user.username)
        if is_new:
            msg = random.choice(WELCOME_MESSAGES).format(name=get_display_name(user))
            await update.message.reply_text(msg)

    save_data(data)


async def pidor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)
    today = str(date.today())

    data = load_data()
    chat = get_chat_data(data, chat_id)

    # Явно регистрируем вызывающего, если его ещё нет
    user = update.effective_user
    is_new = await register_member(chat, str(user.id), get_display_name(user), user.username)
    if is_new:
        msg = random.choice(WELCOME_MESSAGES).format(name=get_display_name(user))
        await update.message.reply_text(msg)

    members = chat["members"]
    if len(members) < 2:
        await update.message.reply_text(
            f"Пока ты один в списке участников, {get_display_name(user)}. "
            "Пусть остальные напишут что-нибудь в чат или вызовут /pidor!"
        )
        save_data(data)
        return

    # Уже выбирали сегодня?
    if today in chat["history"]:
        winner_id = chat["history"][today]
        winner = members.get(winner_id, {})
        name = winner.get("name", "Неизвестный")
        await update.message.reply_text(
            f"Пидор дня уже выбран! 🏆\n\nСегодня это — {name}\n\nРезультат меняется завтра."
        )
        save_data(data)
        return

    # Выбираем победителя
    winner_id = random.choice(list(members.keys()))
    winner = members[winner_id]
    name = winner["name"]

    chat["history"][today] = winner_id
    chat["stats"][winner_id] = chat["stats"].get(winner_id, 0) + 1

    save_data(data)

    reason = random.choice(FUNNY_REASONS).format(name=name)
    await update.message.reply_text(
        f"🔍 Ищем пидора дня...\n\n{reason}\n\n🏆 Пидор дня — {name}!"
    )


async def pidorstat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)

    data = load_data()
    chat = get_chat_data(data, chat_id)

    stats = chat["stats"]
    members = chat["members"]

    if not stats:
        await update.message.reply_text("Статистика пока пуста. Запусти /pidor!")
        return

    sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)

    lines = ["🏆 Зал славы пидоров:\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, count) in enumerate(sorted_stats):
        member = members.get(uid, {})
        name = member.get("name", f"Пользователь {uid}")
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {name} — {count} раз(а)")

    await update.message.reply_text("\n".join(lines))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Установи переменную окружения TELEGRAM_BOT_TOKEN")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("pidor", pidor))
    app.add_handler(CommandHandler("pidorstat", pidorstat))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_member))

    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
