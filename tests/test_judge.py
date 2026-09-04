import subprocess

from gauntlet.judge import judge_output


def fake_claude(reply_text):
    def fake_run(cmd, **kwargs):
        captured_prompt.append(cmd[cmd.index("-p") + 1])
        return subprocess.CompletedProcess(
            cmd, 0, stdout='{"result": ' + __import__("json").dumps(reply_text) + "}", stderr=""
        )

    return fake_run


captured_prompt = []


def test_judge_parses_score(monkeypatch):
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr(
        "gauntlet.judge.subprocess.run",
        fake_claude('{"score": 8, "reasoning": "mostly correct"}'),
    )
    spec = {"rubric": "Must say Alpha is on track.", "answer_key": "on track"}
    verdict = judge_output("Alpha is on track", spec, "claude-fable-5")
    assert verdict == {"score": 8, "reasoning": "mostly correct"}
    prompt = captured_prompt[-1]
    assert "Must say Alpha is on track." in prompt
    assert "on track" in prompt
    assert "Alpha is on track" in prompt


def test_judge_handles_garbage_reply(monkeypatch):
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.judge.subprocess.run", fake_claude("I refuse to answer in JSON"))
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert verdict["score"] is None
    assert "unparseable" in verdict["reasoning"]


def test_judge_handles_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.judge.subprocess.run", fake_run)
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert verdict["score"] is None
    assert "timed out" in verdict["reasoning"]


def test_judge_rejects_non_integer_score(monkeypatch):
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr(
        "gauntlet.judge.subprocess.run",
        fake_claude('{"score": "8", "reasoning": "mostly correct"}'),
    )
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert verdict["score"] is None


def test_judge_rejects_out_of_range_score(monkeypatch):
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr(
        "gauntlet.judge.subprocess.run",
        fake_claude('{"score": 11, "reasoning": "too high"}'),
    )
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert verdict["score"] is None


def test_judge_rejects_boolean_score(monkeypatch):
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr(
        "gauntlet.judge.subprocess.run",
        fake_claude('{"score": true, "reasoning": "sneaky bool"}'),
    )
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert verdict["score"] is None
