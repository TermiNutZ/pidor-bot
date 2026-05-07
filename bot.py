import asyncio
import json
import math
import os
import random
from datetime import date, datetime, timezone, timedelta
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
TIERLIST_TOPICS     = _cfg["tierlist_topics"]

WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "words.txt")
with open(WORDS_FILE, "r", encoding="utf-8") as _wf:
    _wordle_words = set(line.strip().lower().replace("ё", "е") for line in _wf if line.strip())

WORDLE_MAX_ATTEMPTS = 6
WORDLE_TURN_COOLDOWN = 15 * 60  # 15 минут
WORDLE_TIMEOUT = 2 * 60 * 60   # 2 часа неактивности

BATTLE_CLOSE_SECONDS = 1 * 60 * 60  # 1 час
QUIPLASH_COLLECT_SECONDS = 60 * 60  # 1 час на сбор шуток
QUIPLASH_VOTE_SECONDS = 60 * 60     # 1 час на голосование
CASTING_ROLE_SECONDS = 10 * 60      # 10 минут на роль
TIERLIST_VOTE_SECONDS = 2 * 60 * 60 # 2 часа на голосование по объекту
TIERLIST_QUIET_START = 0   # тихие часы: начало (0:00 МСК)
TIERLIST_QUIET_END = 10    # тихие часы: конец (10:00 МСК)
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

# chat_id -> состояние wordle
_active_wordle: dict[str, dict] = {}

# chat_id -> asyncio.Task (таймаут wordle)
_wordle_timers: dict[str, asyncio.Task] = {}

# chat_id -> состояние tierlist
_active_tierlist: dict[str, dict] = {}

# poll_id -> chat_id (для tierlist-опросов)
_tierlist_poll_map: dict[str, str] = {}

# Блокировка для атомарных read-modify-write циклов data.json
_data_lock = asyncio.Lock()

TIERLIST_OPTIONS = ["Лучший из лучших", "Харош", "Под пивко сойдет", "Срань"]
TIERLIST_SCORES = {0: 3, 1: 2, 2: 1, 3: 0}  # option_index -> score
TIERLIST_TIER_EMOJIS = {
    "Лучший из лучших": "🏆",
    "Харош": "👍",
    "Под пивко сойдет": "🍺",
    "Срань": "💩",
}



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

