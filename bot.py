import asyncio
import json
import math
import os
import random
from datetime import date
from telegram import Bot, ReactionTypeEmoji, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    PollAnswerHandler, filters, ContextTypes,
)

DATA_FILE = "data.json"
CONFIG_FILE = "config.json"

with open(CONFIG_FILE, "r", encoding="utf-8") as _f:
    _cfg = json.load(_f)

FUNNY_REASONS       = _cfg["funny_reasons"]
PIDOR_SEARCH        = _cfg["pidor_search_phrases"]
PIDOR_ALMOST        = _cfg["pidor_almost_phrases"]
WELCOME_MESSAGES    = _cfg["welcome_messages"]
BATTLE_QUESTIONS    = _cfg["battle_questions"]
SITUATIONS          = _cfg["quiplash_situations"]
SCENARIOS           = _cfg["casting_scenarios"]

BATTLE_CLOSE_SECONDS = 1 * 60 * 60  # 1 час
QUIPLASH_COLLECT_SECONDS = 60 * 60  # 1 час на сбор шуток
QUIPLASH_VOTE_SECONDS = 60 * 60     # 1 час на голосование
CASTING_ROLE_SECONDS = 10 * 60      # 10 минут на роль
MIN_VOTES = 3                        # минимум голосов для закрытия опроса

# poll_id -> {"chat_id", "match_index", "event"} (активные опросы турнира)
_tournament_polls: dict[str, dict] = {}

# chat_id -> asyncio.Task (таймер раунда турнира)
_tournament_timers: dict[str, asyncio.Task] = {}

# chat_id -> состояние quiplash
_active_quiplash: dict[str, dict] = {}

# poll_id -> chat_id (для quiplash голосований)
_quiplash_poll_map: dict[str, str] = {}

# poll_id -> asyncio.Task (таймаут голосования quiplash)
_quiplash_vote_timers: dict[str, asyncio.Task] = {}

# chat_id -> состояние кастинга
_active_casting: dict[str, dict] = {}

# poll_id -> chat_id (для кастинг-опросов)
_casting_poll_map: dict[str, str] = {}

# Блокировка для атомарных read-modify-write циклов data.json
_data_lock = asyncio.Lock()



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

    msg = await update.message.reply_text(random.choice(PIDOR_SEARCH))
    await asyncio.sleep(random.uniform(2, 3))

    await msg.edit_text(random.choice(PIDOR_ALMOST))
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


# ───────────────────────── BATTLE (TOURNAMENT) ─────────────────────────

def _get_round_name(round_idx: int, total_rounds: int) -> str:
    """Возвращает название раунда по индексу и общему числу раундов."""
    remaining = total_rounds - round_idx
    if remaining == 1:
        return "Финал"
    if remaining == 2:
        return "Полуфинал"
    if remaining == 3:
        return "Четвертьфинал"
    return f"Раунд {round_idx + 1}"


def _make_matches(player_ids: list[str]) -> list[list[str]]:
    """Формирует матчи из списка участников. Если нечётное — последняя группа = тройка."""
    ids = list(player_ids)
    matches = []
    if len(ids) % 2 == 1 and len(ids) >= 3:
        # Последние 3 — тройка
        triple = ids[-3:]
        rest = ids[:-3]
        for i in range(0, len(rest), 2):
            matches.append(rest[i:i+2])
        matches.append(triple)
    else:
        for i in range(0, len(ids), 2):
            matches.append(ids[i:i+2])
    return matches


def _create_tournament(member_ids: list[str], question: str) -> dict:
    """Создаёт новый турнир: перемешивает участников, формирует первый раунд."""
    ids = list(member_ids)
    random.shuffle(ids)
    first_round = _make_matches(ids)
    n = len(ids)
    total_rounds = max(1, math.ceil(math.log2(n)))
    return {
        "question": question,
        "bracket": [first_round],
        "results": [],
        "current_round": 0,
        "last_round_date": None,
        "total_rounds": total_rounds,
        "finished": False,
        "champion": None,
    }


