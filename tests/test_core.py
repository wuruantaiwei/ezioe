"""Regression tests for the answer engine (no network needed)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ioeauto import asr, cli, config, games, report  # noqa: E402
from ioeauto.api import ApiError  # noqa: E402


def q(**kw):
    base = {"type": 3, "Point": 10, "numTChar": 0, "STT": 0,
            "content": {"content": ""}, "Description": {"content": ""},
            "ans": []}
    base.update(kw)
    return base


# ------------------------------------------------------------------ answers
def test_fill_words_joined_in_order():
    question = q(ans=[{"orderTrue": 2, "content": "like"},
                      {"orderTrue": 1, "content": "would"},
                      {"orderTrue": 3, "content": "to"}])
    assert games.candidates(question)[0] == "would|like|to"


def test_order_game_offers_both_separators():
    question = q(type=games.T_ORDER,
                 ans=[{"orderTrue": 2, "content": "milk"},
                      {"orderTrue": 1, "content": "drinks"}])
    options = games.candidates(question)
    assert "drinks|milk" in options and "drinks milk" in options


def test_pair_uses_left_right_shape():
    question = q(type=games.T_PAIR, content={"content": "Hobby"},
                 ans=[{"orderTrue": 0, "content": "sở thích"}])
    assert games.candidates(question)[0] == "Hobby|sở thích"


def test_choice_returns_option_text():
    question = q(type=games.T_CHOICE, ans=[{"orderTrue": 0, "content": "a"},
                                           {"orderTrue": 1, "content": "b"}])
    assert games.candidates(question) == ["a", "b"]


def test_shape_mask_uses_validated_words_first():
    question = q(type=games.T_SHAPE, content={"content": "BIR*HDAY"})
    assert games.candidates(question, 5, ["birthday"])[0] == "t"


def test_underscore_blank_is_treated_as_a_mask():
    question = q(type=games.T_SHAPE, numTChar=4,
                 content={"content": "People raise livestock such as ____ and cows."})
    assert games._masked_token(question["content"]["content"]) == "____"


# ---------------------------------------------------------------------- asr
def test_transcript_picks_the_word_in_the_slot():
    question = q(type=games.T_SHAPE, content={
        "content": "People in the highlands often raise livestock such as **** and cows."})
    heard = asr.masked_answers(question, "people in the highlands often raise livestock such as pigs and cows")
    assert heard[0] == "pigs"


def test_partial_mask_returns_missing_letters():
    question = q(type=games.T_SHAPE, content={"content": "She is ****ping gifts."})
    heard = asr.masked_answers(question, "she is wrapping gifts")
    assert "wrap" in heard and "wrapping" in heard


def test_rank_options_prefers_the_spoken_sentence():
    question = q(type=games.T_SHAPE, content={"content": "Listen and choose the best sentence:"})
    options = ["She's tall and thin.", "Peter fell asleep while he was reading."]
    heard = asr.rank_options(question, options, "peter fell asleep while he was reading")
    assert heard[0].startswith("Peter")


def test_score_against_is_case_and_punctuation_tolerant():
    assert asr.score_against("The cat sat.", "the cat sat") == 1.0


# ------------------------------------------------------------------- config
def test_config_merges_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"pace": [10, 20], "notify": {"telegram": {"token": "x"}}}', encoding="utf-8")
    loaded = config.load(str(path))
    assert loaded["pace"] == [10, 20]
    assert loaded["notify"]["telegram"]["token"] == "x"
    assert loaded["notify"]["telegram"]["chat_id"] == ""   # default kept
    assert loaded["attempts"] == config.DEFAULTS["attempts"]


def test_config_missing_file_returns_defaults(tmp_path):
    assert config.load(str(tmp_path / "nope.json"))["pace"] == config.DEFAULTS["pace"]


# ------------------------------------------------------------------- report
def test_report_records_and_summarises(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    report.add_exercise("u", {"idexercise": 1, "gameId": 2}, {"point": 80, "examScore": 100}, 12.5)
    rows = report.summary()
    assert rows and rows[0]["mark"] == "80/100"
    assert report.weak_spots()[0]["idexercise"] == 1


# --------------------------------------------------------------- multi account
class _Stub:
    def __init__(self, name):
        self.name = name

    def post(self, *_args, **_kw):
        return {"s_username": self.name}


def test_whoami_normalises_the_account_name():
    assert cli._whoami(_Stub("SVTBTranVanBinh")) == "svtbtranvanbinh"
    assert cli._whoami(_Stub("")) == ""


def test_whoami_survives_an_unusable_token():
    class Broken:
        def post(self, *_a, **_k):
            raise cli.ApiError(86, "token_expired")

    assert cli._whoami(Broken()) == ""


def test_session_path_is_per_account(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA", str(tmp_path))
    assert cli._state_path("acc-one") != cli._state_path("acc-two")
    assert cli._state_path("acc-one").endswith("session_acc-one.json")


def test_rounds_today_does_not_mix_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    report.add_exercise("acc-one", {"idexercise": 1, "gameId": 1}, {"point": 100, "examScore": 100}, 5)
    report.mark_round("acc-one", True)
    assert report.rounds_today("acc-one") == 1
    assert report.rounds_today("acc-two") == 0


# ------------------------------------------------------- round bookkeeping
class _RoundStub:
    """Minimal client: only the round endpoint and the release schedule."""

    def __init__(self, games, current_round=13, sys_round=15, next_start=""):
        self.games = games
        self.current_round = current_round
        self.sys_round = sys_round
        self.next_start = next_start

    def post(self, path, *_args, **_kw):
        assert path == games.ROUND_PATH
        return {"roundInfoRes": {
            "lstgames": self.games,
            "WaitConfirm": False,
            "bilInfo": {"current_round": self.current_round, "sysRound": self.sys_round,
                        "sysRoundMax": 35},
        }}

    def schedule(self):
        return [{"status": 0, "startDate": "2026-08-15T10:00:00+07:00"},
                {"status": 3, "startDate": self.next_start}] if self.next_start else []


def _game(exercise, status, rank=True):
    return {"idexercise": exercise, "gameId": 1, "Status": status, "isRank": rank, "mark": 0}


def test_round_counters_come_from_inside_round_info_res():
    stub = _RoundStub([_game(1, 0)], current_round=13, sys_round=15)
    info = games.round_info(stub)
    assert info["current_round"] == 13 and info["sysRound"] == 15
    assert games.rounds_left(info) == 2


def test_next_round_start_is_none_while_games_are_still_open():
    stub = _RoundStub([_game(1, 0), _game(2, 2)], next_start="2026-11-30T09:00:00+07:00")
    assert games.next_round_start(stub) is None


def test_next_round_start_reports_the_release_date_when_the_round_is_done():
    stub = _RoundStub([_game(1, 2), _game(2, 3)], next_start="2026-11-30T09:00:00+07:00")
    from datetime import datetime

    assert games.next_round_start(stub) == datetime.fromisoformat(
        "2026-11-30T09:00:00+07:00").timestamp()


def test_pending_ignores_finished_locked_and_non_rank_games():
    stub = _RoundStub([_game(1, 0), _game(2, 1), _game(3, 2), _game(4, 3),
                       _game(5, 0, rank=False)])
    assert [e["idexercise"] for e in games.pending_exercises(stub)] == [1, 2]


def test_mark_round_matches_the_account_regardless_of_case(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    report.add_exercise("SVTBTranVanBinh", {"idexercise": 1, "gameId": 1},
                        {"point": 100, "examScore": 100}, 5)
    report.mark_round("svtbtranvanbinh", True)
    assert report.rounds_today("SVTBTranVanBinh") == 1
    assert report.rounds_today("acc-other") == 0


def test_failed_retry_does_not_unflag_a_finished_round(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    report.add_exercise("acc-one", {"idexercise": 1, "gameId": 1}, {"point": 100, "examScore": 100}, 5)
    report.mark_round("acc-one", True)
    report.mark_round("acc-one", False, "còn 2 bài")
    assert report.rounds_today("acc-one") == 1


# ----------------------------------------------- multi account run control
def test_second_account_still_plays_after_the_first_one_is_done(capsys, tmp_path, monkeypatch):
    """The finished account must not end the turn for the one that has work."""
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(cli.notify, "send", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.cfgmod, "load", lambda *_a, **_k: {
        **config.DEFAULTS, "accounts": [{"user": "acc-done"}, {"user": "acc-work"}],
        "rounds": 1, "turbo": True, "max_rounds_per_day": 0, "active_hours": [0, 24],
    })

    payloads = {
        "acc-done": [{"current_round": 16, "sysRound": 15, "games": [_game(1, 2), _game(2, 3)]}],
        "acc-work": [{"current_round": 15, "sysRound": 15, "games": [_game(7, 0), _game(8, 1)]},
                     {"current_round": 16, "sysRound": 15, "games": [_game(7, 2), _game(8, 2)]}],
    }
    played_out = {"acc-done": False, "acc-work": False}

    class FakeClient:
        def __init__(self, name):
            self.name = name

        def post(self, path, body=None, **_kw):
            if path != games.ROUND_PATH:
                return {}
            rows = payloads[self.name]
            row = rows[-1] if played_out[self.name] else rows[0]
            if True:
                return {"roundInfoRes": {"lstgames": row["games"], "WaitConfirm": False,
                                         "bilInfo": {"current_round": row["current_round"],
                                                     "sysRound": row["sysRound"],
                                                     "sysRoundMax": 35}}}
            return {}

        def schedule(self):
            return [{"status": 3, "startDate": "2026-11-30T09:00:00+07:00"}]

        def balance(self):
            return {}

    monkeypatch.setattr(cli, "_client", lambda user, password="": FakeClient(user))
    played = []

    def fake_solve(client, *_a, **_k):
        played.append(client.name)
        played_out[client.name] = True        # the games are submitted now
        return []

    monkeypatch.setattr(games, "solve_round", fake_solve)

    args = cli.argparse.Namespace(no_sleep=False, turbo=True, dry_run=False, pace=None,
                                  between=None, delay=None, rounds=1, only=None,
                                  user=None, password=None, config="x")
    assert cli.cmd_run(args) == 0

    out = capsys.readouterr().out
    assert "acc-done: vòng đã xong" in out             # vòng đã đóng (khoá) -> không hồi sinh
    assert played == ["acc-work"]                      # the busy account did run
    assert report.rounds_today("acc-work") == 1        # and its round was counted
    assert report.rounds_today("acc-done") == 0


def test_round_is_not_counted_while_exercises_are_still_missing(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(cli.notify, "send", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.cfgmod, "load", lambda *_a, **_k: {
        **config.DEFAULTS, "accounts": [{"user": "acc"}], "rounds": 3,
        "turbo": True, "max_rounds_per_day": 0, "active_hours": [0, 24],
    })

    class FakeClient:
        def __init__(self, name):
            self.name = name

        def post(self, path, body=None, **_kw):
            if path == games.ROUND_PATH:      # the game never gets past Status 0
                return {"roundInfoRes": {"lstgames": [_game(7, 0), _game(8, 0)], "WaitConfirm": False,
                                         "bilInfo": {"current_round": 15, "sysRound": 15}}}
            return {}

        def schedule(self):
            return []

        def balance(self):
            return {}

    monkeypatch.setattr(cli, "_client", lambda user, password="": FakeClient(user))
    monkeypatch.setattr(games, "solve_round", lambda *_a, **_k: [])

    args = cli.argparse.Namespace(no_sleep=False, turbo=True, dry_run=False, pace=None,
                                  between=None, delay=None, rounds=3, only=None,
                                  user=None, password=None, config="x")
    assert cli.cmd_run(args) == 0
    assert "chưa tính vòng này" in capsys.readouterr().out
    assert report.rounds_today("acc") == 0


# ------------------------------------------------------ stats / scheduler / jobs
def test_stats_aggregates_per_day_and_never_mixes_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    report.add_exercise("acc-a", {"idexercise": 1, "gameId": 1},
                        {"point": 80, "examScore": 100, "isPass": True}, 4)
    report.mark_round("acc-a", True)
    report.add_exercise("acc-b", {"idexercise": 2, "gameId": 2},
                        {"point": 100, "examScore": 100, "isPass": True}, 6)

    only_a = report.stats(days=2, user="acc-a")
    assert len(only_a) == 1 and only_a[0]["points"] == 80 and only_a[0]["rounds"] == 1
    both = report.stats(days=2)
    assert both[0]["exercises"] == 2 and both[0]["passed"] == 2


def test_summary_filters_by_account_before_limiting(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    for index in range(30):                       # acc-b floods the newest rows
        report.add_exercise("acc-b", {"idexercise": index, "gameId": 1},
                            {"point": 100, "examScore": 100}, 1)
    report.add_exercise("acc-a", {"idexercise": 999, "gameId": 1},
                        {"point": 90, "examScore": 100}, 1)
    rows = report.summary(limit=5, user="acc-a")
    assert [row["exercise"] for row in rows] == [999]


def test_csv_export_writes_one_row_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    report.add_exercise("acc", {"idexercise": 1, "gameId": 1}, {"point": 70, "examScore": 100}, 2)
    out = tmp_path / "s.csv"
    args = cli.argparse.Namespace(days=3, user="", csv=str(out))
    assert cli.cmd_stats(args) == 0
    body = out.read_text(encoding="utf-8").strip().splitlines()
    assert body[0].startswith("date,") and len(body) == 2


def test_scheduler_dry_run_builds_a_daily_job(capsys):
    from ioeauto import scheduler

    assert scheduler.install(at="08:30", dry=True) == 0
    out = capsys.readouterr().out
    assert "08:30" in out and "run" in out
    assert scheduler.install(every=90, dry=True) == 0
    assert "90" in capsys.readouterr().out


def test_parallel_jobs_keep_bank_and_report_consistent(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(cli, "BANK", str(tmp_path / "answers.json"))
    monkeypatch.setattr(cli.notify, "send", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.cfgmod, "load", lambda *_a, **_k: {
        **config.DEFAULTS, "accounts": [{"user": "acc-1"}, {"user": "acc-2"}],
        "rounds": 1, "turbo": True, "jobs": 2, "max_rounds_per_day": 0,
        "active_hours": [0, 24], "wait_for_round": False,
    })

    done = {"acc-1": False, "acc-2": False}

    class FakeClient:
        def __init__(self, name):
            self.name = name

        def post(self, path, body=None, **_kw):
            if path != games.ROUND_PATH:
                return {}
            games_state = [_game(1, 2 if done[self.name] else 0)]
            return {"roundInfoRes": {"lstgames": games_state, "WaitConfirm": False,
                                     "bilInfo": {"current_round": 1, "sysRound": 15}}}

        def schedule(self):
            return []

        def balance(self):
            return {}

    def fake_solve(client, bank, **_kw):
        question = {"type": 1, "content": {"content": client.name}, "ans": []}
        games._store(bank, question, "x", 10)
        report.add_exercise(client.name, {"idexercise": 1, "gameId": 1},
                            {"point": 100, "examScore": 100}, 1)
        done[client.name] = True
        return []

    monkeypatch.setattr(cli, "_client", lambda user, password="": FakeClient(user))
    monkeypatch.setattr(games, "solve_round", fake_solve)

    args = cli.argparse.Namespace(no_sleep=False, turbo=True, dry_run=False, pace=None,
                                  between=None, delay=None, rounds=1, jobs=2, only=None,
                                  user=None, password=None, config="x")
    assert cli.cmd_run(args) == 0
    assert report.rounds_today("acc-1") == 1 and report.rounds_today("acc-2") == 1
    bank = json.loads((tmp_path / "answers.json").read_text(encoding="utf-8"))
    assert len(bank) == 2


def test_config_only_is_honoured(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(cli.notify, "send", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.cfgmod, "load", lambda *_a, **_k: {
        **config.DEFAULTS, "accounts": [{"user": "acc"}], "only": 77,
        "rounds": 1, "active_hours": [0, 24],
    })
    seen = []

    class FakeClient:
        def post(self, path, body=None, **_kw):
            if path == games.ROUND_PATH:
                return {"roundInfoRes": {"lstgames": [_game(77, 0), _game(78, 0)],
                                         "bilInfo": {"current_round": 1, "sysRound": 15}}}
            return {}

        def schedule(self):
            return []

        def balance(self):
            return {}

    monkeypatch.setattr(cli, "_client", lambda user, password="": FakeClient())
    monkeypatch.setattr(games, "solve_round", lambda *a, **k: seen.append(k.get("only")) or [])
    args = cli.argparse.Namespace(no_sleep=False, turbo=True, dry_run=False, pace=None,
                                  between=None, delay=None, rounds=1, jobs=1, only=None,
                                  user=None, password=None, config="x")
    assert cli.cmd_run(args) == 0
    assert seen == [77]


def test_doctor_reports_a_usable_setup(tmp_path, capsys):
    from ioeauto import doctor

    path = tmp_path / "config.json"
    path.write_text('{"accounts": [{"user": "acc", "password": "x"}]}', encoding="utf-8")
    code = doctor.run(str(path), check_accounts=False)
    out = capsys.readouterr().out
    assert "Python" in out and "config đọc được" in out
    assert code in (0, 1)


def test_gui_module_imports_without_display():
    from ioeauto import gui

    assert callable(gui.run)


# --------------------------------------------------- đóng tính năng Tự luyện
CLOSED = ApiError(1, "Hệ thống đóng tính năng Tự luyện trong tài khoản học sinh từ "
                     "19/11/2026 17:00:01 - 19/11/2026 20:00:00")


def test_closed_window_is_parsed_from_the_message():
    window = games.closed_window(CLOSED)
    assert window is not None
    from datetime import datetime

    assert datetime.fromtimestamp(window[0]).strftime("%d/%m %H:%M") == "19/11 17:00"
    assert datetime.fromtimestamp(window[1]).strftime("%d/%m %H:%M") == "19/11 20:00"
    assert games.closed_window(ApiError(1, "lỗi khác")) is None


def test_status_reports_the_closure_instead_of_failing(capsys, monkeypatch):
    class Closed:
        def post(self, *_a, **_k):
            raise CLOSED

    monkeypatch.setattr(cli, "_client", lambda *_a, **_k: Closed())
    assert cli.cmd_status(cli.argparse.Namespace(user="acc", password="", config="x")) == 0
    out = capsys.readouterr().out
    assert "đang đóng" in out and "mở lại" in out


def test_run_waits_for_the_reopening_instead_of_quitting(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(cli.notify, "send", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(cli.cfgmod, "load", lambda *_a, **_k: {
        **config.DEFAULTS, "accounts": [{"user": "acc"}], "rounds": 1,
        "active_hours": [0, 24], "wait_for_round": True,
    })
    slept: list[float] = []

    class Closed:
        def balance(self):
            return {}

        def post(self, *_a, **_k):
            raise CLOSED

    monkeypatch.setattr(cli, "_client", lambda *_a, **_k: Closed())
    args = cli.argparse.Namespace(no_sleep=False, turbo=True, dry_run=False, pace=None,
                                  between=None, delay=None, rounds=1, jobs=1, only=None,
                                  user=None, password=None, config="x", with_speaking=False)
    assert cli.cmd_run(args) == 0
    assert slept and slept[0] >= 15          # cửa sổ đã qua: chờ ngắn rồi làm


# ------------------------------------------------------------- xếp hạng đáp án
def _shape(text, num=0):
    return {"type": games.T_SHAPE, "Point": 10, "numTChar": num, "ans": [],
            "content": {"content": text}, "Description": {"content": ""}}


def test_comparative_context_prefers_comparatives_over_participle():
    values = games.shape_candidates(_shape("The air is ______ in the mountains than here."), [])
    assert values[0].endswith("er") or values[0].endswith("est")


def test_a_real_word_beats_a_made_up_inflection():
    values = games.shape_candidates(_shape("He ______ his homework yesterday."), [])
    assert "noter" not in values[:5]


def test_adverb_slot_ranks_ly_words_first():
    values = games.shape_candidates(_shape("She speaks English very ______."), [])
    assert values[0].endswith("ly")


def test_participle_slot_accepts_ed_forms():
    values = games.shape_candidates(_shape("They have ______ the whole house."), [])
    assert values[0].endswith("ed")


def test_only_can_target_an_optional_speaking_exercise(tmp_path, monkeypatch):
    played = []

    class FakeClient:
        def post(self, path, body=None, **_kw):
            if path == games.ROUND_PATH:
                return {"roundInfoRes": {"lstgames": [
                    _game(1, 2), {"idexercise": 9, "gameId": 36, "Status": 0, "isRank": False}],
                    "bilInfo": {"current_round": 1, "sysRound": 15}}}
            return {}

        def balance(self):
            return {}

    monkeypatch.setattr(cli, "_client", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr(games, "solve_round",
                        lambda *a, **k: played.append(k.get("only")) or [])
    monkeypatch.setattr(games, "list_exercises",
                        lambda client: client.post(games.ROUND_PATH)["roundInfoRes"]["lstgames"])
    args = cli.argparse.Namespace(no_sleep=False, turbo=True, dry_run=False, pace=None,
                                  between=None, delay=None, rounds=1, jobs=1, only=9,
                                  user=None, password=None, config="x", with_speaking=False)
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(cli.cfgmod, "load", lambda *_a, **_k: {
        **config.DEFAULTS, "accounts": [{"user": "acc"}], "active_hours": [0, 24]})
    assert cli.cmd_run(args) == 0
    assert played == [9]


def test_a_played_but_unconfirmed_round_is_written_back(capsys, tmp_path, monkeypatch):
    """Drop-out giữa vòng rồi chạy lại: bài đã nộp hết thì phải Ghi lại kết quả."""
    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(cli.notify, "send", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.cfgmod, "load", lambda *_a, **_k: {
        **config.DEFAULTS, "accounts": [{"user": "acc"}], "rounds": 1, "active_hours": [0, 24],
    })
    confirmed = []
    state = {"round": 1}

    class FakeClient:
        def post(self, path, body=None, **_kw):
            if path == games.ROUND_PATH:
                return {"roundInfoRes": {"lstgames": [_game(5, 2), _game(6, 2)],
                                         "bilInfo": {"current_round": state["round"],
                                                     "sysRound": 9}}}
            confirmed.append(path)
            state["round"] += 1
            return {}

        def schedule(self):
            return []

        def balance(self):
            return {}

    monkeypatch.setattr(cli, "_client", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr(games, "solve_round", lambda *_a, **_k: [])
    args = cli.argparse.Namespace(no_sleep=False, turbo=True, dry_run=False, pace=None,
                                  between=None, delay=None, rounds=1, jobs=1, only=None,
                                  user=None, password=None, config="x", with_speaking=False)
    assert cli.cmd_run(args) == 0
    assert confirmed == [games.ROUND_PATH.replace("getlistGameAndResultMax", "comfirmresult")]
    assert "đã ghi kết quả vòng đang chờ" in capsys.readouterr().out


def test_parallel_writers_keep_the_report_file_valid(tmp_path, monkeypatch):
    import threading as _t

    monkeypatch.setattr(report, "REPORT", str(tmp_path / "report.json"))

    def worker(index: int) -> None:
        for round_index in range(10):
            report.add_exercise(f"acc-{index}", {"idexercise": round_index, "gameId": 1},
                                {"point": 100, "examScore": 100, "isPass": True}, 1)

    threads = [_t.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert len(data["runs"]) == 60
    assert sum(len(run["exercises"]) for run in data["runs"]) == 60


# ------------------------------------------------------- lớp chống bị vá
def test_service_version_can_be_moved_without_touching_code(monkeypatch):
    from ioeauto import api

    monkeypatch.setattr(api, "SERVICE", "/ioe-service/v3")
    assert api.service_path("/ioe-service/v3/TuLuyen/getlistGameAndResultMax").endswith("v3/TuLuyen/getlistGameAndResultMax")
    api.set_service("/ioe-service/v4")
    assert api.service_path("/ioe-service/v3/TuLuyen/getlistGameAndResultMax") == \
        "/ioe-service/v4/TuLuyen/getlistGameAndResultMax"
    api.set_service("")
    assert api.SERVICE == "/ioe-service/v3"


def test_game_root_follows_the_api_host(monkeypatch):
    from ioeauto import api

    monkeypatch.setattr(api, "API_BASE", "https://api-moi.go.vn")
    monkeypatch.setattr(api, "GAME_ROOT", "")
    assert api.game_root() == "https://api-moi.go.vn/ioe-service/v2/game/"
    monkeypatch.setattr(api, "GAME_ROOT", "https://khac/game/")
    assert api.game_root() == "https://khac/game/"


def test_endpoints_file_is_applied_and_overridable(tmp_path, monkeypatch):
    from ioeauto import api, discovery

    monkeypatch.setattr(discovery, "path", lambda: str(tmp_path / "endpoints.json"))
    discovery.save({"ioe_service": "/ioe-service/v4", "api": "https://api-edu.go.vn"})
    config.apply_endpoints({})
    assert api.SERVICE == "/ioe-service/v4"
    config.apply_endpoints({"endpoints": {"ioe_service": "/ioe-service/v5"}})
    assert api.SERVICE == "/ioe-service/v5"
    api.set_service("")


def test_schema_drift_points_at_the_changed_fields(tmp_path, monkeypatch):
    from ioeauto import paths, schema

    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    payload = {"code": 0, "data": {"roundInfoRes": {"lstgames": [{"idexercise": 1}]}}}
    assert schema.record("round", payload) == []
    same = {"code": 0, "data": {"roundInfoRes": {"lstgames": [{"idexercise": 2}]}}}
    assert schema.drift("round", same) == []
    moved = {"code": 0, "data": {"roundInfo": {"games": [{"id": 2, "status": 0}]}}}
    drift = schema.drift("round", moved)
    assert any("mất: data.roundInfoRes" in line for line in drift)
    assert any("lạ: data.roundInfo.games" in line for line in drift)


def test_schema_keeps_the_first_shape_until_it_changes(tmp_path, monkeypatch):
    from ioeauto import paths, schema

    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    schema.record("getinfo", {"a": 1})
    schema.record("getinfo", {"a": 1})          # không đổi thì không ghi lại
    assert schema.names() == ["getinfo"]
    schema.record("getinfo", {"a": 1, "b": 2})
    assert schema.drift("getinfo", {"a": 1, "b": 2}) == []


def test_dead_oracle_stops_costing_requests():
    class FakeSession(games.Session):
        def __init__(self):
            super().__init__(None, 1, 1)
            self.calls = 0

        def check(self, question, answer, position=1):
            self.calls += 1
            raise ApiError(500, "chấm server-side rồi")

    session = FakeSession()
    question = q(ans=[{"orderTrue": 1, "content": "go"}])
    answer, point = session.answer(question, 1, {}, use_oracle=True)
    assert (answer, point) == ("go", 0)
    assert session.oracle_dead is True
    session.answer(question, 2, {}, use_oracle=True)
    assert session.calls == 1               # đã tắt thì không gọi lại


def test_answer_without_oracle_returns_the_best_guess():
    session = games.Session(None, 1, 1)
    question = q(ans=[{"orderTrue": 2, "content": "b"}, {"orderTrue": 1, "content": "a"}])
    answer, point = session.answer(question, 1, {}, use_oracle=False)
    assert answer == "a|b" and point == 0


def test_capabilities_advice_says_what_to_do_per_dead_layer():
    from ioeauto import capabilities

    tips = capabilities.advice({"round_api": False, "answers_in_payload": False, "oracle": False})
    assert any("endpoints --discover" in tip for tip in tips)
    assert any("bank export" in tip for tip in tips)
    assert any("phương án B" in tip for tip in tips)
    assert capabilities.advice({"round_api": True, "answers_in_payload": True, "oracle": True}) == []


def test_capabilities_report_marks_dead_layers():
    from ioeauto import capabilities

    text = capabilities.report({"round_api": True, "oracle": False, "answers_in_payload": None})
    assert "còn" in text and "MẤT" in text and "chưa rõ" in text


def test_stale_capabilities_are_ignored(tmp_path, monkeypatch):
    from ioeauto import capabilities

    monkeypatch.setattr(capabilities, "CACHE", str(tmp_path / "caps.json"))
    capabilities.save({"oracle": False, "at": "2020-01-01 00:00:00"})
    assert capabilities.fresh() == {}
    capabilities.save({"oracle": False})
    assert capabilities.fresh()["oracle"] is False


def test_userscript_embeds_only_scoring_answers(tmp_path):
    from ioeauto import userscript

    bank = {"a": {"answer": "would", "point": 10},
            "b": {"answer": "sai", "point": 0},
            "c": {"answer": "", "point": 10}}
    path = userscript.write(bank, str(tmp_path / "x.user.js"))
    text = open(path, encoding="utf-8").read()
    assert "would" in text and "sai" not in text
    assert "answercheck" in text and "finishgame" in text
    assert userscript.known(bank) == 1


def test_userscript_is_valid_js_when_node_is_available(tmp_path):
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        return
    from ioeauto import userscript

    path = userscript.write({"a": {"answer": "x", "point": 5}}, str(tmp_path / "y.user.js"))
    body = open(path, encoding="utf-8").read()
    body = body[body.index("(function"):]
    script = tmp_path / "check.js"
    script.write_text(body, encoding="utf-8")
    assert subprocess.run([node, "--check", str(script)], capture_output=True).returncode == 0


def test_bank_export_import_merges_without_losing_better_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "BANK", str(tmp_path / "answers.json"))
    cli._save_bank({"k1": {"answer": "cu", "point": 10, "type": 10}})
    source = tmp_path / "in.json"
    source.write_text(json.dumps({"k1": {"answer": "moi", "point": 5, "type": 10},
                                  "k2": {"answer": "them", "point": 10, "type": 10}}), encoding="utf-8")
    args = type("A", (), {"action": "import", "path": str(source)})()
    cli.cmd_bank(args)
    merged = cli._load_bank()
    assert merged["k1"]["answer"] == "cu" and merged["k2"]["answer"] == "them"


# ------------------------------------------------- phương án C (AI nhìn ảnh)
def _png(width: int = 400, height: int = 300) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (250, 250, 250)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_annotate_covers_every_cell_with_unique_labels():
    from PIL import Image
    import io

    from ioeauto import vision

    png, cells = vision.annotate(_png(), 4, 3)
    image = Image.open(io.BytesIO(png))
    assert image.size == (400, 300)
    assert len(cells) == 12 and len(set(cells)) == 12
    assert cells["A1"][0] == 0 and cells["D3"][2] == 399
    assert any(pixel != (250, 250, 250) for pixel in image.getdata())


def test_annotate_crop_shifts_cells_to_page_coordinates():
    from ioeauto import vision

    _png_out, cells = vision.annotate(_png(), 2, 2, crop=(100, 50, 140, 90))
    assert cells["A1"][:2] == (100, 50)
    assert cells["B2"][2:] == (139, 89)


def test_parse_accepts_json_in_prose_and_clamps_action():
    from ioeauto import vision

    assert vision.parse('ok {"action":"click","cell":" c4 ","conf":0.9}')["cell"] == "C4"
    assert vision.parse('{"action":"lướt","cell":"ZZ"}')["action"] == "click"
    assert vision.parse('{"action":"lướt","cell":"ZZ"}')["cell"] == ""
    assert vision.parse('{"action":"type","text":"would like"}')["text"] == "would like"


def test_parse_rejects_text_without_json():
    from ioeauto import vision

    try:
        vision.parse("xin chào")
    except vision.VisionError as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("phải lỗi khi AI trả chữ")


def test_provider_reads_environment(monkeypatch):
    from ioeauto import vision

    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    for name in ("EZIOE_AI_KEY", "EZIOE_AI_PROVIDER", "EZIOE_AI_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    name, key, model, base = vision.provider({"vision": {}})
    assert (name, key) == ("gemini", "g-key")
    assert "gemini" in model and "googleapis" in base


class FakeBrowser:
    def __init__(self, answers, width=400, height=300):
        self.answers = list(answers)
        self.clicks: list[tuple[float, float]] = []
        self.typed: list[str] = []
        self.urls = ["https://ioe.vn/start"]
        self.width, self.height = width, height

    def screenshot(self, clip=None, scale=1.0):
        return _png(self.width, self.height)

    def size(self):
        return self.width, self.height

    def click(self, x, y):
        self.clicks.append((x, y))

    def insert_text(self, text):
        self.typed.append(text)

    def goto(self, url, wait=0):
        self.urls.append(url)

    def url_now(self):
        return self.urls[-1]

    def eval(self, _script):
        return None


def test_pick_zooms_into_the_chosen_cell(monkeypatch):
    from ioeauto import vision

    replies = [{"action": "click", "cell": "B2", "text": "", "conf": 0.9},
               {"action": "click", "cell": "A1", "text": "", "conf": 0.8}]
    monkeypatch.setattr(vision, "ask", lambda *_a, **_k: replies.pop(0))
    browser = FakeBrowser([])
    result = vision.pick(browser, {"vision": {}}, "chọn đáp án")
    assert result["x"] == 54.5 and result["y"] == 54.5        # tâm ô A1 nằm trong ô B2
    assert browser.clicks == []
    assert replies == []


def test_step_types_text_and_dry_run_does_not_click(monkeypatch, tmp_path):
    from ioeauto import paths, vision

    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(vision, "ask", lambda *_a, **_k: {"action": "type", "cell": "A1",
                                                          "text": "would like", "conf": 0.7})
    browser = FakeBrowser([])
    vision.step(browser, {"vision": {}}, "viết đáp án", dry=True)
    assert browser.clicks == [] and browser.typed == []
    vision.step(browser, {"vision": {}}, "viết đáp án")
    assert browser.typed == ["would like"] and len(browser.clicks) == 1
    assert list(tmp_path.joinpath("vision").glob("*.png"))


def test_solve_exercise_stops_on_done(monkeypatch, tmp_path):
    from ioeauto import paths, vision

    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    replies = [{"action": "click", "cell": "A1", "conf": 0.9},   # lượt thô
               {"action": "click", "cell": "A1", "conf": 0.9},   # lượt phóng
               {"action": "done", "conf": 0.9}]
    monkeypatch.setattr(vision, "ask", lambda *_a, **_k: replies.pop(0))
    browser = FakeBrowser([])
    result = vision.solve_exercise(browser, {"vision": {}}, "https://ioe.vn/x", game_id=3,
                                   on_log=lambda _l: None)
    assert result["done"] is True and result["steps"] == 2
    assert browser.url_now() == "https://ioe.vn/x"


def test_audio_urls_are_read_from_the_page(monkeypatch):
    from ioeauto import vision

    class B(FakeBrowser):
        def eval(self, _script):
            return '["https://x/a.mp3"]'

    assert vision.audio_urls(B([])) == ["https://x/a.mp3"]

    class Broken(FakeBrowser):
        def eval(self, _script):
            return "hỏng"

    assert vision.audio_urls(Broken([])) == []


def test_heard_text_goes_into_the_typing_hint(monkeypatch, tmp_path):
    from ioeauto import asr, paths, vision

    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(vision, "audio_urls", lambda _b: ["https://x/a.mp3"])
    monkeypatch.setattr(asr, "available", lambda: True)
    monkeypatch.setattr(asr, "transcribe", lambda url, timeout=60: "the book is on the table")
    seen = {}

    def fake_ask(_png, prompt, _cfg):
        seen["prompt"] = prompt
        return {"action": "done", "cell": "", "text": "", "conf": 1.0}

    monkeypatch.setattr(vision, "ask", fake_ask)
    vision.solve_exercise(FakeBrowser([]), {"vision": {}}, "https://ioe.vn/x", game_id=1,
                          on_log=lambda _l: None)
    assert "the book is on the table" in seen["prompt"]


def test_logged_in_treats_transport_errors_as_false(monkeypatch):
    from ioeauto import cdp, vision

    class B:
        def __init__(self, value, url="https://ioe.vn/hoc-sinh/tu-luyen"):
            self.value = value
            self.url = url

        def goto(self, _url, wait=0):
            pass

        def url_now(self):
            if isinstance(self.value, Exception):
                raise self.value
            return self.url

        def eval(self, _script):
            return None

    assert vision.logged_in(B(0)) is True
    assert vision.logged_in(B(0, "https://edu.go.vn/user/dang-nhap")) is False
    assert vision.logged_in(B(cdp.CdpError("mất kết nối"))) is False


def test_finish_round_clicks_and_confirms(monkeypatch, tmp_path):
    from ioeauto import paths, vision

    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(vision, "ask", lambda *_a, **_k: {"action": "click", "cell": "C2",
                                                          "text": "", "conf": 0.9})
    clicked_readonly = {"n": 0}

    class B(FakeBrowser):
        def eval(self, script):
            if "Đồng ý" in script:
                clicked_readonly["n"] += 1
                return True
            return None

    browser = B([])
    vision.finish_round(browser, {"vision": {}})
    assert browser.urls[-1] == "https://ioe.vn/hoc-sinh/tu-luyen"
    assert len(browser.clicks) == 1 and clicked_readonly["n"] == 1


def test_finish_round_dry_run_does_not_click(monkeypatch, tmp_path):
    from ioeauto import paths, vision

    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(vision, "ask", lambda *_a, **_k: {"action": "click", "cell": "C2",
                                                          "text": "", "conf": 0.9})
    browser = FakeBrowser([])
    vision.finish_round(browser, {"vision": {}}, dry=True)
    assert browser.clicks == []


# ------------------------------------------------------ tính năng tiện ích
def test_profiles_set_human_like_pacing():
    from ioeauto import cli

    special = cli.apply_profile({}, "careful")
    assert special["pace"] == [180.0, 420.0] and special["turbo"] is False
    fast = cli.apply_profile({}, "turbo")
    assert fast["between"] == [0.0, 3.0] and fast["turbo"] is True
    try:
        cli.apply_profile({}, "khong-co")
    except SystemExit as exc:
        assert "profile lạ" in str(exc)
    else:
        raise AssertionError("phải lỗi khi profile sai")


def test_pick_skips_browser_internal_tabs():
    from ioeauto import cdp

    browser = cdp.Browser.__new__(cdp.Browser)
    browser.url = "https://ioe.vn/hoc-sinh/tu-luyen"
    pages = [{"url": "edge://sync-confirmation-dialog/"},
             {"url": "https://edu.go.vn/user/dang-nhap", "id": "a"},
             {"url": "https://ioe.vn/hoc-sinh/tu-luyen", "id": "b"}]
    assert browser._pick(pages)["id"] == "b"
    assert browser._pick([{"url": "edge://x"}]) is None


def test_profile_pacing_is_used_by_run(monkeypatch, tmp_path):
    from ioeauto import cli, config as cfgmod

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"accounts": [{"user": "u", "password": "p"}],
                                "pace": [1, 2]}), encoding="utf-8")
    captured = {}

    monkeypatch.setattr(cli, "_run_api", lambda args, config: captured.update(config) or 0)
    args = type("A", (), {"config": str(path), "no_sleep": False, "turbo": False,
                          "dry_run": False, "no_oracle": False, "with_speaking": False,
                          "pace": None, "between": None, "delay": None, "rounds": None,
                          "jobs": None, "profile": "careful", "fallback": None,
                          "force_ai": False, "only": None, "user": None, "password": None})()
    cli.cmd_run(args)
    assert captured["pace"] == [180.0, 420.0]
    assert cfgmod  # config module vẫn dùng được


def test_bank_prune_drops_zero_point_entries(tmp_path, monkeypatch):
    from ioeauto import cli

    monkeypatch.setattr(cli, "BANK", str(tmp_path / "answers.json"))
    cli._save_bank({"a": {"answer": "tot", "point": 10},
                    "b": {"answer": "sai", "point": 0},
                    "c": {"answer": "", "point": 9}})
    cli.cmd_bank(type("A", (), {"action": "prune", "path": None, "limit": 5})())
    assert set(cli._load_bank()) == {"a"}


def test_bank_show_finds_by_content_and_answer(tmp_path, monkeypatch, capsys):
    from ioeauto import cli

    monkeypatch.setattr(cli, "BANK", str(tmp_path / "answers.json"))
    cli._save_bank({"a": {"answer": "would like", "point": 10, "content": "The man ...",
                          "type": 10}})
    cli.cmd_bank(type("A", (), {"action": "show", "path": "would", "limit": 5})())
    out = capsys.readouterr().out
    assert "would like" in out and "1 kết quả" in out


def test_weak_lists_low_scores_and_hot_games(monkeypatch, capsys):
    from ioeauto import cli, report

    monkeypatch.setattr(report, "weak_spots", lambda user="", limit=20: [
        {"user": "u1", "idexercise": 7, "gameId": 58, "mark": 0, "max": 100},
        {"user": "u1", "idexercise": 8, "gameId": 58, "mark": 60, "max": 100}])
    cli.cmd_weak(type("A", (), {"user": "", "limit": 20})())
    out = capsys.readouterr().out
    assert "0/100" in out and "game 58: 2 bài" in out


def test_backup_and_restore_roundtrip(tmp_path, monkeypatch):
    from ioeauto import cli

    monkeypatch.setattr(cli, "DATA", str(tmp_path))
    monkeypatch.setattr(cli, "BANK", str(tmp_path / "answers.json"))
    cli._save_bank({"a": {"answer": "tot", "point": 10}})
    (tmp_path / "config.json").write_text('{"accounts": []}', encoding="utf-8")
    archive = tmp_path / "b.zip"
    cli.cmd_backup(type("A", (), {"out": str(archive), "keep": 3})())

    cli._save_bank({"a": {"answer": "kem", "point": 1}, "b": {"answer": "moi", "point": 7}})
    cli.cmd_restore(type("A", (), {"path": str(archive), "replace": False})())
    bank = cli._load_bank()
    assert bank["a"]["answer"] == "tot" and bank["b"]["answer"] == "moi"

    cli.cmd_restore(type("A", (), {"path": str(archive), "replace": True})())
    assert cli._load_bank() == {"a": {"answer": "tot", "point": 10}}


def test_watch_once_reports_waiting_round(monkeypatch, tmp_path, capsys):
    from ioeauto import cli

    monkeypatch.setattr(cli, "_client", lambda user, password="", prompt=True: object())
    monkeypatch.setattr(cli.games, "pending_exercises", lambda client: [])
    monkeypatch.setattr(cli.games, "next_round_start",
                        lambda client: __import__("time").time() + 7200)
    monkeypatch.setattr(cli.cfgmod, "apply_endpoints", lambda config: None)
    monkeypatch.setattr(cli.notify, "configure", lambda cfg: None)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"accounts": [{"user": "u", "password": ""}]}), encoding="utf-8")
    args = type("A", (), {"config": str(path), "poll": 15, "once": True, "user": None,
                          "password": None})()
    assert cli.cmd_watch(args) == 0
    assert "chờ vòng mới" in capsys.readouterr().out


# --------------------------------------------------- kế hoạch theo hạn chót
def test_parse_day_understands_short_and_long_dates():
    import time as _t

    from ioeauto import planner

    short = planner.parse_day("02/03")
    assert _t.localtime(short).tm_mday == 2 and _t.localtime(short).tm_mon == 3
    full = planner.parse_day("2027-03-02")
    assert _t.localtime(full).tm_year == 2027
    try:
        planner.parse_day("hôm qua")
    except ValueError as exc:
        assert "DD/MM" in str(exc)
    else:
        raise AssertionError("phải lỗi khi ngày sai")


def test_outline_counts_future_round_openings():
    from ioeauto import planner

    now = 1_800_000_000.0
    far = now + 70 * planner.DAY            # 10 tuần nữa
    plan = planner.outline(current=9, target=25, deadline=far, opened=15, now=now)
    assert plan["blocked"] is False          # 15 + 10 tuần = 25 >= 25
    assert plan["left"] == 16 and plan["per_day"] >= 1
    near = now + 14 * planner.DAY            # 2 tuần nữa
    late = planner.outline(current=9, target=25, deadline=near, opened=15, now=now)
    assert late["blocked"] is True and late["reachable"] < late["left"]


def test_outline_never_exceeds_max_round():
    from ioeauto import planner

    now = 1_800_000_000.0
    plan = planner.outline(current=30, target=35, deadline=now + 400 * planner.DAY,
                           opened=35, now=now, max_round=35)
    assert plan["reachable"] == 5 and plan["blocked"] is False


def test_verdict_and_hours_follow_the_plan():
    from ioeauto import planner

    assert planner.verdict({"left": 0, "blocked": False, "rate": 0}, 25)[0] == "ok"
    assert planner.verdict({"left": 5, "blocked": True, "rate": 1, "reachable": 0,
                            "available": 10}, 25)[0] == "bad"
    assert planner.verdict({"left": 9, "blocked": False, "rate": 2.0}, 25)[0] == "warn"
    assert planner.suggest_hours({"per_day": 3}) == [6, 23]
    assert planner.suggest_hours({"per_day": 1}) == [9, 21]


def test_plan_apply_writes_pace_and_hours(tmp_path, monkeypatch, capsys):
    from ioeauto import cli, config as cfgmod

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"accounts": [{"user": "u", "password": "p"}],
                                "max_rounds_per_day": 2}), encoding="utf-8")
    monkeypatch.setattr(cli, "_client", lambda *a, **k: object())
    monkeypatch.setattr(cli.cfgmod, "apply_endpoints", lambda config: None)
    monkeypatch.setattr(cli.games, "round_info",
                        lambda client: {"current_round": 9, "sysRound": 15})
    args = type("A", (), {"config": str(path), "target": 25, "by": "02/03", "milestone": None,
                          "apply": True, "schedule": False, "at": None, "every": None,
                          "user": None, "password": None})()
    assert cli.cmd_plan(args) == 0
    saved = cfgmod.load(str(path))
    assert saved["max_rounds_per_day"] >= 1
    assert saved["active_hours"][0] < saved["active_hours"][1]
    assert "vòng 9/15" in capsys.readouterr().out


# ------------------------------------------------------- bài nói (hợp đồng thật)
def test_speak_answer_runs_the_upload_contract(monkeypatch):
    from ioeauto import games, tts

    calls = []

    def fake_http(url, data=None, headers=None, timeout=60):
        calls.append((url, data, headers))
        if url == games.FTECH_STREAM:
            return 200, json.dumps({"code": 0, "data": {"request_id": "rid-1"}}).encode()
        if url == games.FTECH_STOP:
            return 200, json.dumps({"code": 0, "data": {"score_data": {"accuracy_score": 88}}}).encode()
        return 200, b'{"is_silence": false}'

    monkeypatch.setattr(games, "_http", fake_http)
    monkeypatch.setattr(tts, "speak", lambda text, voice="": b"RIFFwav")
    question = q(type=games.T_SPEAK, content={"content": "Absorb"})
    request_id, score = games.speak_answer(question, "", "tok-1")
    assert (request_id, score) == ("rid-1", 88)
    urls = [call[0] for call in calls]
    assert urls == [games.FTECH_STREAM, games.FTECH_VAD, games.FTECH_STOP]
    assert b'name="reference_text"' in calls[2][1] and b"Absorb" in calls[2][1]
    assert b"rid-1.wav" in calls[2][1] and b'name="extra_data"' in calls[2][1]
    assert calls[0][2]["Authorization"] == "Bearer tok-1"


def test_speak_answer_survives_service_errors(monkeypatch):
    from ioeauto import games, tts

    monkeypatch.setattr(games, "_http", lambda *a, **k: (500, "hỏng".encode()))
    monkeypatch.setattr(tts, "speak", lambda text, voice="": b"RIFF")
    question = q(type=games.T_SPEAK, content={"content": "Absorb"})
    assert games.speak_answer(question, "", "t") == ("", 0)


def test_speak_answer_skips_empty_text(monkeypatch):
    from ioeauto import games, tts

    monkeypatch.setattr(tts, "speak", lambda text, voice="": b"RIFF")
    question = q(type=games.T_SPEAK, content={"content": "   "})
    assert games.speak_answer(question) == ("", 0)


def test_sentence_pronunciation_counts_as_speaking():
    from ioeauto import games

    assert games.T_SPEAK_TEXT == 13
    assert set(games.SPEAK_TYPES) == {12, 13}


# ------------------------------------------------------------- giọng nói neural
def test_neural_voice_detection():
    from ioeauto import tts

    assert tts.NEURAL_RE.match("en-US-AriaNeural")
    assert not tts.NEURAL_RE.match("Microsoft Zira Desktop")
    assert not tts.NEURAL_RE.match("")


def test_neural_voice_is_used_and_cached(tmp_path, monkeypatch):
    from ioeauto import tts

    monkeypatch.setattr(tts, "CACHE", str(tmp_path))
    calls = []

    def fake_edge(text, voice):
        calls.append((text, voice))
        return b"RIFF" + b"\x00" * 40

    monkeypatch.setattr(tts, "_edge", fake_edge)
    assert tts.speak("Absorb", "en-US-AriaNeural") .startswith(b"RIFF")
    assert tts.speak("Absorb", "en-US-AriaNeural") .startswith(b"RIFF")
    assert len(calls) == 1                      # lần hai lấy từ cache
    assert calls[0] == ("Absorb", "en-US-AriaNeural")


def test_neural_failure_falls_back_to_sapi(tmp_path, monkeypatch):
    from ioeauto import tts

    monkeypatch.setattr(tts, "CACHE", str(tmp_path))
    monkeypatch.setattr(tts, "_edge", lambda text, voice: (_ for _ in ()).throw(OSError("mạng")))
    monkeypatch.setattr(tts, "_powershell", lambda text, path, voice="": _write(path))
    monkeypatch.setattr(tts, "_espeak", lambda text, path: False)
    assert tts.speak("Absorb", "en-US-AriaNeural").startswith(b"RIFF")


def _write(path):
    with open(path, "wb") as fh:
        fh.write(b"RIFF-sapi")
    return True
