"""Юнит-тесты чистых функций — без I/O, без Telegram API."""
import math
import random
from unittest.mock import MagicMock

import bot


# ── _normalize ──────────────────────────────────────────────

class TestNormalize:
    def test_lowercase(self):
        assert bot._normalize("КОШКА") == "кошка"

    def test_yo_replaced(self):
        assert bot._normalize("ёлка") == "елка"

    def test_strip(self):
        assert bot._normalize("  слово  ") == "слово"

    def test_combined(self):
        assert bot._normalize("  ЁЖИК  ") == "ежик"


# ── get_display_name ────────────────────────────────────────

class TestGetDisplayName:
    def _user(self, **kw):
        u = MagicMock()
        u.first_name = kw.get("first_name", None)
        u.last_name = kw.get("last_name", None)
        u.username = kw.get("username", None)
        u.id = kw.get("id", 999)
        return u

    def test_full_name(self):
        assert bot.get_display_name(self._user(first_name="Иван", last_name="Петров")) == "Иван Петров"

    def test_first_only(self):
        assert bot.get_display_name(self._user(first_name="Иван")) == "Иван"

    def test_username_fallback(self):
        assert bot.get_display_name(self._user(username="ivan")) == "ivan"

    def test_id_fallback(self):
        assert bot.get_display_name(self._user(id=42)) == "42"


# ── _get_round_name ────────────────────────────────────────

class TestGetRoundName:
    def test_final_1(self):
        assert bot._get_round_name(1) == "Финал"

    def test_final_2(self):
        assert bot._get_round_name(2) == "Финал"

    def test_semifinal_3(self):
        assert bot._get_round_name(3) == "Полуфинал"

    def test_semifinal_4(self):
        assert bot._get_round_name(4) == "Полуфинал"

    def test_quarterfinal_5(self):
        assert bot._get_round_name(5) == "Четвертьфинал"

    def test_quarterfinal_8(self):
        assert bot._get_round_name(8) == "Четвертьфинал"

    def test_generic_9(self):
        assert bot._get_round_name(9) == "Раунд (9 участников)"

    def test_generic_16(self):
        assert bot._get_round_name(16) == "Раунд (16 участников)"


# ── _make_matches ───────────────────────────────────────────

class TestMakeMatches:
    def test_two_players(self):
        matches = bot._make_matches(["a", "b"])
        assert matches == [["a", "b"]]

    def test_three_players_triple(self):
        matches = bot._make_matches(["a", "b", "c"])
        assert len(matches) == 1
        assert len(matches[0]) == 3

    def test_four_players_two_pairs(self):
        matches = bot._make_matches(["a", "b", "c", "d"])
        assert len(matches) == 2
        assert all(len(m) == 2 for m in matches)

    def test_five_players_pair_and_triple(self):
        matches = bot._make_matches(["a", "b", "c", "d", "e"])
        assert len(matches) == 2
        sizes = sorted(len(m) for m in matches)
        assert sizes == [2, 3]

    def test_seven_players(self):
        matches = bot._make_matches(list("abcdefg"))
        assert len(matches) == 3
        sizes = sorted(len(m) for m in matches)
        assert sizes == [2, 2, 3]

    def test_all_players_present(self):
        ids = list("abcdef")
        matches = bot._make_matches(ids)
        flat = [p for m in matches for p in m]
        assert sorted(flat) == sorted(ids)

    def test_single_player(self):
        matches = bot._make_matches(["a"])
        assert matches == [["a"]]


# ── _create_tournament ──────────────────────────────────────

class TestCreateTournament:
    def test_structure_keys(self):
        random.seed(42)
        t = bot._create_tournament(["a", "b", "c", "d"], "Кто круче?")
        assert t["question"] == "Кто круче?"
        assert t["current_round"] == 0
        assert t["finished"] is False
        assert t["champion"] is None
        assert len(t["bracket"]) == 1
        assert t["total_rounds"] == math.ceil(math.log2(4))

    def test_all_players_in_bracket(self):
        random.seed(0)
        ids = ["1", "2", "3", "4", "5"]
        t = bot._create_tournament(ids, "q")
        flat = [p for m in t["bracket"][0] for p in m]
        assert sorted(flat) == sorted(ids)

    def test_total_rounds(self):
        for n, expected in [(2, 1), (3, 2), (4, 2), (5, 3), (8, 3), (9, 4)]:
            t = bot._create_tournament([str(i) for i in range(n)], "q")
            assert t["total_rounds"] == expected, f"n={n}"


# ── _check_wordle ───────────────────────────────────────────