async def _finish_tournament_match(bot: Bot, chat_id: str, poll_id: str):
    """Останавливает один опрос матча и определяет победителя."""
    info = _tournament_polls.pop(poll_id, None)
    if not info:
        return

    async with _data_lock:
        data = load_data()
        poll_data = data.get("tournament_polls", {}).get(poll_id)
        if not poll_data or poll_data.get("finished"):
            return

        poll_data["finished"] = True
        save_data(data)

    try:
        poll_result = await bot.stop_poll(
            chat_id=chat_id, message_id=poll_data["message_id"]
        )
    except Exception:
        return

    options = poll_result.options
    votes = [o.voter_count for o in options]
    max_v = max(votes)
    top = [i for i, v in enumerate(votes) if v == max_v]
    winner_idx = random.choice(top)
    winner_id = poll_data["fighters"][winner_idx]

    async with _data_lock:
        data = load_data()
        pd = data.get("tournament_polls", {}).get(poll_id)
        if pd:
            pd["winner"] = winner_id
            save_data(data)

    # Сигнализируем что матч завершён
    event = info.get("event")
    if event:
        event.set()


async def _run_tournament_round(bot: Bot, chat_id: str):
    """Проводит один раунд турнира: отправляет все опросы, ждёт завершения, объявляет результаты."""
    data = load_data()
    chat = get_chat_data(data, chat_id)
    tournament = chat.get("tournament")
    if not tournament or tournament.get("finished"):
        return

    round_idx = tournament["current_round"]
    matches = tournament["bracket"][round_idx]
    question = tournament["question"]
    total_rounds = tournament["total_rounds"]
    round_name = _get_round_name(round_idx, total_rounds)
    members = chat["members"]
    total_voters = len(members)

    await bot.send_message(
        chat_id=chat_id,
        text=f"⚔️ <b>ТУРНИР — {round_name.upper()}</b>\n\n"
             f"Вопрос: <b>{question}</b>\n\n"
             f"Матчей в этом раунде: {len(matches)}",
        parse_mode="HTML",
    )
    await asyncio.sleep(2)

    if "tournament_polls" not in data:
        data["tournament_polls"] = {}

    events = []
    for match_idx, match in enumerate(matches):
        names = [members.get(uid, {}).get("name", f"Участник {uid}") for uid in match]
        poll_msg = await bot.send_poll(
            chat_id=chat_id,
            question=f"⚔️ {question}",
            options=names,
            is_anonymous=False,
        )
        poll_id = poll_msg.poll.id
        event = asyncio.Event()

        data["tournament_polls"][poll_id] = {
            "chat_id": chat_id,
            "message_id": poll_msg.message_id,
            "fighters": match,
            "total_voters": total_voters,
            "voted": [],
            "finished": False,
            "winner": None,
            "round_idx": round_idx,
            "match_idx": match_idx,
        }

        _tournament_polls[poll_id] = {
            "chat_id": chat_id,
            "match_index": match_idx,
            "event": event,
        }
        events.append((poll_id, event))
        await asyncio.sleep(1)

    save_data(data)

    # Ждём завершения всех матчей (таймер + голоса)
    async def _wait_and_close(poll_id: str, evt: asyncio.Event):
        try:
            await asyncio.wait_for(evt.wait(), timeout=BATTLE_CLOSE_SECONDS)
        except asyncio.TimeoutError:
            # Таймер истёк — ждём минимум голосов
            while True:
                d = load_data()
                pd = d.get("tournament_polls", {}).get(poll_id, {})
                if pd.get("finished"):
                    return
                voted = len(pd.get("voted", []))
                tv = pd.get("total_voters", 0)
                if voted >= min(MIN_VOTES, tv):
                    break
                await asyncio.sleep(15)
            await _finish_tournament_match(bot, chat_id, poll_id)

    tasks = [asyncio.create_task(_wait_and_close(pid, evt)) for pid, evt in events]
    await asyncio.gather(*tasks)

    # Все матчи завершены — собираем победителей
    data = load_data()
    chat = get_chat_data(data, chat_id)
    tournament = chat["tournament"]

    round_results = []
    winners = []
    for match_idx, match in enumerate(matches):
        # Ищем poll для этого матча
        winner_id = None
        for pid, pd in data.get("tournament_polls", {}).items():
            if (pd.get("chat_id") == chat_id
                    and pd.get("round_idx") == round_idx
                    and pd.get("match_idx") == match_idx):
                winner_id = pd.get("winner")
                break
        if not winner_id:
            winner_id = random.choice(match)
        round_results.append({"match": match_idx, "winner": winner_id})
        winners.append(winner_id)

    tournament["results"].append(round_results)

    # Объявляем результаты раунда
    lines = [f"📊 <b>Результаты — {round_name}</b>\n"]
    for i, res in enumerate(round_results):
        match = matches[i]
        w_id = res["winner"]
        w_name = members.get(w_id, {}).get("name", "Неизвестный")
        vs = " vs ".join(members.get(uid, {}).get("name", "?") for uid in match)
        mention = f'<a href="tg://user?id={w_id}">{w_name}</a>'
        lines.append(f"⚔️ {vs} → {mention}")

    is_final = (len(winners) == 1)

    if is_final:
        # Турнир завершён
        champion_id = winners[0]
        champion_name = members.get(champion_id, {}).get("name", "Неизвестный")
        champion_mention = f'<a href="tg://user?id={champion_id}">{champion_name}</a>'
        lines.append(f"\n🏆 <b>ЧЕМПИОН ТУРНИРА — {champion_mention}!</b>")

        tournament["finished"] = True
        tournament["champion"] = champion_id

        battle_stats = chat.setdefault("battle_stats", {})
        battle_stats[champion_id] = battle_stats.get(champion_id, 0) + 1
    else:
        # Готовим следующий раунд
        next_matches = _make_matches(winners)
        tournament["bracket"].append(next_matches)
        tournament["current_round"] = round_idx + 1
        next_round_name = _get_round_name(round_idx + 1, total_rounds)
        lines.append(f"\nСледующий раунд: <b>{next_round_name}</b> — завтра!")

    save_data(data)

    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="HTML",
    )

    _tournament_timers.pop(chat_id, None)


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
        await update.message.reply_text("Статистика турниров пуста. Запусти /battle!")
        return

    sorted_stats = sorted(battle_stats.items(), key=lambda x: x[1], reverse=True)

    lines = ["⚔️ Зал славы турниров:\n"]
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
        await update.message.reply_text("Нужно хотя бы 2 участника для турнира!")
        save_data(data)
        return

    today = str(date.today())
    tournament = chat.get("tournament")

    # Активный турнир
    if tournament and not tournament.get("finished"):
        if tournament.get("last_round_date") == today:
            round_name = _get_round_name(
                tournament["current_round"], tournament["total_rounds"]
            )
            await update.message.reply_text(
                f"Сегодняшний раунд ({round_name}) уже сыгран! "
                f"Следующий — завтра ⚔️"
            )
            save_data(data)
            return
        # Проводим следующий раунд
        tournament["last_round_date"] = today
        save_data(data)
        asyncio.create_task(_run_tournament_round(context.bot, chat_id))
        return

    # Нет турнира или завершён — создаём новый
    used_questions = set(chat.get("used_battle_questions", []))
    available = [q for q in BATTLE_QUESTIONS if q not in used_questions]
    if not available:
        chat["used_battle_questions"] = []
        available = list(BATTLE_QUESTIONS)
    question = random.choice(available)
    chat.setdefault("used_battle_questions", []).append(question)
    member_ids = list(members.keys())
    tournament = _create_tournament(member_ids, question)
    tournament["last_round_date"] = today
    chat["tournament"] = tournament
    save_data(data)

    asyncio.create_task(_run_tournament_round(context.bot, chat_id))


