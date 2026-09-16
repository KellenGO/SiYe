"""指标快照合并规则（api/services/favorite_snapshot.py）。

核心不变量：**保存动作不能让已经拿到的指标倒退**。一次超时、限流、或只走到
列表阶段的同步，缺的字段沿用本机已有的值，完整度只升不降；接口明确返回的 0
仍然是真值，可以覆盖旧值。
"""

from api.services.favorite_snapshot import (
    decode_metrics,
    encode_metrics,
    merge_counts,
    merge_into_result,
    merge_metrics_status,
    merge_snapshot,
)


def test_missing_fields_fall_back_to_the_previous_snapshot():
    assert merge_counts({"view_count": 100, "like_count": 5}, {"like_count": 7}) == {
        "view_count": 100, "like_count": 7}


def test_genuine_zero_still_overwrites_the_previous_value():
    """接口明确返回的 0 是真值，不能被旧快照盖住。"""
    assert merge_counts({"like_count": 5}, {"like_count": 0}) == {"like_count": 0}


def test_status_only_improves():
    assert merge_metrics_status("complete", "failed") == "complete"
    assert merge_metrics_status("partial", "complete") == "complete"
    assert merge_metrics_status("failed", "partial") == "partial"
    assert merge_metrics_status(None, "partial") == "partial"
    assert merge_metrics_status("failed", None) == "failed"


def test_approximate_marks_follow_the_value_they_describe():
    previous = {"metrics": {"like_count": 100000}, "metrics_approximate": ["like_count"]}
    current = {"metrics": {"collect_count": 200}, "metrics_approximate": ["collect_count"]}
    merged = merge_snapshot(previous, current)
    # like_count 沿用旧值 → 保留旧的近似标记；collect_count 本次新取 → 用本次标记。
    assert merged["metrics_approximate"] == ["collect_count", "like_count"]


def test_merge_into_result_keeps_collection_names_and_survives_a_round_trip():
    result = {"metrics": {"like_count": 2}, "metrics_status": "failed",
              "collection_names": ["稍后学习"]}
    previous = encode_metrics({"metrics": {"view_count": 9}, "metrics_status": "complete",
                               "metrics_updated_at": 123.0, "collection_names": ["旧收藏夹"]})
    merged = merge_into_result(result, previous)
    assert merged["collection_names"] == ["稍后学习"]  # 收藏夹归属按本次结果走
    assert merged["metrics"] == {"view_count": 9, "like_count": 2}
    decoded = decode_metrics(encode_metrics(merged))
    assert decoded["metrics_status"] == "complete"
    assert decoded["metrics_updated_at"] == 123.0


def test_merge_into_result_without_a_previous_snapshot_is_a_no_op():
    result = {"metrics": {"like_count": 2}}
    assert merge_into_result(result, None) is result