class TestCheckWordle:
    def test_exact_match(self):
        assert bot._check_wordle("кошка", "кошка") == ["🟩"] * 5

    def test_no_match(self):
        assert bot._check_wordle("абвгд", "еёжзи") == ["⬛"] * 5

    def test_wrong_position(self):
        # guess="абвгд", answer="бааав"
        # pass 1: нет exact matches
        # pass 2: а[0]→есть в answer (pos 1,2,3) → 🟨
        #         б[1]→есть в answer (pos 0) → 🟨
        #         в[2]→есть в answer (pos 4) → 🟨
        #         г[3]→нет → ⬛
        #         д[4]→нет → ⬛
        result = bot._check_wordle("абвгд", "бааав")
        assert result == ["🟨", "🟨", "🟨", "⬛", "⬛"]

    def test_duplicate_in_guess_one_correct(self):
        # guess: аабвг, answer: аедеж
        # а[0]: exact → 🟩, а[1]: 'а' уже исчерпана → ⬛
        result = bot._check_wordle("аабвг", "аедеж")
        assert result[0] == "🟩"
        assert result[1] == "⬛"

    def test_duplicate_in_answer(self):
        # answer: ааааа, guess: бааав
        # б → ⬛, а → 🟩, а → 🟩, а → 🟩, в → ⬛
        result = bot._check_wordle("бааав", "ааааа")
        assert result == ["⬛", "🟩", "🟩", "🟩", "⬛"]

    def test_yellow_before_green(self):
        # guess: ааббб, answer: бааав
        # а[0] → exact? answer[0]='б' → no. answer has 'а' at 1,2,3 → 🟨
        # а[1] → exact? answer[1]='а' → 🟩
        # б[2] → exact? answer[2]='а' → no. answer has 'б' at 0 → 🟨
        # б[3] → exact? answer[3]='а' → no. answer 'б' already used → ⬛
        # б[4] → exact? answer[4]='в' → no → ⬛
        result = bot._check_wordle("ааббб", "бааав")
        assert result == ["🟨", "🟩", "🟨", "⬛", "⬛"]


# ── _calc_points ────────────────────────────────────────────

class TestCalcPoints:
    def test_new_green_letter(self):
        result = ["🟩", "⬛", "⬛", "⬛", "⬛"]
        points, _ = bot._calc_points(result, "кошка", {})
        assert points == 3

    def test_new_yellow_letter(self):
        result = ["🟨", "⬛", "⬛", "⬛", "⬛"]
        points, _ = bot._calc_points(result, "кошка", {})
        assert points == 2

    def test_known_letter_new_position(self):
        revealed = {"к": {"positions": {2}, "known": True}}
        result = ["🟩", "⬛", "⬛", "⬛", "⬛"]
        points, _ = bot._calc_points(result, "кошка", revealed)
        assert points == 1

    def test_already_revealed(self):
        revealed = {"к": {"positions": {0}, "known": True}}
        result = ["🟩", "⬛", "⬛", "⬛", "⬛"]
        points, _ = bot._calc_points(result, "кошка", revealed)
        assert points == 0

    def test_gray_gives_zero(self):
        result = ["⬛"] * 5
        points, _ = bot._calc_points(result, "абвгд", {})
        assert points == 0


# ── _update_revealed ────────────────────────────────────────

class TestUpdateRevealed:
    def test_adds_green(self):
        revealed = {}
        bot._update_revealed(["🟩", "⬛", "⬛", "⬛", "⬛"], "кошка", revealed)
        assert "к" in revealed
        assert 0 in revealed["к"]["positions"]
        assert revealed["к"]["known"] is True

    def test_adds_yellow(self):
        revealed = {}
        bot._update_revealed(["🟨", "⬛", "⬛", "⬛", "⬛"], "кошка", revealed)
        assert "к" in revealed
        assert revealed["к"]["known"] is True
        assert 0 not in revealed["к"]["positions"]  # жёлтая не добавляет позицию

    def test_ignores_gray(self):
        revealed = {}
        bot._update_revealed(["⬛"] * 5, "абвгд", revealed)
        assert revealed == {}


# ── _format_known ───────────────────────────────────────────

class TestFormatKnown:
    def test_nothing_revealed(self):
        assert bot._format_known("кошка", {}) == "_____"

    def test_first_and_last(self):
        revealed = {
            "к": {"positions": {0}},
            "а": {"positions": {4}},
        }
        assert bot._format_known("кошка", revealed) == "К___А"

    def test_all_revealed(self):
        revealed = {
            "к": {"positions": {0}},
            "о": {"positions": {1}},
            "ш": {"positions": {2}},
            "а": {"positions": {4}},  # к at 3 also needed
        }
        revealed["к"]["positions"].add(3)  # к appears at 0 and 3? No, "кошка" has к at 0
        # кошка: к=0, о=1, ш=2, к=3, а=4
        revealed = {
            "к": {"positions": {0, 3}},
            "о": {"positions": {1}},
            "ш": {"positions": {2}},
            "а": {"positions": {4}},
        }
        assert bot._format_known("кошка", revealed) == "КОШКА"


# ── _format_misplaced ──────────────────────────────────────

class TestScoreToTier:
    def test_best(self):
        assert bot._score_to_tier(3.0) == "Лучший из лучших"
        assert bot._score_to_tier(2.5) == "Лучший из лучших"

    def test_good(self):
        assert bot._score_to_tier(2.49) == "Харош"
        assert bot._score_to_tier(1.5) == "Харош"

    def test_ok(self):
        assert bot._score_to_tier(1.49) == "Под пивко сойдет"
        assert bot._score_to_tier(0.5) == "Под пивко сойдет"

    def test_bad(self):
        assert bot._score_to_tier(0.49) == "Срань"
        assert bot._score_to_tier(0) == "Срань"


class TestFormatMisplaced:
    def test_no_misplaced(self):
        assert bot._format_misplaced({}, "кошка") == ""

    def test_letter_with_unguessed_positions(self):
        # к known, but only position 0 found — к also at position 3
        revealed = {"к": {"positions": {0}, "known": True}}
        result = bot._format_misplaced(revealed, "кошка")
        assert "К" in result

    def test_fully_placed_letter_not_shown(self):
        # о at position 1, fully placed
        revealed = {"о": {"positions": {1}, "known": True}}
        result = bot._format_misplaced(revealed, "кошка")
        assert "О" not in result