async def poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = str(answer.user.id)

    # Турнирный батл?
    should_finish = False
    async with _data_lock:
        data = load_data()
        t_poll = data.get("tournament_polls", {}).get(poll_id)
        if t_poll and not t_poll.get("finished"):
            if user_id not in t_poll["voted"]:
                t_poll["voted"].append(user_id)
            save_data(data)
            should_finish = len(t_poll["voted"]) >= t_poll["total_voters"]
    if t_poll and not t_poll.get("finished"):
        if should_finish:
            await _finish_tournament_match(context.bot, t_poll["chat_id"], poll_id)
        return

    # Casting?
    chat_id = _casting_poll_map.get(poll_id)
    if chat_id:
        state = _active_casting.get(chat_id)
        if state and state.get("current_poll_id") == poll_id:
            if user_id not in state["current_poll_voted"]:
                state["current_poll_voted"].append(user_id)
            if len(state["current_poll_voted"]) >= state["total_voters"]:
                event = state.get("current_poll_event")
                if event:
                    event.set()
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
    while True:
        state = _active_quiplash.get(chat_id)
        if not state:
            return
        voted = len(state.get("voted", []))
        total = state.get("total_voters", 0)
        if voted >= min(MIN_VOTES, total):
            break
        await asyncio.sleep(15)
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


