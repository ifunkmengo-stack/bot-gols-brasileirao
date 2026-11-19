import pytest
from app import parse_goal, format_goal, goal_id, full_name

def fixture(home=2, away=1, own=False, assist=None):
    return {
        "event": "goal",
        "match": {"id": "match01", "home": {"name": "Cruzeiro", "score": str(home)},
                  "away": {"name": "Palmeiras", "score": str(away)}},
        "goal": {"side": "home", "score": f"{home}-{away}",
                 "player": {"name": "Khellven"}, "assist": assist,
                 "is_own_goal": own},
    }

def test_scorer_and_score():
    g = parse_goal(fixture(2, 1))
    assert "GOL DO CRUZEIRO!" in format_goal(g)
    assert "Cruzeiro 2 x 1 Palmeiras" in format_goal(g)

def test_own_goal():
    text = format_goal(parse_goal(fixture(2, 1, own=True)), "Primeiro Sobrenome")
    assert "Khellven (GC)" in text
    assert "🥾" not in text

def test_assistance():
    g = parse_goal(fixture(2, 1, assist={"name": "R. Silva", "id": "1"}))
    assert full_name(g["assist"]) is None
    assert "🥾" not in format_goal(g)
    assert "🥾 Rafael Silva" in format_goal(g, "Rafael Silva")

def test_repeated_event_same_score():
    one = parse_goal(fixture(1, 0))
    two = parse_goal(fixture(1, 0, assist={"name": "Outro Jogador"}))
    assert goal_id(one) == goal_id(two)

def test_each_goal_unique():
    assert goal_id(parse_goal(fixture(1, 0))) != goal_id(parse_goal(fixture(2, 0)))

def test_invalid():
    with pytest.raises(ValueError):
        parse_goal({"event": "yellow_card"})

def test_full_name():
    assert full_name({"first_name": "Gabriel", "last_name": "Varela"}) == "Gabriel Varela"
    assert full_name({"name": "G. Varela"}) is None