def _get_round_name(num_players: int) -> str:
    """Возвращает название раунда по количеству участников в нём."""
    if num_players <= 2:
        return "Финал"
    if num_players <= 4:
        return "Полуфинал"
    if num_players <= 8:
        return "Четвертьфинал"
    return f"Раунд ({num_players} участников)"


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
            # Сигнализируем даже если уже завершён
            event = info.get("event")
            if event:
                event.set()
            return

        poll_data["finished"] = True
        save_data(data)

    match_idx = poll_data.get("match_idx")
    round_idx = poll_data.get("round_idx")

    try:
        poll_result = await bot.stop_poll(
            chat_id=chat_id, message_id=poll_data["message_id"]
        )
    except Exception:
        # Сигнализируем чтобы не зависнуть
        event = info.get("event")
        if event:
            event.set()
        return

    options = poll_result.options
    votes = [o.voter_count for o in options]
    max_v = max(votes)
    top = [i for i, v in enumerate(votes) if v == max_v]
    winner_idx = random.choice(top)
    winner_id = poll_data["fighters"][winner_idx]

    # Сохраняем winner прямо в tournament state — надёжнее чем в tournament_polls
    async with _data_lock:
        data = load_data()
        chat = get_chat_data(data, chat_id)
        tournament = chat.get("tournament")
        if tournament:
            rw = tournament.setdefault("round_winners", {})
            rw[f"{round_idx}_{match_idx}"] = winner_id
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
    num_players = sum(len(m) for m in matches)
    round_name = _get_round_name(num_players)
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
    rw = tournament.get("round_winners", {})
    for match_idx, match in enumerate(matches):
        key = f"{round_idx}_{match_idx}"
        winner_id = rw.get(key)
        if not winner_id or winner_id not in match:
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
        next_round_name = _get_round_name(len(winners))
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
            current_matches = tournament["bracket"][tournament["current_round"]]
            round_name = _get_round_name(sum(len(m) for m in current_matches))
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

    # Tierlist?
    chat_id = _tierlist_poll_map.get(poll_id)
    if chat_id:
        state = _active_tierlist.get(chat_id)
        if state and state.get("poll_id") == poll_id:
            if user_id not in state["voted"]:
                state["voted"].append(user_id)
            if len(state["voted"]) >= state["total_voters"]:
                event = state.get("event")
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

    # Сохраняем результаты атомарно
    async with _data_lock:
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

    # Атомарно читаем данные, регистрируем участника, выбираем сценарий
    async with _data_lock:
        data = load_data()
        chat = get_chat_data(data, chat_id)

        user = update.effective_user
        is_new = await register_member(chat, str(user.id), get_display_name(user), user.username)

        members = chat["members"]
        if len(members) < 2:
            save_data(data)
            await update.message.reply_text("Нужно хотя бы 2 участника для кастинга!")
            return

        today = str(date.today())
        if chat.get("last_casting") == today:
            save_data(data)
            await update.message.reply_text("Кастинг сегодня уже был! Приходи завтра 🎬")
            return

        # Выбираем сценарий, который ещё не разыгрывался в этом чате
        used = set(chat.get("used_scenarios", []))
        available_scenarios = [s for s in SCENARIOS if s["id"] not in used]
        if not available_scenarios:
            save_data(data)
            await update.message.reply_text(
                "🎬 Все сценарии кастинга уже сыграны!\n\n"
                "Предложите новые идеи админу, чтобы добавить свежие сценарии 💡"
            )
            return

        scenario = random.choice(available_scenarios)
        chat.setdefault("used_scenarios", []).append(scenario["id"])
        chat["last_casting"] = today
        save_data(data)

        all_member_ids = list(members.keys())

    if is_new:
        await update.message.reply_text(
            random.choice(WELCOME_MESSAGES).format(name=get_display_name(user))
        )

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


# ───────────────────────── TIERLIST ───────────────────────

def _score_to_tier(avg: float) -> str:
    if avg >= 2.5:
        return "Лучший из лучших"
    if avg >= 1.5:
        return "Харош"
    if avg >= 0.5:
        return "Под пивко сойдет"
    return "Срань"


_MSK = timezone(timedelta(hours=3))


async def _wait_for_quiet_hours():
    """Ждёт окончания тихих часов (00:00–10:00 МСК) перед отправкой опроса."""
    now = datetime.now(_MSK)
    if TIERLIST_QUIET_START <= now.hour < TIERLIST_QUIET_END:
        # Считаем сколько ждать до TIERLIST_QUIET_END
        wake = now.replace(hour=TIERLIST_QUIET_END, minute=0, second=0, microsecond=0)
        wait_seconds = (wake - now).total_seconds()
        await asyncio.sleep(wait_seconds)


