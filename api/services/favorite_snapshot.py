"""Public result metadata encoded in the existing JSON column (legacy-compatible)."""

import json

METRIC_NAMES = ("like_count", "view_count", "collect_count", "comment_count", "share_count", "coin_count", "danmaku_count")

# 指标完整度由差到好。合并两份快照时只升不降（见 merge_snapshot）：
# 一次超时 / 只拿到列表字段的同步，不能把以前完整的指标降级成残缺版本。
_STATUS_RANK = {"failed": 0, "unavailable": 1, "pending": 2, "partial": 3, "complete": 4}


def encode_metrics(result: dict) -> str:
    counts = result.get("metrics")
    counts = counts if isinstance(counts, dict) else {}
    metadata = {
        "collection_names": result.get("collection_names", []),
        "metrics_status": result.get("metrics_status"),
        "metrics_updated_at": result.get("metrics_updated_at"),
        "metrics_approximate": result.get("metrics_approximate", []),
    }
    return json.dumps({"counts": counts, "metadata": metadata}, ensure_ascii=False)


def decode_metrics(raw: str) -> dict:
    try:
        payload = json.loads(raw or "{}")
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # Old releases stored only a flat dictionary of counts.
    metadata = payload.get("metadata", {}) if isinstance(payload.get("counts"), dict) else {}
    counts = payload.get("counts", payload)
    metadata = metadata if isinstance(metadata, dict) else {}
    status = metadata.get("metrics_status")
    names = metadata.get("collection_names")
    approximate = metadata.get("metrics_approximate")
    return {
        "metrics": counts,
        "metrics_status": status if status in ("pending", "complete", "partial", "unavailable", "failed") else None,
        "metrics_updated_at": metadata.get("metrics_updated_at") if isinstance(metadata.get("metrics_updated_at"), (float, int)) else None,
        "metrics_approximate": [name for name in approximate if name in METRIC_NAMES] if isinstance(approximate, list) else [],
        "collection_names": [name for name in names if isinstance(name, str)][:20] if isinstance(names, list) else [],
    }


def merge_counts(previous: dict, current: dict) -> dict:
    """逐字段合并两份指标：本次拿到的覆盖旧值，本次没拿到的沿用旧值。

    指标只有在接口真的返回了数字时才会进这个字典（0 是真实值），
    所以「这次缺字段」等于「这次没拿到」，绝不能当成 0 写回去。
    """
    merged = {name: value for name, value in (previous or {}).items() if value is not None}
    for name, value in (current or {}).items():
        if value is not None:
            merged[name] = value
    return merged


def merge_metrics_status(previous, current):
    """完整度只升不降：残缺的本次结果不能改写本机已经存下来的完整结论。"""
    if current is None:
        return previous
    if previous is None:
        return current
    return current if _STATUS_RANK.get(current, -1) >= _STATUS_RANK.get(previous, -1) else previous


def merge_approximate(previous, current, previous_counts, current_counts):
    """沿用旧值的字段保留旧的近似标记，本次新取的字段用本次的标记。"""
    previous_counts, current_counts = previous_counts or {}, current_counts or {}
    kept_from_previous = {name for name in previous_counts if name not in current_counts}
    return sorted({name for name in (current or []) if name in current_counts}
                  | {name for name in (previous or []) if name in kept_from_previous})


def merge_snapshot(previous: dict, current: dict) -> dict:
    """合并两份结果快照的指标部分，返回 metrics / 状态 / 时间 / 近似标记。

    键名与 ``UnifiedSearchResult`` 的字段同名，所以既可以直接喂给
    ``encode_metrics``，也可以交给 ``model_copy(update=...)``。
    """
    previous = previous if isinstance(previous, dict) else {}
    current = current if isinstance(current, dict) else {}
    previous_counts = previous.get("metrics") or {}
    current_counts = current.get("metrics") or {}
    updated_at = current.get("metrics_updated_at")
    return {
        "metrics": merge_counts(previous_counts, current_counts),
        "metrics_status": merge_metrics_status(previous.get("metrics_status"), current.get("metrics_status")),
        "metrics_updated_at": updated_at if updated_at is not None else previous.get("metrics_updated_at"),
        "metrics_approximate": merge_approximate(
            previous.get("metrics_approximate"), current.get("metrics_approximate"),
            previous_counts, current_counts),
    }


def merge_into_result(result: dict, previous_raw) -> dict:
    """把旧快照里已拿到的指标补进本次结果（结果仍可交给 encode_metrics）。

    ``previous_raw`` 是库里那列 metrics JSON；没有旧值时原样返回。
    """
    if not previous_raw or not isinstance(previous_raw, str):
        return result
    return {**result, **merge_snapshot(decode_metrics(previous_raw), result)}
