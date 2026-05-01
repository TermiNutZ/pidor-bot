"""Тесты маршрутизации poll_answer: tournament / casting / quiplash / unknown."""
import asyncio

import pytest

import bot
from tests.conftest import make_user, make_update, make_context, make_poll_answer


class TestPollAnswerRouting:
    @pytest.mark.asyncio
    async def test_unknown_poll_no_crash(self, data_file):
        """Голос в неизвестный poll не должен вызвать ошибку."""
        pa = make_poll_answer(poll_id="nonexistent", user_id=1)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        # Не должно бросить исключение
        await bot.poll_answer(update, ctx)

    @pytest.mark.asyncio
    async def test_casting_vote_registered(self, data_file):
        """Голос в casting poll добавляется в current_poll_voted."""
        event = asyncio.Event()
        bot._casting_poll_map["cast_poll_1"] = "-1001"
        bot._active_casting["-1001"] = {
            "current_poll_id": "cast_poll_1",
            "current_poll_voted": [],
            "total_voters": 5,
            "current_poll_event": event,
        }

        pa = make_poll_answer(poll_id="cast_poll_1", user_id=42)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        await bot.poll_answer(update, ctx)

        assert "42" in bot._active_casting["-1001"]["current_poll_voted"]

    @pytest.mark.asyncio
    async def test_casting_event_set_when_all_voted(self, data_file):
        """Event устанавливается когда все проголосовали в casting."""
        event = asyncio.Event()
        bot._casting_poll_map["cast_poll_2"] = "-1001"
        bot._active_casting["-1001"] = {
            "current_poll_id": "cast_poll_2",
            "current_poll_voted": ["1", "2"],
            "total_voters": 3,
            "current_poll_event": event,
        }

        pa = make_poll_answer(poll_id="cast_poll_2", user_id=3)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        await bot.poll_answer(update, ctx)

        assert event.is_set()

    @pytest.mark.asyncio
    async def test_casting_no_duplicate_votes(self, data_file):
        """Повторный голос того же юзера не дублируется."""
        event = asyncio.Event()
        bot._casting_poll_map["cast_poll_3"] = "-1001"
        bot._active_casting["-1001"] = {
            "current_poll_id": "cast_poll_3",
            "current_poll_voted": ["42"],
            "total_voters": 5,
            "current_poll_event": event,
        }

        pa = make_poll_answer(poll_id="cast_poll_3", user_id=42)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        await bot.poll_answer(update, ctx)

        assert bot._active_casting["-1001"]["current_poll_voted"].count("42") == 1

    @pytest.mark.asyncio
    async def test_tournament_vote_registered(self, data_file):
        """Голос в турнирный poll записывается в data.json."""
        data = {
            "tournament_polls": {
                "tour_poll_1": {
                    "chat_id": "-1001",
                    "message_id": 100,
                    "fighters": ["1", "2"],
                    "total_voters": 99,  # много, чтобы не триггернуть finish
                    "voted": [],
                    "finished": False,
                    "round_idx": 0,
                    "match_idx": 0,
                }
            }
        }
        bot.save_data(data)

        # Нужно также добавить в in-memory map
        bot._tournament_polls["tour_poll_1"] = {
            "chat_id": "-1001",
            "match_index": 0,
            "event": asyncio.Event(),
        }

        pa = make_poll_answer(poll_id="tour_poll_1", user_id=5)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        await bot.poll_answer(update, ctx)

        saved = bot.load_data()
        assert "5" in saved["tournament_polls"]["tour_poll_1"]["voted"]

    @pytest.mark.asyncio
    async def test_tierlist_vote_registered(self, data_file):
        """Голос в tierlist poll добавляется в voted."""
        event = asyncio.Event()
        bot._tierlist_poll_map["tl_poll_1"] = "-1001"
        bot._active_tierlist["-1001"] = {
            "poll_id": "tl_poll_1",
            "voted": [],
            "total_voters": 5,
            "event": event,
        }

        pa = make_poll_answer(poll_id="tl_poll_1", user_id=10)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        await bot.poll_answer(update, ctx)

        assert "10" in bot._active_tierlist["-1001"]["voted"]

    @pytest.mark.asyncio
    async def test_tierlist_event_set_when_all_voted(self, data_file):
        """Event устанавливается когда все проголосовали в tierlist."""
        event = asyncio.Event()
        bot._tierlist_poll_map["tl_poll_2"] = "-1001"
        bot._active_tierlist["-1001"] = {
            "poll_id": "tl_poll_2",
            "voted": ["1", "2"],
            "total_voters": 3,
            "event": event,
        }

        pa = make_poll_answer(poll_id="tl_poll_2", user_id=3)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        await bot.poll_answer(update, ctx)

        assert event.is_set()

    @pytest.mark.asyncio
    async def test_quiplash_vote_registered(self, data_file):
        """Голос в quiplash poll добавляется в voted."""
        bot._quiplash_poll_map["ql_poll_1"] = "-1001"
        bot._active_quiplash["-1001"] = {
            "phase": "voting",
            "vote_poll_id": "ql_poll_1",
            "voted": [],
            "total_voters": 99,  # много, чтобы не завершить
        }

        pa = make_poll_answer(poll_id="ql_poll_1", user_id=7)
        update = make_update(poll_answer=pa)
        update.poll_answer = pa
        ctx = make_context()
        await bot.poll_answer(update, ctx)

        assert "7" in bot._active_quiplash["-1001"]["voted"]