async def _run_tierlist_poll(bot: Bot, chat_id: str, state: dict, item_name: str, item_index: int, total_items: int):
    """Проводит одно голосование за объект. Возвращает dict с результатом."""
    topic = state["topic"]

    await bot.send_message(
        chat_id=chat_id,
        text=f"{topic['emoji']} Объект {item_index + 1}/{total_items}\n"
             f"<b>{item_name}</b>\n\n"
             f"Куда кидаем?",
        parse_mode="HTML",
    )
    await asyncio.sleep(1)

    poll_msg = await bot.send_poll(
        chat_id=chat_id,
        question=f"{topic['emoji']} {item_name}",
        options=TIERLIST_OPTIONS,
        is_anonymous=False,
    )

    poll_id = poll_msg.poll.id
    event = asyncio.Event()

    state["poll_id"] = poll_id
    state["poll_message_id"] = poll_msg.message_id
    state["poll_started_at"] = datetime.now().isoformat()
    state["voted"] = []
    state["event"] = event
    _tierlist_poll_map[poll_id] = chat_id

    # Сохраняем poll state в data.json для восстановления после рестарта
    async with _data_lock:
        data = load_data()
        chat_data = get_chat_data(data, chat_id)
        run = chat_data.get("tierlist_run", {})
        run["poll_id"] = poll_id
        run["poll_message_id"] = poll_msg.message_id
        run["poll_started_at"] = state["poll_started_at"]
        run["current_item_index"] = item_index
        chat_data["tierlist_run"] = run
        save_data(data)

    # Ждём таймаут или все проголосовали
    try:
        await asyncio.wait_for(event.wait(), timeout=TIERLIST_VOTE_SECONDS)
    except asyncio.TimeoutError:
        # Таймер истёк — ждём минимум голосов
        while len(state.get("voted", [])) < min(MIN_VOTES, state["total_voters"]):
            await asyncio.sleep(15)

    _tierlist_poll_map.pop(poll_id, None)

    # Закрываем poll и собираем результаты
    try:
        poll_result = await bot.stop_poll(chat_id=chat_id, message_id=poll_msg.message_id)
    except Exception:
        return {"item": item_name, "avg": 0, "votes": 0, "tier": "Срань", "low_votes": True}

    options = poll_result.options
    total_votes = sum(o.voter_count for o in options)

    if total_votes == 0:
        return {"item": item_name, "avg": 0, "votes": 0, "tier": "Срань", "low_votes": True}

    # Считаем средний балл
    weighted_sum = sum(TIERLIST_SCORES[i] * o.voter_count for i, o in enumerate(options))
    avg = round(weighted_sum / total_votes, 2)
    tier = _score_to_tier(avg)
    low_votes = total_votes < MIN_VOTES

    return {"item": item_name, "avg": avg, "votes": total_votes, "tier": tier, "low_votes": low_votes}


async def _finalize_tierlist(bot: Bot, chat_id: str, topic: dict, results: list):
    """Публикует финальный тирлист и сохраняет историю."""
    tiers = {}
    for tier_name in TIERLIST_OPTIONS:
        tiers[tier_name] = []
    for r in results:
        tiers[r["tier"]].append(r)

    lines = [f"📊 <b>НАРОДНЫЙ ВЕРДИКТ: {topic['name'].upper()}</b>\n"]

    for tier_name in TIERLIST_OPTIONS:
        emoji = TIERLIST_TIER_EMOJIS[tier_name]
        lines.append(f"\n{emoji} <b>{tier_name}</b>")
        items = tiers[tier_name]
        if items:
            items.sort(key=lambda x: x["avg"], reverse=True)
            for r in items:
                suffix = " ⚠️" if r.get("low_votes") else ""
                lines.append(f"· {r['item']} ({r['avg']}){suffix}")
        else:
            lines.append("— пусто —")

    low_count = sum(1 for r in results if r.get("low_votes"))
    if low_count:
        lines.append(f"\n⚠️ = мало голосов (менее {MIN_VOTES})")

    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")

    # Сохраняем в историю
    async with _data_lock:
        data = load_data()
        chat_data = get_chat_data(data, chat_id)
        history = chat_data.setdefault("tierlist_history", [])
        history.append({
            "topic_id": topic["id"],
            "date": str(date.today()),
            "results": results,
        })
        run = chat_data.get("tierlist_run", {})
        run["status"] = "completed"
        chat_data["tierlist_run"] = run
        save_data(data)

    _active_tierlist.pop(chat_id, None)


async def _run_tierlist(bot: Bot, chat_id: str, start_index: int = 0):
    """Основной цикл тирлиста — последовательно голосуем за каждый объект."""
    state = _active_tierlist.get(chat_id)
    if not state:
        return

    topic = state["topic"]
    items = topic["items"]
    total = len(items)
    results = state.get("results", [])

    for i in range(start_index, total):
        state = _active_tierlist.get(chat_id)
        if not state:
            return

        await _wait_for_quiet_hours()

        result = await _run_tierlist_poll(bot, chat_id, state, items[i], i, total)
        results.append(result)
        state["results"] = results

        # Промежуточный итог
        low_mark = " (мало голосов)" if result.get("low_votes") else ""
        emoji = "⚠️" if result.get("low_votes") else "✅"
        await bot.send_message(
            chat_id=chat_id,
            text=f"{emoji} <b>{result['item']}</b> → <b>{result['tier']}</b>{low_mark}\n"
                 f"Голосов: {result['votes']} · Средняя: {result['avg']}",
            parse_mode="HTML",
        )

        # Сохраняем прогресс
        async with _data_lock:
            data = load_data()
            chat_data = get_chat_data(data, chat_id)
            run = chat_data.get("tierlist_run", {})
            run["results"] = results
            run["current_item_index"] = i + 1
            chat_data["tierlist_run"] = run
            save_data(data)

        if i < total - 1:
            await asyncio.sleep(5)

    await _finalize_tierlist(bot, chat_id, topic, results)