# ───────────────────────── CASTING ─────────────────────────
async def _run_casting_poll(bot: Bot, chat_id: str, state: dict, role: dict) -> dict | None:
    """Проводит один опрос для роли. Возвращает {role, user_id, name, votes} или None."""
    available_ids = [uid for uid in state["all_member_ids"] if uid not in state["assigned_user_ids"]]

    await bot.send_message(
        chat_id=chat_id,
        text=f"👤 <b>{role['name']}</b>\n📝 {role['description']}\n\nКто получит эту роль?",
        parse_mode="HTML",
    )
    await asyncio.sleep(1)

    poll_ids = available_ids[:10]
    poll_options = [state["member_names"][uid] for uid in poll_ids]

    poll_msg = await bot.send_poll(
        chat_id=chat_id,
        question=f"👤 {role['name']}",
        options=poll_options,
        is_anonymous=False,
    )

    poll_id = poll_msg.poll.id
    event = asyncio.Event()
    state["current_poll_id"] = poll_id
    state["current_poll_member_ids"] = poll_ids
    state["current_poll_message_id"] = poll_msg.message_id
    state["current_poll_voted"] = []
    state["current_poll_event"] = event
    _casting_poll_map[poll_id] = chat_id

    # Ждём дедлайн или пока все не проголосуют
    try:
        await asyncio.wait_for(event.wait(), timeout=CASTING_ROLE_SECONDS)
    except asyncio.TimeoutError:
        # Дедлайн прошёл — ждём минимум 3 голоса
        while len(state["current_poll_voted"]) < min(MIN_VOTES, state["total_voters"]):
            await asyncio.sleep(15)

    _casting_poll_map.pop(poll_id, None)

    try:
        poll_result = await bot.stop_poll(chat_id=chat_id, message_id=poll_msg.message_id)
    except Exception:
        return None

    options = poll_result.options
    max_votes = max(o.voter_count for o in options)
    top_indices = [i for i, o in enumerate(options) if o.voter_count == max_votes]
    winner_idx = random.choice(top_indices)
    winner_id = poll_ids[winner_idx]
    winner_name = state["member_names"][winner_id]
    winner_votes = options[winner_idx].voter_count

    state["assigned_user_ids"].add(winner_id)

    mention = f'<a href="tg://user?id={winner_id}">{winner_name}</a>'
    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ <b>{role['name']}</b> — {mention} ({winner_votes} голос(ов))",
        parse_mode="HTML",
    )
    return {"role": role, "user_id": winner_id, "name": winner_name, "votes": winner_votes}


async def _run_casting(bot: Bot, chat_id: str):
    state = _active_casting.get(chat_id)
    if not state:
        return

    scenario = state["scenario"]
    main_role = state["main_role"]
    results = []

    for role in state["roles"]:
        result = await _run_casting_poll(bot, chat_id, state, role)
        if result:
            results.append(result)
        await asyncio.sleep(5)

    # Последний оставшийся получает главную роль
    remaining = [uid for uid in state["all_member_ids"] if uid not in state["assigned_user_ids"]]
    if remaining:
        winner_id = remaining[0]
        winner_name = state["member_names"][winner_id]
        results.append({"role": main_role, "user_id": winner_id, "name": winner_name, "votes": 0})

        mention = f'<a href="tg://user?id={winner_id}">{winner_name}</a>'
        await bot.send_message(
            chat_id=chat_id,
            text=f"🏆 Последний оставшийся — {mention}!\n\n"
                 f"{main_role['emoji']} <b>{main_role['name']}</b> — {mention}!\n\n"
                 f"Поздравляем победителя!",
            parse_mode="HTML",
        )
        await asyncio.sleep(3)

    # Сохраняем результаты
    data = load_data()
    chat_data = get_chat_data(data, chat_id)
    casting_results = chat_data.setdefault("casting_results", [])
    today = str(date.today())
    for r in results:
        casting_results.append({
            "scenario_id": scenario["id"],
            "user_id": r["user_id"],
            "role_id": r["role"]["id"],
            "role_name": r["role"]["name"],
            "role_type": r["role"]["type"],
            "main": bool(r["role"].get("main")),
            "votes": r["votes"],
            "date": today,
        })
    save_data(data)

    # Итоговое сообщение — главная роль первой
    main_result = next((r for r in results if r["role"].get("main")), None)
    other_results = [r for r in results if not r["role"].get("main")]

    lines = [f"🎬 <b>КАСТИНГ ЗАВЕРШЁН: {scenario['name'].upper()}</b>\n"]
    if main_result:
        mention = f'<a href="tg://user?id={main_result["user_id"]}">{main_result["name"]}</a>'
        lines.append(f"{main_result['role']['emoji']} <b>{main_result['role']['name']}</b> — {mention} 🏆")
    for r in other_results:
        mention = f'<a href="tg://user?id={r["user_id"]}">{r["name"]}</a>'
        lines.append(f"{r['role']['emoji']} {r['role']['name']} — {mention}")
    lines.append("\nСпасибо за игру!")

    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
    _active_casting.pop(chat_id, None)


