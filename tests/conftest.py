import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Autouse: сброс глобального стейта между тестами
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_global_state():
    import bot
    bot._tournament_polls.clear()
    bot._tournament_timers.clear()
    bot._active_quiplash.clear()
    bot._quiplash_poll_map.clear()
    bot._quiplash_vote_timers.clear()
    bot._active_casting.clear()
    bot._casting_poll_map.clear()
    bot._active_wordle.clear()
    bot._wordle_timers.clear()
    bot._active_tierlist.clear()
    bot._tierlist_poll_map.clear()
    bot._data_lock = asyncio.Lock()
    yield
    bot._tournament_polls.clear()
    bot._tournament_timers.clear()
    bot._active_quiplash.clear()
    bot._quiplash_poll_map.clear()
    bot._quiplash_vote_timers.clear()
    bot._active_casting.clear()
    bot._casting_poll_map.clear()
    bot._active_wordle.clear()
    bot._wordle_timers.clear()
    bot._active_tierlist.clear()
    bot._tierlist_poll_map.clear()


# ---------------------------------------------------------------------------
# Autouse: asyncio.sleep мгновенный
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


# ---------------------------------------------------------------------------
# Временный data.json
# ---------------------------------------------------------------------------

@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = str(tmp_path / "data.json")
    monkeypatch.setattr("bot.DATA_FILE", path)
    return path


# ---------------------------------------------------------------------------
# Фабрики моков Telegram
# ---------------------------------------------------------------------------

def make_user(id=1, first_name="Иван", last_name="Иванов",
              username="ivanov", is_bot=False):
    user = MagicMock()
    user.id = id
    user.first_name = first_name
    user.last_name = last_name
    user.username = username
    user.is_bot = is_bot
    return user


def make_chat(id=-1001, type="group"):
    chat = MagicMock()
    chat.id = id
    chat.type = type
    return chat


def make_message(chat=None, user=None, text="", message_id=1):
    msg = AsyncMock()
    msg.message_id = message_id
    msg.text = text
    msg.reply_text = AsyncMock(return_value=msg)
    msg.set_reaction = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.reply_to_message = None
    msg.new_chat_members = []
    return msg


def make_update(message=None, user=None, chat=None, poll_answer=None):
    update = MagicMock()
    update.effective_user = user or make_user()
    update.effective_chat = chat or make_chat()
    update.message = message or make_message()
    update.poll_answer = poll_answer
    return update


def make_context(bot=None):
    ctx = MagicMock()
    ctx.bot = bot or make_bot()
    return ctx


def make_bot():
    bot = AsyncMock()
    poll_msg = AsyncMock()
    poll_msg.message_id = 100
    poll_msg.poll = MagicMock()
    poll_msg.poll.id = "poll_123"
    bot.send_poll = AsyncMock(return_value=poll_msg)
    bot.send_message = AsyncMock()
    bot.stop_poll = AsyncMock()
    return bot


def make_poll_answer(poll_id="poll_123", user_id=1):
    pa = MagicMock()
    pa.poll_id = poll_id
    pa.user = make_user(id=user_id)
    pa.option_ids = [0]
    return pa