async def tierlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)

    if chat_id in _active_tierlist:
        state = _active_tierlist[chat_id]
        topic = state["topic"]
        idx = len(state.get("results", []))
        total = len(topic["items"])
        item = topic["items"][idx] if idx < total else "—"
        await update.message.reply_text(
            f"Тирлист уже идёт!\n\n"
            f"Тема: <b>{topic['name']}</b>\n"
            f"Сейчас голосуем: <b>{item}</b> ({idx + 1}/{total})",
            parse_mode="HTML",
        )
        return

    async with _data_lock:
        data = load_data()
        chat = get_chat_data(data, chat_id)

        user = update.effective_user
        is_new = await register_member(chat, str(user.id), get_display_name(user), user.username)

        members = chat["members"]
        if len(members) < 2:
            save_data(data)
            await update.message.reply_text("Нужно хотя бы 2 участника!")
            return

        today = str(date.today())
        if chat.get("last_tierlist") == today:
            save_data(data)
            await update.message.reply_text("Тирлист сегодня уже запускали! Приходи завтра 📊")
            return

        # Выбираем тему
        used = set(chat.get("used_tierlist_topics", []))
        available = [t for t in TIERLIST_TOPICS if t["id"] not in used]
        if not available:
            chat["used_tierlist_topics"] = []
            available = list(TIERLIST_TOPICS)

        topic = random.choice(available)
        chat.setdefault("used_tierlist_topics", []).append(topic["id"])
        chat["last_tierlist"] = today
        chat["tierlist_run"] = {
            "topic_id": topic["id"],
            "status": "active",
            "current_item_index": 0,
            "poll_id": None,
            "poll_message_id": None,
            "poll_started_at": None,
            "results": [],
        }
        save_data(data)

    if is_new:
        await update.message.reply_text(
            random.choice(WELCOME_MESSAGES).format(name=get_display_name(user))
        )

    _active_tierlist[chat_id] = {
        "topic": topic,
        "results": [],
        "total_voters": len(members),
        "poll_id": None,
        "poll_message_id": None,
        "poll_started_at": None,
        "voted": [],
        "event": None,
    }

    await update.message.reply_text(
        f"📊 <b>ТИРЛИСТ: {topic['name'].upper()}</b>\n\n"
        f"Коллективный суд вкуса начинается!\n"
        f"10 объектов. На каждый — 2 часа.\n"
        f"Ночью (00:00–10:00 МСК) бот не беспокоит.\n"
        f"В конце соберём народный вердикт.",
        parse_mode="HTML",
    )
    await asyncio.sleep(2)

    asyncio.create_task(_run_tierlist(context.bot, chat_id))


