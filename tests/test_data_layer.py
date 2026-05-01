"""Тесты слоя данных: load/save, get_chat_data, register_member."""
import json
import pytest

import bot


class TestLoadSaveData:
    def test_load_missing_file(self, data_file):
        assert bot.load_data() == {}

    def test_round_trip(self, data_file):
        original = {"chat1": {"members": {"1": {"name": "Иван", "username": "ivan"}}}}
        bot.save_data(original)
        loaded = bot.load_data()
        assert loaded == original

    def test_cyrillic_preserved(self, data_file):
        data = {"ключ": {"значение": "Привет мир"}}
        bot.save_data(data)
        loaded = bot.load_data()
        assert loaded["ключ"]["значение"] == "Привет мир"


class TestGetChatData:
    def test_creates_structure(self):
        data = {}
        chat = bot.get_chat_data(data, "123")
        assert chat == {"members": {}, "history": {}, "stats": {}}
        assert "123" in data

    def test_preserves_existing_fields(self):
        data = {"123": {
            "members": {"1": {"name": "X"}},
            "history": {},
            "stats": {},
            "used_scenarios": ["prison", "spaceship"],
            "custom_field": 42,
        }}
        chat = bot.get_chat_data(data, "123")
        assert chat["used_scenarios"] == ["prison", "spaceship"]
        assert chat["custom_field"] == 42
        assert chat["members"] == {"1": {"name": "X"}}

    def test_does_not_overwrite(self):
        data = {"123": {"members": {"1": {"name": "X"}}, "history": {}, "stats": {}}}
        chat = bot.get_chat_data(data, "123")
        assert chat["members"]["1"]["name"] == "X"


class TestRegisterMember:
    @pytest.mark.asyncio
    async def test_new_member_returns_true(self):
        chat = {"members": {}}
        result = await bot.register_member(chat, "1", "Иван", "ivan")
        assert result is True
        assert "1" in chat["members"]
        assert chat["members"]["1"]["name"] == "Иван"

    @pytest.mark.asyncio
    async def test_existing_member_returns_false(self):
        chat = {"members": {"1": {"name": "Старое", "username": "old"}}}
        result = await bot.register_member(chat, "1", "Новое", "new")
        assert result is False

    @pytest.mark.asyncio
    async def test_updates_name(self):
        chat = {"members": {"1": {"name": "Старое", "username": "old"}}}
        await bot.register_member(chat, "1", "Новое", "new")
        assert chat["members"]["1"]["name"] == "Новое"