async def casting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)

    if chat_id in _active_casting:
        await update.message.reply_text("Кастинг уже идёт! Дождитесь окончания.")
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
        await update.message.reply_text("Нужно хотя бы 2 участника для кастинга!")
        save_data(data)
        return

    today = str(date.today())
    if chat.get("last_casting") == today:
        await update.message.reply_text("Кастинг сегодня уже был! Приходи завтра 🎬")
        save_data(data)
        return

    # Выбираем сценарий, который ещё не разыгрывался в этом чате
    used = set(chat.get("used_scenarios", []))
    available_scenarios = [s for s in SCENARIOS if s["id"] not in used]
    if not available_scenarios:
        # Все сыграны — сбрасываем историю
        available_scenarios = SCENARIOS
        chat["used_scenarios"] = []

    scenario = random.choice(available_scenarios)
    chat.setdefault("used_scenarios", []).append(scenario["id"])
    chat["last_casting"] = today
    save_data(data)

    all_member_ids = list(members.keys())

    # Главная роль достаётся последнему — голосуем только за остальные
    main_role = next((r for r in scenario["roles"] if r.get("main")), scenario["roles"][0])
    other_roles = [r for r in scenario["roles"] if not r.get("main")]
    # Берём столько ролей, чтобы осталось ровно 1 человек для главной
    voting_roles = other_roles[:len(all_member_ids) - 1]

    _active_casting[chat_id] = {
        "scenario": scenario,
        "main_role": main_role,
        "roles": voting_roles,
        "all_member_ids": all_member_ids,
        "member_names": {uid: info["name"] for uid, info in members.items()},
        "assigned_user_ids": set(),
        "total_voters": len(members),
        "current_poll_id": None,
        "current_poll_member_ids": [],
        "current_poll_message_id": None,
        "current_poll_voted": [],
        "current_poll_event": None,
    }

    await update.message.reply_text(
        f"🎬 <b>КАСТИНГ: {scenario['name'].upper()}</b>\n\n"
        f"{scenario['description']}\n\n"
        f"На каждую роль — 10 минут голосования. Последний оставшийся получит главную роль!\n\n"
        f"Начинаем!",
        parse_mode="HTML",
    )
    await asyncio.sleep(2)

    asyncio.create_task(_run_casting(context.bot, chat_id))


async def casting_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)
    members = chat["members"]

    results = chat.get("casting_results", [])
    if not results:
        await update.message.reply_text("Статистика кастингов пуста. Запусти /casting!")
        return

    wins: dict[str, int] = {}
    for r in results:
        if r.get("main"):
            uid = r["user_id"]
            wins[uid] = wins.get(uid, 0) + 1

    if not wins:
        await update.message.reply_text("Ещё никто не получал главную роль. Сыграйте кастинг!")
        return

    lines = ["👑 <b>Чаще всего получали главную роль:</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, count) in enumerate(sorted(wins.items(), key=lambda x: x[1], reverse=True)):
        name = members.get(uid, {}).get("name", f"Пользователь {uid}")
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {name} — {count} раз(а)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 <b>Команды бота:</b>\n\n"
        "🍑 /pidor — выбрать пидора дня (раз в день)\n"
        "📊 /pidorstat — статистика пидоров\n\n"
        "⚔️ /battle — многодневный турнир с турнирной сеткой (раз в день — один раунд)\n"
        "📊 /battlestat — статистика чемпионов турниров\n\n"
        "🎭 /quiplash — игра: придумай шутку про участника чата (раз в день)\n"
        "📊 /quiplashstat — статистика побед в Quiplash\n\n"
        "🎬 /casting — кастинг: распределить участников по ролям сценария (раз в день)\n"
        "📊 /casting_stats — кто чаще всего получал главную роль\n\n"
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
    app.add_handler(CommandHandler("casting", casting))
    app.add_handler(CommandHandler("casting_stats", casting_stats))
    app.add_handler(PollAnswerHandler(poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))
    # Quiplash ответы — группа 1, чтобы работало параллельно с track_member
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, quiplash_answer), group=1)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_member))

    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