async def _restore_tierlist(app):
    """Восстанавливает активные тирлисты после рестарта бота."""
    data = load_data()
    bot_instance = app.bot

    for chat_id, chat_data in data.items():
        if not isinstance(chat_data, dict):
            continue
        run = chat_data.get("tierlist_run")
        if not run or run.get("status") != "active":
            continue

        topic_id = run.get("topic_id")
        topic = next((t for t in TIERLIST_TOPICS if t["id"] == topic_id), None)
        if not topic:
            run["status"] = "completed"
            save_data(data)
            continue

        members = chat_data.get("members", {})
        start_index = run.get("current_item_index", 0)
        results = run.get("results", [])

        # Если есть открытый poll — попробуем закрыть
        if run.get("poll_id") and run.get("poll_message_id"):
            try:
                poll_result = await bot_instance.stop_poll(
                    chat_id=chat_id, message_id=run["poll_message_id"]
                )
                options = poll_result.options
                total_votes = sum(o.voter_count for o in options)
                item_name = topic["items"][start_index] if start_index < len(topic["items"]) else "?"

                if total_votes > 0:
                    weighted_sum = sum(TIERLIST_SCORES[i] * o.voter_count for i, o in enumerate(options))
                    avg = round(weighted_sum / total_votes, 2)
                    tier = _score_to_tier(avg)
                else:
                    avg, tier = 0, "Срань"

                results.append({
                    "item": item_name, "avg": avg, "votes": total_votes,
                    "tier": tier, "low_votes": total_votes < MIN_VOTES,
                })
                start_index += 1

                await bot_instance.send_message(
                    chat_id=chat_id,
                    text=f"🔄 Бот перезапустился. Продолжаем тирлист!\n\n"
                         f"✅ <b>{item_name}</b> → <b>{tier}</b>\n"
                         f"Голосов: {total_votes} · Средняя: {avg}",
                    parse_mode="HTML",
                )
            except Exception:
                # Poll уже закрыт или недоступен — пропускаем объект
                start_index += 1

        if start_index >= len(topic["items"]):
            await _finalize_tierlist(bot_instance, chat_id, topic, results)
            continue

        # Восстанавливаем in-memory state и продолжаем
        _active_tierlist[chat_id] = {
            "topic": topic,
            "results": results,
            "total_voters": len(members),
            "poll_id": None,
            "poll_message_id": None,
            "poll_started_at": None,
            "voted": [],
            "event": None,
        }

        run["results"] = results
        run["current_item_index"] = start_index
        save_data(data)

        asyncio.create_task(_run_tierlist(bot_instance, chat_id, start_index))


# ───────────────────────── WORDLE ─────────────────────────

def _normalize(word: str) -> str:
    return word.strip().lower().replace("ё", "е")


def _check_wordle(guess: str, answer: str) -> list[str]:
    """Проверка слова по правилам Wordle с корректной обработкой дублей."""
    result = ["⬛"] * 5
    answer_chars = list(answer)

    # Проход 1: точные совпадения (🟩)
    for i in range(5):
        if guess[i] == answer_chars[i]:
            result[i] = "🟩"
            answer_chars[i] = None

    # Проход 2: буква есть, но не на месте (🟨)
    for i in range(5):
        if result[i] == "🟩":
            continue
        if guess[i] in answer_chars:
            result[i] = "🟨"
            answer_chars[answer_chars.index(guess[i])] = None

    return result


def _calc_points(result: list[str], guess: str, revealed: dict) -> tuple[int, list[str]]:
    """Считает очки за попытку. Возвращает (очки, детали)."""
    points = 0
    details = []

    for i, (letter, status) in enumerate(zip(guess, result)):
        if status == "⬛":
            continue

        letter_info = revealed.get(letter, {"positions": set(), "known": False})

        if status == "🟩":
            if not letter_info["known"] and i not in letter_info["positions"]:
                # Новая буква + новая позиция
                points += 3
                details.append(f"{letter.upper()} +3")
            elif letter_info["known"] and i not in letter_info["positions"]:
                # Известная буква, новая позиция
                points += 1
                details.append(f"{letter.upper()} +1")
            # Иначе 0 — всё уже было известно
        elif status == "🟨":
            if not letter_info["known"]:
                # Новая буква (жёлтая)
                points += 2
                details.append(f"{letter.upper()} +2")

    return points, details


def _update_revealed(result: list[str], guess: str, revealed: dict):
    """Обновляет revealed после попытки."""
    for i, (letter, status) in enumerate(zip(guess, result)):
        if status == "⬛":
            continue
        if letter not in revealed:
            revealed[letter] = {"positions": set(), "known": False}
        revealed[letter]["known"] = True
        if status == "🟩":
            revealed[letter]["positions"].add(i)


def _format_known(answer: str, revealed: dict) -> str:
    """Форматирует известные позиции: К_Ш_А"""
    chars = []
    for i, letter in enumerate(answer):
        info = revealed.get(letter, {"positions": set()})
        if i in info.get("positions", set()):
            chars.append(letter.upper())
        else:
            chars.append("_")
    return "".join(chars)


