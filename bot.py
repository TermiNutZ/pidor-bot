import asyncio
import json
import os
import random
from datetime import date
from telegram import Bot, ReactionTypeEmoji, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    PollAnswerHandler, filters, ContextTypes,
)

DATA_FILE = "data.json"

BATTLE_CLOSE_SECONDS = 1 * 60 * 60  # 1 час
QUIPLASH_COLLECT_SECONDS = 60 * 60  # 1 час на сбор шуток
QUIPLASH_VOTE_SECONDS = 5 * 60      # 5 минут на голосование

# poll_id -> asyncio.Task (таймаут батла)
_battle_timers: dict[str, asyncio.Task] = {}

# chat_id -> состояние quiplash
_active_quiplash: dict[str, dict] = {}

# poll_id -> chat_id (для quiplash голосований)
_quiplash_poll_map: dict[str, str] = {}

# poll_id -> asyncio.Task (таймаут голосования quiplash)
_quiplash_vote_timers: dict[str, asyncio.Task] = {}

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
    "Кто больше скуф?"
]

SITUATIONS = [
    "Последние слова {name} перед расстрелом:",
    "Единственная строчка в завещании {name}:",
    "{name} на смертном одре признаётся:",
    "{name} на допросе в ФСБ. За что?",
    "{name} задержали на таможне. Что нашли в чемодане?",
    "Последнее слово {name} перед судом:",
    "{name} на детекторе лжи. Какой вопрос провалит?",
    "{name} случайно стал диктатором. Первый указ:",
    "{name} теперь владеет Газпромом. Первое решение:",
    "{name} основал религию. Главная заповедь:",
    "{name} нашёл чемодан денег. Что дальше?",
    "{name} встретил себя из будущего. Что тот сказал?",
    "{name} просыпается в Северной Корее. Первые действия?",
    "{name} случайно отправил скрин переписки не туда. Что там было?",
    "Название автобиографии {name}:",
    "За что {name} попадёт в учебники истории?",
    "Что напишут на памятнике {name}?",
    "Секретное хобби {name}, о котором никто не знает:",
    "Цитата {name}, которую будут вспоминать через 100 лет:",
    "{name} выступает перед ООН. Первая фраза:",
    "Тост {name} на свадьбе:",
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


# ───────────────────────── BATTLE ─────────────────────────

async def _finish_battle(bot: Bot, poll_id: str):
    """Завершает батл: останавливает опрос и объявляет победителя."""
    _battle_timers.pop(poll_id, None)

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
        poll_result = await bot.stop_poll(chat_id=chat_id, message_id=message_id)
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

    chat_data = data.get(chat_id, {})
    battle_stats = chat_data.setdefault("battle_stats", {})
    battle_stats[winner_id] = battle_stats.get(winner_id, 0) + 1
    save_data(data)

    if votes[0] == votes[1]:
        result_line = f"Ничья! Но жребий пал на {mention} 🎲"
    else:
        result_line = f"Победитель — {mention}! 🏆"

    await bot.send_message(
        chat_id=chat_id,
        text=f"⚔️ Батл завершён!\n\n{result_line}",
        parse_mode="HTML",
    )


async def _battle_timeout(bot: Bot, poll_id: str):
    await asyncio.sleep(BATTLE_CLOSE_SECONDS)
    await _finish_battle(bot, poll_id)


async def battlestat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)

    battle_stats = chat.get("battle_stats", {})
    members = chat["members"]

    if not battle_stats:
        await update.message.reply_text("Статистика батлов пуста. Запусти /battle!")
        return

    sorted_stats = sorted(battle_stats.items(), key=lambda x: x[1], reverse=True)

    lines = ["⚔️ Зал славы батлов:\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, count) in enumerate(sorted_stats):
        name = members.get(uid, {}).get("name", f"Пользователь {uid}")
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {name} — {count} побед(ы)")

    await update.message.reply_text("\n".join(lines))


async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)

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

    today = str(date.today())
    if chat.get("last_battle") == today:
        await update.message.reply_text("Батл сегодня уже был! Приходи завтра ⚔️")
        save_data(data)
        return

    chat["last_battle"] = today

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

    task = asyncio.create_task(_battle_timeout(context.bot, poll_id))
    _battle_timers[poll_id] = task


async def poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = str(answer.user.id)

    # Батл?
    data = load_data()
    battle = data.get("polls", {}).get(poll_id)
    if battle and not battle.get("finished"):
        if user_id not in battle["voted"]:
            battle["voted"].append(user_id)
        save_data(data)

        if len(battle["voted"]) >= battle["total_voters"]:
            timer = _battle_timers.pop(poll_id, None)
            if timer:
                timer.cancel()
            await _finish_battle(context.bot, poll_id)
        return

    # Quiplash?
    chat_id = _quiplash_poll_map.get(poll_id)
    if not chat_id:
        return

    state = _active_quiplash.get(chat_id)
    if not state or state.get("phase") != "voting" or state.get("vote_poll_id") != poll_id:
        return

    if user_id not in state["voted"]:
        state["voted"].append(user_id)

    if len(state["voted"]) >= state["total_voters"]:
        timer = _quiplash_vote_timers.pop(poll_id, None)
        if timer:
            timer.cancel()
        await _finish_quiplash_vote(context.bot, chat_id)


