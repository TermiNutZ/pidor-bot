"""Тесты хэндлеров команд с моками Telegram API."""
import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
from tests.conftest import make_user, make_chat, make_message, make_update, make_context


# ── Хелперы ─────────────────────────────────────────────────

def _setup_chat(data_file, chat_id="-1001", members=None):
    """Подготавливает data.json с участниками."""
    members = members or {
        "1": {"name": "Иван", "username": "ivan"},
        "2": {"name": "Пётр", "username": "petr"},
        "3": {"name": "Мария", "username": "masha"},
    }
    data = {chat_id: {"members": members, "history": {}, "stats": {}}}
    bot.save_data(data)
    return data


def _make_group_update(user_id=1, chat_id=-1001, text=""):
    user = make_user(id=user_id, first_name="Иван")
    chat = make_chat(id=chat_id, type="group")
    msg = make_message(text=text)
    return make_update(message=msg, user=user, chat=chat)


# ── /pidor ──────────────────────────────────────────────────

class TestPidor:
    @pytest.mark.asyncio
    async def test_private_chat_rejected(self, data_file):
        update = _make_group_update()
        update.effective_chat.type = "private"
        ctx = make_context()
        await bot.pidor(update, ctx)
        update.message.reply_text.assert_any_call(
            "Эта команда работает только в групповых чатах!"
        )

    @pytest.mark.asyncio
    async def test_picks_winner_first_call(self, data_file):
        _setup_chat(data_file)
        update = _make_group_update()
        ctx = make_context()

        with patch("bot.random.choice") as mock_choice, \
             patch("bot.random.uniform", return_value=0), \
             patch("bot.date") as mock_date:
            mock_date.today.return_value = date(2025, 1, 15)
            # random.choice вызывается для фраз и для выбора победителя
            # Нам важно чтобы выбор победителя был предсказуемым
            mock_choice.side_effect = lambda x: x[0] if isinstance(x, list) else x
            await bot.pidor(update, ctx)

        data = bot.load_data()
        chat = data["-1001"]
        assert "2025-01-15" in chat["history"]

    @pytest.mark.asyncio
    async def test_same_day_returns_cached(self, data_file):
        today_str = str(date.today())
        data = {"-1001": {
            "members": {
                "1": {"name": "Иван", "username": "ivan"},
                "2": {"name": "Пётр", "username": "petr"},
            },
            "history": {today_str: "2"},
            "stats": {"2": 1},
        }}
        bot.save_data(data)

        update = _make_group_update()
        ctx = make_context()
        await bot.pidor(update, ctx)

        # Должен ответить что пидор уже выбран
        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("уже выбран" in c for c in calls)

    @pytest.mark.asyncio
    async def test_single_member_rejected(self, data_file):
        data = {"-1001": {
            "members": {"1": {"name": "Иван", "username": "ivan"}},
            "history": {}, "stats": {},
        }}
        bot.save_data(data)

        update = _make_group_update()
        ctx = make_context()
        await bot.pidor(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("один" in c.lower() for c in calls)


# ── /wordle ─────────────────────────────────────────────────

class TestWordle:
    @pytest.mark.asyncio
    async def test_private_chat_rejected(self, data_file):
        update = _make_group_update()
        update.effective_chat.type = "private"
        ctx = make_context()
        await bot.wordle(update, ctx)
        update.message.reply_text.assert_any_call(
            "Эта команда работает только в групповых чатах!"
        )

    @pytest.mark.asyncio
    async def test_starts_game(self, data_file):
        _setup_chat(data_file)
        update = _make_group_update()
        ctx = make_context()

        with patch("bot.random.choice", return_value="кошка"):
            await bot.wordle(update, ctx)

        assert "-1001" in bot._active_wordle
        state = bot._active_wordle["-1001"]
        assert state["word"] == "кошка"
        assert state["attempts"] == []

    @pytest.mark.asyncio
    async def test_already_active_rejected(self, data_file):
        _setup_chat(data_file)
        bot._active_wordle["-1001"] = {"word": "тест"}

        update = _make_group_update()
        ctx = make_context()
        await bot.wordle(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("уже идёт" in c for c in calls)


# ── wordle_guess ────────────────────────────────────────────

class TestWordleGuess:
    def _setup_wordle(self, data_file, word="кошка"):
        _setup_chat(data_file)
        bot._active_wordle["-1001"] = {
            "word": word,
            "attempts": [],
            "revealed": {},
            "last_player_id": None,
            "last_move_time": None,
            "prompt_message_id": 42,
            "scores": {},
        }

    def _make_guess_update(self, text, user_id=1):
        update = _make_group_update(user_id=user_id, text=text)
        reply_to = MagicMock()
        reply_to.message_id = 42
        update.message.reply_to_message = reply_to
        update.message.text = text
        return update

    @pytest.mark.asyncio
    async def test_not_five_letters(self, data_file):
        self._setup_wordle(data_file)
        update = self._make_guess_update("аб")
        await bot.wordle_guess(update, make_context())
        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("5 русских букв" in c for c in calls)

    @pytest.mark.asyncio
    async def test_not_in_dictionary(self, data_file):
        self._setup_wordle(data_file)
        update = self._make_guess_update("ааааа")
        await bot.wordle_guess(update, make_context())
        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("нет в словаре" in c for c in calls)

    @pytest.mark.asyncio
    async def test_cooldown_same_player(self, data_file):
        self._setup_wordle(data_file)
        state = bot._active_wordle["-1001"]
        state["last_player_id"] = "1"
        state["last_move_time"] = datetime.now()

        update = self._make_guess_update("кошка", user_id=1)
        await bot.wordle_guess(update, make_context())
        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("подожди" in c for c in calls)

    @pytest.mark.asyncio
    async def test_wrong_prompt_ignored(self, data_file):
        self._setup_wordle(data_file)
        update = self._make_guess_update("кошка")
        update.message.reply_to_message.message_id = 999  # другое сообщение
        await bot.wordle_guess(update, make_context())
        update.message.reply_text.assert_not_called()


# ── /casting ────────────────────────────────────────────────

class TestCasting:
    @pytest.mark.asyncio
    async def test_same_day_rejected(self, data_file):
        today_str = str(date.today())
        data = {"-1001": {
            "members": {
                "1": {"name": "Иван", "username": "ivan"},
                "2": {"name": "Пётр", "username": "petr"},
            },
            "history": {}, "stats": {},
            "last_casting": today_str,
        }}
        bot.save_data(data)

        update = _make_group_update()
        ctx = make_context()
        await bot.casting(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("уже был" in c for c in calls)

    @pytest.mark.asyncio
    async def test_all_scenarios_played(self, data_file):
        all_ids = [s["id"] for s in bot.SCENARIOS]
        data = {"-1001": {
            "members": {
                "1": {"name": "Иван", "username": "ivan"},
                "2": {"name": "Пётр", "username": "petr"},
            },
            "history": {}, "stats": {},
            "used_scenarios": all_ids,
        }}
        bot.save_data(data)

        update = _make_group_update()
        ctx = make_context()
        await bot.casting(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("Все сценарии" in c for c in calls)

    @pytest.mark.asyncio
    async def test_used_scenarios_saved_atomically(self, data_file):
        _setup_chat(data_file)
        update = _make_group_update()
        ctx = make_context()

        await bot.casting(update, ctx)

        data = bot.load_data()
        chat = data["-1001"]
        assert len(chat.get("used_scenarios", [])) == 1
        assert chat["last_casting"] == str(date.today())

    @pytest.mark.asyncio
    async def test_already_active_rejected(self, data_file):
        _setup_chat(data_file)
        bot._active_casting["-1001"] = {}

        update = _make_group_update()
        ctx = make_context()
        await bot.casting(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("уже идёт" in c for c in calls)


# ── /battle ─────────────────────────────────────────────────

class TestTierlist:
    @pytest.mark.asyncio
    async def test_private_chat_rejected(self, data_file):
        update = _make_group_update()
        update.effective_chat.type = "private"
        ctx = make_context()
        await bot.tierlist(update, ctx)
        update.message.reply_text.assert_any_call(
            "Эта команда работает только в групповых чатах!"
        )

    @pytest.mark.asyncio
    async def test_same_day_rejected(self, data_file):
        today_str = str(date.today())
        data = {"-1001": {
            "members": {
                "1": {"name": "Иван", "username": "ivan"},
                "2": {"name": "Пётр", "username": "petr"},
            },
            "history": {}, "stats": {},
            "last_tierlist": today_str,
        }}
        bot.save_data(data)

        update = _make_group_update()
        ctx = make_context()
        await bot.tierlist(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("уже запускали" in c for c in calls)

    @pytest.mark.asyncio
    async def test_already_active_rejected(self, data_file):
        _setup_chat(data_file)
        bot._active_tierlist["-1001"] = {
            "topic": {"name": "Тест", "items": ["a", "b"]},
            "results": [],
        }

        update = _make_group_update()
        ctx = make_context()
        await bot.tierlist(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("уже идёт" in c for c in calls)

    @pytest.mark.asyncio
    async def test_starts_run(self, data_file):
        _setup_chat(data_file)
        update = _make_group_update()
        ctx = make_context()

        await bot.tierlist(update, ctx)

        assert "-1001" in bot._active_tierlist
        state = bot._active_tierlist["-1001"]
        assert state["topic"]["id"] in [t["id"] for t in bot.TIERLIST_TOPICS]

        data = bot.load_data()
        chat = data["-1001"]
        assert chat["tierlist_run"]["status"] == "active"
        assert chat["last_tierlist"] == str(date.today())

    @pytest.mark.asyncio
    async def test_all_topics_played(self, data_file):
        all_ids = [t["id"] for t in bot.TIERLIST_TOPICS]
        data = {"-1001": {
            "members": {
                "1": {"name": "Иван", "username": "ivan"},
                "2": {"name": "Пётр", "username": "petr"},
            },
            "history": {}, "stats": {},
            "used_tierlist_topics": all_ids,  # все использованы
        }}
        bot.save_data(data)

        update = _make_group_update()
        ctx = make_context()
        await bot.tierlist(update, ctx)

        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("Все темы" in c for c in calls)
        assert "-1001" not in bot._active_tierlist


class TestBattle:
    @pytest.mark.asyncio
    async def test_private_chat_rejected(self, data_file):
        update = _make_group_update()
        update.effective_chat.type = "private"
        ctx = make_context()
        await bot.battle(update, ctx)
        update.message.reply_text.assert_any_call(
            "Эта команда работает только в групповых чатах!"
        )

    @pytest.mark.asyncio
    async def test_creates_tournament(self, data_file):
        _setup_chat(data_file)
        update = _make_group_update()
        ctx = make_context()

        with patch("bot.random.choice") as mock_choice:
            mock_choice.side_effect = lambda x: x[0] if isinstance(x, list) else x
            await bot.battle(update, ctx)

        data = bot.load_data()
        chat = data["-1001"]
        assert "tournament" in chat
        assert chat["tournament"]["finished"] is False