def _format_misplaced(revealed: dict, answer: str) -> str:
    """Список букв которые есть, но позиция не найдена."""
    misplaced = []
    for letter, info in revealed.items():
        if info["known"]:
            # Проверяем есть ли неоткрытые позиции этой буквы
            answer_positions = {i for i, c in enumerate(answer) if c == letter}
            if not answer_positions.issubset(info["positions"]):
                if letter.upper() not in misplaced:
                    misplaced.append(letter.upper())
    return ", ".join(misplaced) if misplaced else ""


async def _wordle_timeout(bot: Bot, chat_id: str):
    """Завершает игру по таймауту неактивности."""
    await asyncio.sleep(WORDLE_TIMEOUT)
    state = _active_wordle.pop(chat_id, None)
    if state:
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Wordle завершён по таймауту!\n\nСлово было: <b>{state['word'].upper()}</b>",
            parse_mode="HTML",
        )


def _restart_wordle_timer(bot: Bot, chat_id: str):
    """Перезапускает таймер неактивности."""
    old = _wordle_timers.pop(chat_id, None)
    if old:
        old.cancel()
    _wordle_timers[chat_id] = asyncio.create_task(_wordle_timeout(bot, chat_id))


async def _finish_wordle(bot: Bot, chat_id: str, state: dict, won: bool,
                         winner_id: str | None = None, loser_id: str | None = None):
    """Финализирует игру: сохраняет статистику, объявляет результат."""
    timer = _wordle_timers.pop(chat_id, None)
    if timer:
        timer.cancel()
    _active_wordle.pop(chat_id, None)

    data = load_data()
    chat = get_chat_data(data, chat_id)
    members = chat["members"]
    ws = chat.setdefault("wordle_stats", {})

    # Обновляем статистику по всем игрокам
    all_players = set()
    for a in state["attempts"]:
        all_players.add(a["player_id"])

    for pid in all_players:
        if pid not in ws:
            ws[pid] = {"games_played": 0, "games_won": 0, "total_points": 0, "sixth_fails": 0}
        ws[pid]["games_played"] += 1
        ws[pid]["total_points"] += state["scores"].get(pid, 0)

    if won and winner_id and winner_id in ws:
        ws[winner_id]["games_won"] += 1

    if not won and loser_id and loser_id in ws:
        ws[loser_id]["sixth_fails"] += 1

    save_data(data)

    # Форматируем итоги
    lines = []
    if won:
        last = state["attempts"][-1]
        winner_name = members.get(winner_id, {}).get("name", "Неизвестный")
        mention = f'<a href="tg://user?id={winner_id}">{winner_name}</a>'
        lines.append(f"🎉 <b>WORDLE — ПОБЕДА!</b>\n")
        lines.append(f"{last['word'].upper()} → {''.join(last['result'])}\n")
        lines.append(f"Слово: <b>{state['word'].upper()}</b>")
        lines.append(f"Угадал: {mention} за {len(state['attempts'])} попыток")
        lines.append(f"Награда: +{last.get('win_points', 2)} очков")
    else:
        lines.append(f"💀 <b>WORDLE — ПРОВАЛ</b>\n")
        lines.append(f"Слово было: <b>{state['word'].upper()}</b>")
        if loser_id:
            loser_name = members.get(loser_id, {}).get("name", "Неизвестный")
            mention = f'<a href="tg://user?id={loser_id}">{loser_name}</a>'
            lines.append(f"\n{mention} сделал последнюю попытку: -5 очков")

    # Итоги раунда
    if state["scores"]:
        lines.append("\n📊 <b>Итоги раунда:</b>")
        sorted_scores = sorted(state["scores"].items(), key=lambda x: x[1], reverse=True)
        for pid, pts in sorted_scores:
            pname = members.get(pid, {}).get("name", f"Игрок {pid}")
            sign = f"+{pts}" if pts >= 0 else str(pts)
            lines.append(f"{pname}: {sign}")

    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")