# ───────────────────────── QUIPLASH ─────────────────────────

async def _finish_quiplash_vote(bot: Bot, chat_id: str):
    state = _active_quiplash.pop(chat_id, None)
    if not state or state.get("phase") != "voting":
        return

    poll_id = state.get("vote_poll_id")
    _quiplash_poll_map.pop(poll_id, None)
    _quiplash_vote_timers.pop(poll_id, None)

    try:
        poll_result = await bot.stop_poll(chat_id=chat_id, message_id=state["vote_message_id"])
    except Exception:
        return

    options = poll_result.options
    votes = [o.voter_count for o in options]
    max_votes = max(votes)
    top_indices = [i for i, v in enumerate(votes) if v == max_votes]
    winner_idx = random.choice(top_indices)

    answer_list = state["answer_list"]  # [(user_id, {name, text}), ...]
    winner_id, winner_ans = answer_list[winner_idx]
    winner_name = winner_ans["name"]
    mention = f'<a href="tg://user?id={winner_id}">{winner_name}</a>'

    # Сохраняем статистику
    data = load_data()
    chat_data = get_chat_data(data, chat_id)
    ql_stats = chat_data.setdefault("quiplash_stats", {})
    ql_stats[winner_id] = ql_stats.get(winner_id, 0) + 1
    save_data(data)

    if len(top_indices) > 1:
        result_line = f"Ничья по голосам! Жребий выбрал {mention} 🎲"
    else:
        result_line = f"Победитель — {mention}! 🏆"

    await bot.send_message(
        chat_id=chat_id,
        text=f"🎭 Quiplash завершён!\n\n{result_line}",
        parse_mode="HTML",
    )


async def _quiplash_vote_timeout(bot: Bot, chat_id: str, poll_id: str):
    await asyncio.sleep(QUIPLASH_VOTE_SECONDS)
    await _finish_quiplash_vote(bot, chat_id)


async def _quiplash_collect_phase(bot: Bot, chat_id: str, prompt_message_id: int):
    """Таймер сбора шуток с напоминаниями."""
    await asyncio.sleep(30 * 60)
    if chat_id not in _active_quiplash:
        return
    await bot.send_message(chat_id, "⏰ До конца приёма шуток осталось 30 минут!")

    await asyncio.sleep(20 * 60)
    if chat_id not in _active_quiplash:
        return
    await bot.send_message(chat_id, "⏰ Осталось 10 минут! Последний шанс написать шутку!")

    await asyncio.sleep(9 * 60)
    if chat_id not in _active_quiplash:
        return
    await bot.send_message(chat_id, "⏰ Осталась 1 минута!")

    await asyncio.sleep(60)
    if chat_id not in _active_quiplash:
        return

    state = _active_quiplash[chat_id]
    if state.get("phase") != "collecting":
        return

    answers = state["answers"]

    if len(answers) < 2:
        await bot.send_message(
            chat_id,
            "😢 Мало шуток для голосования. Игра отменена." if not answers
            else "😢 Только одна шутка — победитель определён автоматически!"
        )
        if len(answers) == 1:
            uid, ans = next(iter(answers.items()))
            mention = f'<a href="tg://user?id={uid}">{ans["name"]}</a>'
            data = load_data()
            chat_data = get_chat_data(data, chat_id)
            ql_stats = chat_data.setdefault("quiplash_stats", {})
            ql_stats[uid] = ql_stats.get(uid, 0) + 1
            save_data(data)
            await bot.send_message(
                chat_id,
                f"🏆 Победитель по умолчанию — {mention}!",
                parse_mode="HTML",
            )
        _active_quiplash.pop(chat_id, None)
        return

    # Запускаем голосование
    state["phase"] = "voting"
    answer_list = list(answers.items())
    state["answer_list"] = answer_list

    options = [ans["name"][:100] for _, ans in answer_list]

    # Telegram позволяет максимум 10 вариантов в опросе
    if len(options) > 10:
        answer_list = answer_list[:10]
        options = options[:10]
        state["answer_list"] = answer_list

    data = load_data()
    total_voters = len(get_chat_data(data, chat_id)["members"])
    state["total_voters"] = total_voters
    state["voted"] = []

    poll_msg = await bot.send_poll(
        chat_id=chat_id,
        question="🎭 Чья шутка лучше?",
        options=options,
        is_anonymous=False,
    )

    state["vote_poll_id"] = poll_msg.poll.id
    state["vote_message_id"] = poll_msg.message_id
    _quiplash_poll_map[poll_msg.poll.id] = chat_id

    task = asyncio.create_task(_quiplash_vote_timeout(bot, chat_id, poll_msg.poll.id))
    _quiplash_vote_timers[poll_msg.poll.id] = task


