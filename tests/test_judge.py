import json
import subprocess

from gauntlet.judge import judge_output


def fake_claude(reply_text, cost=None):
    def fake_run(cmd, **kwargs):
        captured_prompt.append(cmd[cmd.index("-p") + 1])
        payload = {"result": reply_text}
        if cost is not None:
            payload["total_cost_usd"] = cost
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    return fake_run


def fake_claude_sequence(replies):
    """Returns a fake_run that returns a different reply on each call, cycling
    through `replies` — used to simulate the CLI succeeding on a retry."""
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        captured_prompt.append(cmd[cmd.index("-p") + 1])
        idx = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": replies[idx]}), stderr=""
        )

    return fake_run


captured_prompt = []


def test_judge_parses_score(monkeypatch):
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr(
        "gauntlet.judge.subprocess.run",
        fake_claude('{"score": 8, "reasoning": "mostly correct"}', cost=0.05),
    )
    spec = {"rubric": "Must say Alpha is on track.", "answer_key": "on track"}
    verdict = judge_output("Alpha is on track", spec, "claude-fable-5")
    assert verdict["score"] == 8
    assert verdict["reasoning"] == "mostly correct"
    assert verdict["cost_usd"] == 0.05
    assert verdict["degraded"] is False
    assert "8" in verdict["raw_reply"]
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
    assert verdict["degraded"] is True


def test_judge_retries_exactly_once_on_unparseable_output(monkeypatch):
    # Review finding: retry must fire for "CLI ran, reply unparseable" — this
    # pins the call count so a regression back to "retry on any None score"
    # (which would also retry a timeout) is caught even if it doesn't change
    # the returned score.
    captured_prompt.clear()
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": "not json at all"}), stderr=""
        )

    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.judge.subprocess.run", fake_run)
    judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert call_count["n"] == 2


def test_judge_does_not_retry_on_timeout(monkeypatch):
    # Review finding: retrying a timeout doubles the cost of every judge
    # timeout (2 x timeout_s) for no benefit — a timeout is not a parse failure.
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        raise subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.judge.subprocess.run", fake_run)
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert call_count["n"] == 1
    assert verdict["score"] is None
    assert "timed out" in verdict["reasoning"]


def test_judge_does_not_retry_on_invalid_score(monkeypatch):
    # An out-of-range score is a real (if unusable) reply, not a parse
    # failure — parseable-but-invalid should not cost a second call either.
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": '{"score": 99, "reasoning": "way too high"}'}),
            stderr="",
        )

    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.judge.subprocess.run", fake_run)
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert call_count["n"] == 1
    assert verdict["score"] is None


def test_judge_retries_once_then_succeeds(monkeypatch):
    captured_prompt.clear()
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr(
        "gauntlet.judge.subprocess.run",
        fake_claude_sequence(["not json at all", '{"score": 7, "reasoning": "ok on retry"}']),
    )
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert verdict["score"] == 7
    assert verdict["reasoning"] == "ok on retry"
    assert verdict["degraded"] is False  # recovered on retry, so not degraded
    assert len(captured_prompt) == 2  # confirms the retry actually fired


def test_judge_samples_takes_median_and_sums_cost(monkeypatch):
    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr(
        "gauntlet.judge.subprocess.run",
        fake_claude_sequence(
            [
                '{"score": 4, "reasoning": "a"}',
                '{"score": 8, "reasoning": "b"}',
                '{"score": 6, "reasoning": "c"}',
            ]
        ),
    )
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5", samples=3)
    assert verdict["score"] == 6  # median of 4, 8, 6
    assert verdict["degraded"] is False


def test_judge_handles_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr("gauntlet.judge.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.judge.subprocess.run", fake_run)
    verdict = judge_output("whatever", {"rubric": "r"}, "claude-fable-5")
    assert verdict["score"] is None
    assert "timed out" in verdict["reasoning"]
    assert verdict["degraded"] is True


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