async def wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)

    if chat_id in _active_wordle:
        await update.message.reply_text("Wordle уже идёт! Отвечайте реплаем на сообщение бота.")
        return

    data = load_data()
    chat = get_chat_data(data, chat_id)

    user = update.effective_user
    await register_member(chat, str(user.id), get_display_name(user), user.username)
    save_data(data)

    word = random.choice(list(_wordle_words))

    prompt_msg = await update.message.reply_text(
        "🟩 <b>WORDLE</b>\n\n"
        "Угадайте слово из 5 букв!\n"
        f"Попыток: {WORDLE_MAX_ATTEMPTS}\n\n"
        "Отвечайте реплаем на это сообщение.",
        parse_mode="HTML",
    )

    _active_wordle[chat_id] = {
        "word": word,
        "attempts": [],
        "revealed": {},
        "last_player_id": None,
        "last_move_time": None,
        "prompt_message_id": prompt_msg.message_id,
        "scores": {},
    }

    _restart_wordle_timer(context.bot, chat_id)


async def wordle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return

    chat_id = str(update.effective_chat.id)
    state = _active_wordle.pop(chat_id, None)
    if not state:
        await update.message.reply_text("Нет активной игры Wordle.")
        return

    timer = _wordle_timers.pop(chat_id, None)
    if timer:
        timer.cancel()

    await update.message.reply_text(
        f"🛑 Wordle остановлен.\n\nСлово было: <b>{state['word'].upper()}</b>",
        parse_mode="HTML",
    )