async def quiplash_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловит ответы на промпт quiplash."""
    if not update.message or not update.message.reply_to_message:
        return
    if not update.effective_chat or update.effective_chat.type == "private":
        return

    chat_id = str(update.effective_chat.id)
    state = _active_quiplash.get(chat_id)
    if not state or state.get("phase") != "collecting":
        return

    if update.message.reply_to_message.message_id != state["prompt_message_id"]:
        return

    user = update.effective_user
    if user.is_bot:
        return

    user_id = str(user.id)
    text = update.message.text or update.message.caption
    if not text:
        return

    is_update = user_id in state["answers"]
    state["answers"][user_id] = {
        "name": get_display_name(user),
        "text": text,
    }

    reaction = "🔄" if is_update else "✍️"
    try:
        await update.message.set_reaction([ReactionTypeEmoji(reaction)])
    except Exception:
        pass


async def quiplash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)

    if chat_id in _active_quiplash:
        await update.message.reply_text("Quiplash уже идёт! Сначала дождитесь конца текущей игры.")
        return

    data = load_data()
    chat = get_chat_data(data, chat_id)

    user = update.effective_user
    is_new = await register_member(chat, str(user.id), get_display_name(user), user.username)
    if is_new:
        await update.message.reply_text(
            random.choice(WELCOME_MESSAGES).format(name=get_display_name(user))
        )

    members = chat["members"]
    if len(members) < 2:
        await update.message.reply_text("Нужно хотя бы 2 участника для игры!")
        save_data(data)
        return

    today = str(date.today())
    if chat.get("last_quiplash") == today:
        await update.message.reply_text("Quiplash сегодня уже был! Приходи завтра 🎭")
        save_data(data)
        return

    chat["last_quiplash"] = today
    save_data(data)

    subject_id = random.choice(list(members.keys()))
    subject_name = members[subject_id]["name"]
    situation = random.choice(SITUATIONS).format(name=subject_name)

    prompt_msg = await update.message.reply_text(
        f"🎭 <b>QUIPLASH!</b>\n\n"
        f"<b>{situation}</b>\n\n"
        f"У вас <b>1 час</b>, чтобы ответить на это сообщение своей шуткой!\n"
        f"Отвечайте реплаем на это сообщение 👇",
        parse_mode="HTML",
    )

    _active_quiplash[chat_id] = {
        "phase": "collecting",
        "prompt_message_id": prompt_msg.message_id,
        "subject_id": subject_id,
        "situation": situation,
        "answers": {},
    }

    asyncio.create_task(_quiplash_collect_phase(context.bot, chat_id, prompt_msg.message_id))


async def quiplashstat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)

    ql_stats = chat.get("quiplash_stats", {})
    members = chat["members"]

    if not ql_stats:
        await update.message.reply_text("Статистика Quiplash пуста. Запусти /quiplash!")
        return

    sorted_stats = sorted(ql_stats.items(), key=lambda x: x[1], reverse=True)

    lines = ["🎭 Зал славы Quiplash:\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, count) in enumerate(sorted_stats):
        name = members.get(uid, {}).get("name", f"Пользователь {uid}")
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {name} — {count} побед(ы)")

    await update.message.reply_text("\n".join(lines))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 <b>Команды бота:</b>\n\n"
        "🍑 /pidor — выбрать пидора дня (раз в день)\n"
        "📊 /pidorstat — статистика пидоров\n\n"
        "⚔️ /battle — батл двух случайных участников голосованием (раз в день)\n"
        "📊 /battlestat — статистика побед в батлах\n\n"
        "🎭 /quiplash — игра: придумай шутку про участника чата (раз в день)\n"
        "📊 /quiplashstat — статистика побед в Quiplash\n\n"
        "❓ /help — это сообщение"
    )
    await update.message.reply_text(text, parse_mode="HTML")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Установи переменную окружения TELEGRAM_BOT_TOKEN")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("pidor", pidor))
    app.add_handler(CommandHandler("pidorstat", pidorstat))
    app.add_handler(CommandHandler("battle", battle))
    app.add_handler(CommandHandler("battlestat", battlestat))
    app.add_handler(CommandHandler("quiplash", quiplash))
    app.add_handler(CommandHandler("quiplashstat", quiplashstat))
    app.add_handler(PollAnswerHandler(poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))
    # Quiplash ответы — группа 1, чтобы работало параллельно с track_member
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, quiplash_answer), group=1)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_member))

    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
