from terminal_runtime.agent_activity import pane_shows_agent_activity, AGENT_ACTIVITY_MARKERS


def test_detects_planning() -> None:
    assert pane_shows_agent_activity("> Planning...\n  using tools")


def test_detects_thinking() -> None:
    assert pane_shows_agent_activity("Thinking about your request")


def test_detects_braille_spinner() -> None:
    # ⠋ is a Braille spinner char
    assert pane_shows_agent_activity("⠋ working...")


def test_detects_gemini_diamond_marker() -> None:
    assert pane_shows_agent_activity("✦ Generating response")


def test_no_false_positive_on_idle_prompt() -> None:
    assert not pane_shows_agent_activity("> ")


def test_no_false_positive_on_empty() -> None:
    assert not pane_shows_agent_activity("")


def test_markers_is_a_non_empty_iterable() -> None:
    assert len(AGENT_ACTIVITY_MARKERS) >= 5
