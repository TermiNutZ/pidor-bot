import asyncio
import json
import os
import random
from datetime import date
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    PollAnswerHandler, filters, ContextTypes,
)

DATA_FILE = "data.json"

BATTLE_CLOSE_SECONDS = 3 * 60 * 60  # 3 часа

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

BATTLE_QUESTIONS = [
    "Кто победит 1 на 1 на миду?",
    "Кто первый сдохнет в зомби-апокалипсисе?",
    "Кто съест больше шаурмы за раз?",
    "Кто скорее вступит в секту?",
    "Кого первым съедят в голодные времена?",
    "Кто скорее станет бомжом?",
    "У кого больше?",
    "Кто более подозрительный?",
    "Кого оставят на тонущем корабле?",
    "Кого сыграет Дуэйн Скала Джонсон?",
    "Кто сожрёт последний кусок пиццы без спроса?",
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

    if today in chat["history"]:
        winner_id = chat["history"][today]
        winner = members.get(winner_id, {})
        name = winner.get("name", "Неизвестный")
        await update.message.reply_text(
            f"Пидор дня уже выбран! 🏆\n\nСегодня это — {name}\n\nРезультат меняется завтра."
        )
        save_data(data)
        return

    winner_id = random.choice(list(members.keys()))
    winner = members[winner_id]
    name = winner["name"]

    chat["history"][today] = winner_id
    chat["stats"][winner_id] = chat["stats"].get(winner_id, 0) + 1

    save_data(data)

    reason = random.choice(FUNNY_REASONS).format(name=name)

    search_phrases = [
        "🔍 Начинаю поиск пидора дня...",
        "🔍 Сканирую участников чата...",
        "🔍 Запускаю секретный алгоритм отбора...",
        "🔍 Консультируюсь с высшими силами...",
    ]
    almost_phrases = [
        "👀 Уже почти... кандидат найден, проверяю данные...",
        "👀 Теплее, теплее... база данных обрабатывается...",
        "👀 Вот-вот... финальная верификация...",
        "👀 Почти готово... подписываю приказ...",
    ]

    msg = await update.message.reply_text(random.choice(search_phrases))
    await asyncio.sleep(random.uniform(2, 3))

    await msg.edit_text(random.choice(almost_phrases))
    await asyncio.sleep(random.uniform(2, 3))

    mention = f'<a href="tg://user?id={winner_id}">{name}</a>'
    await msg.edit_text(
        f"{reason}\n\n🏆 Пидор дня — {mention}!",
        parse_mode="HTML",
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


async def _finish_battle(context: ContextTypes.DEFAULT_TYPE, poll_id: str):
    """Завершает батл: останавливает опрос и объявляет победителя."""
    data = load_data()
    polls = data.get("polls", {})
    battle = polls.get(poll_id)
    if not battle or battle.get("finished"):
        return

    battle["finished"] = True
    save_data(data)

    chat_id = battle["chat_id"]
    message_id = battle["message_id"]
    fighters = battle["fighters"]
    members = data.get(chat_id, {}).get("members", {})

    try:
        poll_result = await context.bot.stop_poll(chat_id=chat_id, message_id=message_id)
    except Exception:
        return

    options = poll_result.options
    votes = [o.voter_count for o in options]

    if votes[0] > votes[1]:
        winner_id = fighters[0]
    elif votes[1] > votes[0]:
        winner_id = fighters[1]
    else:
        winner_id = random.choice(fighters)

    winner_name = members.get(winner_id, {}).get("name", "Неизвестный")
    mention = f'<a href="tg://user?id={winner_id}">{winner_name}</a>'

    if votes[0] == votes[1]:
        result_line = f"Ничья! Но жребий пал на {mention} 🎲"
    else:
        result_line = f"Победитель — {mention}! 🏆"

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚔️ Батл завершён!\n\n{result_line}",
        parse_mode="HTML",
    )


async def battle_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    """Job: закрыть батл по таймауту."""
    poll_id = context.job.data
    await _finish_battle(context, poll_id)


async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)

    # Регистрируем вызывающего
    user = update.effective_user
    is_new = await register_member(chat, str(user.id), get_display_name(user), user.username)
    if is_new:
        msg = random.choice(WELCOME_MESSAGES).format(name=get_display_name(user))
        await update.message.reply_text(msg)

    members = chat["members"]
    if len(members) < 2:
        await update.message.reply_text("Нужно хотя бы 2 участника для батла!")
        save_data(data)
        return

    fighter_ids = random.sample(list(members.keys()), 2)
    names = [members[fid]["name"] for fid in fighter_ids]
    question = random.choice(BATTLE_QUESTIONS)
    total_voters = len(members)

    poll_msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"⚔️ {question}",
        options=names,
        is_anonymous=False,
    )

    poll_id = poll_msg.poll.id

    if "polls" not in data:
        data["polls"] = {}

    data["polls"][poll_id] = {
        "chat_id": chat_id,
        "message_id": poll_msg.message_id,
        "fighters": fighter_ids,
        "total_voters": total_voters,
        "voted": [],
        "finished": False,
    }
    save_data(data)

    context.job_queue.run_once(
        battle_timeout_job,
        when=BATTLE_CLOSE_SECONDS,
        data=poll_id,
        name=f"battle_{poll_id}",
    )


async def poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Срабатывает когда кто-то голосует в опросе."""
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = str(answer.user.id)

    data = load_data()
    polls = data.get("polls", {})
    battle = polls.get(poll_id)

    if not battle or battle.get("finished"):
        return

    if user_id not in battle["voted"]:
        battle["voted"].append(user_id)

    save_data(data)

    # Все проголосовали?
    if len(battle["voted"]) >= battle["total_voters"]:
        # Отменяем таймаут-джоб
        jobs = context.job_queue.get_jobs_by_name(f"battle_{poll_id}")
        for job in jobs:
            job.schedule_removal()

        await _finish_battle(context, poll_id)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Установи переменную окружения TELEGRAM_BOT_TOKEN")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("pidor", pidor))
    app.add_handler(CommandHandler("pidorstat", pidorstat))
    app.add_handler(CommandHandler("battle", battle))
    app.add_handler(PollAnswerHandler(poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_member))

    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
