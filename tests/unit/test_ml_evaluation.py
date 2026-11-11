import pandas as pd

from app.ml.evaluation import chronological_split, directional_accuracy, regression_metrics, walk_forward_date_splits


def test_metrics_and_directional_accuracy_ignore_zero_actuals():
    m = regression_metrics([0.01, -0.02, 0.0], [0.02, -0.01, 0.5])
    assert abs(m["mae"] - (0.01 + 0.01 + 0.5) / 3) < 1e-12
    assert directional_accuracy([0.01, -0.02, 0.0], [0.02, 0.01, 0.5]) == 0.5
    assert directional_accuracy([0.01, -0.02], [0.0, 0.0]) is None      # zero-return baseline makes no call


def test_walk_forward_is_chronological_and_never_overlaps():
    dates = pd.bdate_range("2020-01-01", periods=400)
    splits = walk_forward_date_splits(dates, n_splits=4, min_train_dates=200)
    prev_end = None
    for train, test in splits:
        assert train.max() < test.min() and len(set(train) & set(test)) == 0
        if prev_end is not None:
            assert test.min() > prev_end
        prev_end = test.max()


def test_chronological_split_order():
    s = chronological_split(pd.bdate_range("2020-01-01", periods=100))
    assert s["train"][1] < s["validation"][0] <= s["validation"][1] < s["test"][0]
