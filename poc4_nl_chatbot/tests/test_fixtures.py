from pathlib import Path

from insightflow_chatbot.services.fixtures import load_fixtures

BENCHMARK_PATH = Path(__file__).parent.parent / "data" / "refusal_benchmark.json"


def test_load_fixtures_parses_the_refusal_benchmark():
    fixtures = load_fixtures(str(BENCHMARK_PATH))
    assert len(fixtures) == 16


def test_refusal_benchmark_has_both_answerable_and_unanswerable_cases():
    fixtures = load_fixtures(str(BENCHMARK_PATH))
    answerable = [f for f in fixtures if f.expected_answerable]
    unanswerable = [f for f in fixtures if not f.expected_answerable]
    assert len(answerable) == 8
    assert len(unanswerable) == 8


def test_every_answerable_fixture_names_an_expected_metric():
    fixtures = load_fixtures(str(BENCHMARK_PATH))
    for fixture in fixtures:
        if fixture.expected_answerable:
            assert fixture.expected_metric is not None
            assert fixture.expected_operation is not None
