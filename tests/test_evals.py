import json

from review_radar.demo import fixture_reviews, run_agent
from review_radar.evals import runner
from review_radar.evals.baseline import predict
from review_radar.evals.record import export_run
from review_radar.evals.scorers import (
    cluster_scores,
    extraction_accuracy,
    issue_template_completeness,
    reply_guardrail_rate,
    trajectory_rules,
)
from review_radar.service import create_app, import_reviews

from .conftest import make_engine


def test_extraction_accuracy_per_field():
    labels = {
        "a": {"category": "bug", "sentiment": "negative", "has_device_info": True},
        "b": {"category": "praise", "sentiment": "positive", "has_device_info": False},
        "c": {"category": "billing", "sentiment": "mixed", "has_device_info": False},
    }
    preds = {
        "a": {"category": "bug", "sentiment": "mixed", "has_device_info": True},
        "b": {"category": "praise", "sentiment": "positive", "has_device_info": False},
        "c": {"category": "request", "sentiment": "positive", "has_device_info": False},
        "zz": {"category": "bug", "sentiment": "negative", "has_device_info": False},
    }
    result = extraction_accuracy(preds, labels)
    assert result["n"] == 3
    assert result["category"] == round(2 / 3, 4) and result["sentiment"] == round(1 / 3, 4)
    assert result["has_device_info"] == 1.0 and result["sentiment_lenient"] == 1.0
    assert result["confusion"]["billing"] == {"request": 1}


def test_cluster_purity_and_completeness():
    gold = {"1": "ads", "2": "ads", "3": "ads", "4": "bug", "5": "bug", "6": "misc"}
    perfect = cluster_scores([["1", "2", "3"], ["4", "5"], ["6"]], gold)
    assert perfect["purity"] == perfect["completeness"] == 1.0 and perfect["coverage"] == 1.0
    merged = cluster_scores([["1", "2", "3", "4", "5"]], gold)
    assert merged["purity"] == 0.6 and merged["completeness"] == 1.0
    split = cluster_scores([["1", "2"], ["4", "5"]], gold)
    assert split["purity"] == 1.0 and split["completeness"] == round((2 / 3 * 3 + 2) / 5, 4)
    assert split["coverage"] == 0.8
    assert cluster_scores([], gold)["purity"] is None


def test_baseline_keywords():
    assert predict("Crash", "It crashes on my iPhone 15", 1) == {
        "category": "bug",
        "sentiment": "negative",
        "has_device_info": True,
    }
    assert predict("Ads", "too many ads", 2)["category"] == "billing"
    assert predict("", "I love it", 5)["sentiment"] == "positive"


def recorded_run(settings):
    engine = make_engine(settings)
    app = create_app(engine, store_kind="ios", store_id="1", name="Demo", github_repo="a/b")
    import_reviews(engine, app, fixture_reviews(40))
    result = run_agent(engine, app, max_reviews=30, fetch=False)
    assert result["status"] == "done"
    return json.loads(json.dumps(export_run(engine, result["run_id"], result["seconds"])))


def test_recorded_run_export_and_trajectory_scorers(settings):
    recorded = recorded_run(settings)
    assert len(recorded["signals"]) == 30
    assert all(not rid.count("-") for rid in recorded["review_ids"])  # store ids, not uuids
    trajectory = trajectory_rules(recorded)
    assert trajectory["passed"], trajectory["violations"]
    assert trajectory["model_steps"] <= 24
    guard = reply_guardrail_rate(recorded)
    assert guard["stored_pass_rate"] in (None, 1.0)
    issues = issue_template_completeness(recorded)
    if issues["issues"]:
        assert issues["completeness"] == 1.0
    test = runner.self_test(recorded)
    assert test["all_rules_fail_on_bad_trajectory"]


def test_runner_recorded_mode_renders(tmp_path, monkeypatch, settings):
    recorded = recorded_run(settings)
    fixtures = tmp_path / "evals" / "fixtures"
    fixtures.mkdir(parents=True)
    for name in ("reviews.json", "labels.json"):
        (fixtures / name).write_text((runner.FIXTURES / name).read_text("utf-8"), "utf-8")
    (fixtures / "recorded_run.json").write_text(json.dumps(recorded), "utf-8")
    labels = json.loads((fixtures / "labels.json").read_text("utf-8"))
    vectors = {
        rid: [1.0 if i == hash(label) % 8 else 0.0 for i in range(8)]
        for rid, label in labels["clusters"].items()
    }
    (fixtures / "embeddings.json").write_text(
        json.dumps({"model": "fake", "vectors": vectors}), "utf-8"
    )
    monkeypatch.setattr(runner, "FIXTURES", fixtures)
    report = runner.run_evals()
    assert report["extraction"]["baseline_keywords"]["n"] == 200
    assert report["extraction"]["recorded_run"]["n"] == 30
    assert report["trajectory"]["passed"]
    assert report["clustering"]["sweep"][0]["within_kind"]["purity"] is not None
    text = runner.render(report)
    for heading in ("## Extraction accuracy", "## Clustering", "## Trajectory rules", "## Cost"):
        assert heading in text