async def wordle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик реплаев с попытками Wordle."""
    if not update.message or not update.message.reply_to_message:
        return
    if not update.effective_chat or update.effective_chat.type == "private":
        return

    chat_id = str(update.effective_chat.id)
    state = _active_wordle.get(chat_id)
    if not state:
        return

    # Проверяем что реплай на сообщение бота
    if update.message.reply_to_message.message_id != state["prompt_message_id"]:
        return

    user = update.effective_user
    if not user or user.is_bot:
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    guess = _normalize(text)

    # Валидация
    if len(guess) != 5 or not all("а" <= c <= "я" for c in guess):
        await update.message.reply_text(f'❌ "{text}" — нужно ровно 5 русских букв')
        return

    if guess not in _wordle_words:
        await update.message.reply_text(f'❌ "{text}" — такого слова нет в словаре')
        return

    user_id = str(user.id)

    # Чередование ходов
    if state["last_player_id"] == user_id and state["last_move_time"]:
        elapsed = (datetime.now() - state["last_move_time"]).total_seconds()
        if elapsed < WORDLE_TURN_COOLDOWN:
            remaining = int((WORDLE_TURN_COOLDOWN - elapsed) / 60) + 1
            await update.message.reply_text(
                f"⏳ {get_display_name(user)}, подожди хода другого игрока или ~{remaining} мин"
            )
            return

    # Регистрируем участника
    data = load_data()
    chat = get_chat_data(data, chat_id)
    await register_member(chat, user_id, get_display_name(user), user.username)
    save_data(data)
    members = chat["members"]

    # Проверяем слово
    answer = state["word"]
    result = _check_wordle(guess, answer)

    # Считаем очки
    points, details = _calc_points(result, guess, state["revealed"])

    # Обновляем revealed
    _update_revealed(result, guess, state["revealed"])

    attempt_num = len(state["attempts"]) + 1
    won = (guess == answer)

    # Бонус за победу
    win_points = 0
    if won:
        # +2 за каждую букву, раскрытую финальным словом
        new_letters_in_final = 0
        for i, (letter, status) in enumerate(zip(guess, result)):
            if status != "⬛":
                # Считаем раскрытой если это слово добавило новую информацию
                pass
        # Просто: details уже содержит что раскрыто этим ходом
        win_points = max(2, sum(int(d.split("+")[1]) for d in details) if details else 0)
        # Минимум 2 очка за победу
        win_points = max(2, len([d for d in details if d]) * 2) if details else 2
        # Простая формула: +2 за каждую новую букву в финальном слове
        win_points = max(2, points)
        points += win_points

    # Штраф за проигрыш
    lost = (not won and attempt_num >= WORDLE_MAX_ATTEMPTS)
    if lost:
        points -= 5

    state["scores"][user_id] = state["scores"].get(user_id, 0) + points
    state["last_player_id"] = user_id
    state["last_move_time"] = datetime.now()

    attempt = {
        "player_id": user_id,
        "player_name": get_display_name(user),
        "word": guess,
        "result": result,
        "points": points,
        "win_points": win_points,
    }
    state["attempts"].append(attempt)

    _restart_wordle_timer(context.bot, chat_id)

    if won:
        await _finish_wordle(context.bot, chat_id, state, won=True, winner_id=user_id)
        return

    if lost:
        await _finish_wordle(context.bot, chat_id, state, won=False, loser_id=user_id)
        return

    # Формируем сообщение о попытке
    player_name = get_display_name(user)
    result_str = "".join(result)
    known_str = _format_known(answer, state["revealed"])
    misplaced_str = _format_misplaced(state["revealed"], answer)

    lines = [
        f"🟩 <b>WORDLE</b> (попытка {attempt_num}/{WORDLE_MAX_ATTEMPTS})\n",
        f"{guess.upper()} → {result_str}\n",
    ]

    # Подробности по буквам
    for letter, status in zip(guess, result):
        if status == "🟩":
            lines.append(f"{letter.upper()} — ✓ на месте!")
        elif status == "🟨":
            lines.append(f"{letter.upper()} — есть, но не там")
        else:
            lines.append(f"{letter.upper()} — нет")

    if points != 0:
        sign = f"+{points}" if points > 0 else str(points)
        detail_str = f" ({', '.join(details)})" if details else ""
        lines.append(f"\n{player_name} {sign} очков{detail_str}")

    lines.append(f"\nИзвестно: {known_str}")
    if misplaced_str:
        lines.append(f"Не там: {misplaced_str}")
    lines.append(f"Осталось попыток: {WORDLE_MAX_ATTEMPTS - attempt_num}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def wordle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Эта команда работает только в групповых чатах!")
        return

    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)

    ws = chat.get("wordle_stats", {})
    members = chat["members"]

    if not ws:
        await update.message.reply_text("Статистика Wordle пуста. Запусти /wordle!")
        return

    # Рейтинг по очкам
    sorted_by_points = sorted(ws.items(), key=lambda x: x[1].get("total_points", 0), reverse=True)

    lines = ["📊 <b>Wordle — Статистика чата</b>\n\n🏆 <b>Рейтинг:</b>"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, stats) in enumerate(sorted_by_points):
        name = members.get(uid, {}).get("name", f"Игрок {uid}")
        pts = stats.get("total_points", 0)
        wins = stats.get("games_won", 0)
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {name} — {pts} очков ({wins} побед)")

    # Стена позора
    shamers = [(uid, s.get("sixth_fails", 0)) for uid, s in ws.items() if s.get("sixth_fails", 0) > 0]
    if shamers:
        shamers.sort(key=lambda x: x[1], reverse=True)
        lines.append("\n😈 <b>Стена позора (слили на 6-й попытке):</b>")
        for i, (uid, fails) in enumerate(shamers):
            name = members.get(uid, {}).get("name", f"Игрок {uid}")
            lines.append(f"{i+1}. {name} — {fails} раз(а)")

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
        "📊 /tierlist — коллективный тирлист: оцениваем объекты всем чатом (раз в день)\n\n"
        "🟩 /wordle — угадай слово из 5 букв (отвечай реплаем)\n"
        "🛑 /wordle_stop — остановить текущую игру\n"
        "📊 /wordle_stats — статистика Wordle\n\n"
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
    app.add_handler(CommandHandler("tierlist", tierlist))
    app.add_handler(CommandHandler("wordle", wordle))
    app.add_handler(CommandHandler("wordle_stop", wordle_stop))
    app.add_handler(CommandHandler("wordle_stats", wordle_stats))
    app.add_handler(PollAnswerHandler(poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))
    # Wordle и Quiplash ответы — группа 1, чтобы работало параллельно с track_member
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, wordle_guess), group=1)
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, quiplash_answer), group=2)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_member))

    app.post_init = _restore_tierlist

    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
