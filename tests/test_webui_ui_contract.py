# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""WebUI contract tests.

The webui has no JS test framework (no vitest — adding one would be a new
dependency), so these tests verify the PRODUCTION source files themselves:
exact UI strings, the login_required → accounts-page navigation, and the
existence/wiring of the exported pure functions that encode the
current-job recovery race rules. They read the real files — nothing is
copied or re-implemented here.
"""

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "webui" / "src"
_LOCALES = _ROOT / "i18n" / "locales"


def _zh(ns_key: str) -> str:
    """取 zh-CN 里 'namespace.key' 的文案。

    搜索页的状态/提示文案已改走 i18n（`{t("search.xxx")}`），所以断言从
    "源码里有这串中文" 变成 "源码接了这个键，且这个键在 zh-CN 里就是这句话"——
    意图不变（界面确实显示这句话），但不再把文案钉死在组件里。
    """
    namespace, _, key = ns_key.partition(".")
    data = json.loads((_LOCALES / "zh-CN" / "common.json").read_text(encoding="utf-8"))
    return data[namespace][key]

_PLATFORM_STATUS = (_ROOT / "components" / "search" / "PlatformStatus.tsx").read_text(encoding="utf-8")
_STATUS_DISPLAY = (_ROOT / "lib" / "statusDisplay.ts").read_text(encoding="utf-8")
_TYPES = (_ROOT / "types" / "search.ts").read_text(encoding="utf-8")
_SEARCH_PAGE = (_ROOT / "components" / "search" / "SearchPage.tsx").read_text(encoding="utf-8")
_HOOK = (_ROOT / "hooks" / "useAggregateSearch.ts").read_text(encoding="utf-8")
_EXPERIENCE_HOOK = (_ROOT / "hooks" / "useSearchExperience.ts").read_text(encoding="utf-8")
_ACCOUNTS = (_ROOT / "components" / "accounts" / "AccountsPage.tsx").read_text(encoding="utf-8")
_HELP = (_ROOT / "components" / "help" / "HelpPage.tsx").read_text(encoding="utf-8")
_ACCOUNTS_LIB = (_ROOT / "lib" / "accounts.ts").read_text(encoding="utf-8")
_RESULT_CARD = (_ROOT / "components" / "search" / "ResultCard.tsx").read_text(encoding="utf-8")
_APP = (_ROOT / "App.tsx").read_text(encoding="utf-8")
_AUTOSYNC_HOOK = (_ROOT / "hooks" / "useAutoAccountSync.ts").read_text(encoding="utf-8")
_AUTOSYNC_COMPONENT = (_ROOT / "components" / "accounts" / "AccountAutoSync.tsx").read_text(encoding="utf-8")
_EXTENSION_SYNC = (_ROOT / "lib" / "extensionSync.ts").read_text(encoding="utf-8")
_ACCOUNT_GATE = (_ROOT / "lib" / "accountGate.ts").read_text(encoding="utf-8")
_ACCOUNT_BULK = (_ROOT / "lib" / "accountBulkSync.ts").read_text(encoding="utf-8")
_SCAN_LOGIN = (_ROOT / "lib" / "scanLogin.ts").read_text(encoding="utf-8")


# ── cancelling / cancelled UI text ──────────────────────────────────────

def test_overall_badge_texts():
    """cancelling → 正在取消; cancelled → 搜索已取消; failed → 搜索失败。

    文案已迁到 i18n（zh-CN/common.json 的 search 命名空间），这里同时验
    「组件接了那个键」和「那个键就是这句话」。
    """
    assert 't("search.cancelling")' in _SEARCH_PAGE
    assert _zh("search.cancelling").startswith("正在取消")
    assert 't("search.cancelledNotice")' in _SEARCH_PAGE
    assert "搜索已取消" in _zh("search.cancelledNotice")
    assert 't("search.allFailed")' in _SEARCH_PAGE
    assert _zh("search.allFailed") == "所有平台搜索失败"


def test_platform_status_has_cancelled_case():
    """cancelled 分支与 STATUS_LABELS 回退随 statusLine 迁至 statusDisplay.ts。"""
    assert '"cancelled"' in _STATUS_DISPLAY
    assert 'STATUS_LABELS[status]' in _STATUS_DISPLAY
    assert "statusLine" in _PLATFORM_STATUS  # 组件仍接线生产 statusLine


def test_status_label_cancelled():
    assert 'cancelled: "已取消"' in _TYPES


def test_result_card_renders_optional_snippet():
    assert "result.snippet" in _RESULT_CARD
    assert "result-description" in _RESULT_CARD
    assert "detailsExpanded" in _RESULT_CARD


def test_search_request_cache_bypass_is_wired():
    assert "bypass_cache?: boolean" in _TYPES
    assert "bypass_cache: bypassCache" in _HOOK
    assert "bypassCache = false" in _HOOK
    assert "handleRefresh" in _EXPERIENCE_HOOK
    assert "current.keyword" in _EXPERIENCE_HOOK


def test_platform_status_union_includes_cancelled():
    assert '"cancelled"' in _TYPES


# ── login_required → 账号设置 (no aux login from search page) ───────────

def test_search_page_navigates_to_accounts():
    assert 't("search.goAccounts")' in _SEARCH_PAGE
    assert _zh("search.goAccounts") == "前往账号设置"
    assert "onNavigateAccounts" in _SEARCH_PAGE


def test_search_page_no_longer_starts_login():
    """The search page must not start the aux login itself."""
    assert "handleLogin" not in _SEARCH_PAGE
    assert "useLogin" not in _SEARCH_PAGE


# ── current-job recovery race (pure functions exist & are wired) ────────

def test_race_pure_functions_exported():
    assert "export function shouldApplyRecoveredJob" in _HOOK
    assert "export function shouldClearJobOn404" in _HOOK


def test_race_get_current_job_accepts_abort_signal():
    """getCurrentJob must take an AbortSignal and forward it to axios."""
    assert "signal" in _HOOK
    assert "getCurrentJob" in _HOOK


def test_race_generation_guards_are_wired():
    """startSearch/reset must bump the generation so late responses are
    discarded."""
    assert "generationRef" in _HOOK


def test_race_404_clears_only_matching_job():
    assert "shouldClearJobOn404" in _HOOK


# ── accounts page: security-conscious interactions ──────────────────────

def test_accounts_delete_has_double_confirm():
    assert _ACCOUNTS.count("window.confirm") >= 2


def test_accounts_opens_official_pages_in_current_browser():
    """Login pages open via window.open in the current browser — no
    Playwright import, no backend call for opening login pages."""
    assert "window.open" in _ACCOUNTS
    import_lines = [ln for ln in _ACCOUNTS.splitlines()
                    if ln.strip().startswith("import")]
    for word in ("playwright", "launch_persistent_context"):
        assert word not in "\n".join(import_lines)


def test_accounts_sync_uses_ticket_flow():
    """票据往返协议在 lib/extensionSync（账号页与自动同步共用一份实现）。"""
    assert "sync-ticket" in _EXTENSION_SYNC
    assert "sync-request" in _EXTENSION_SYNC
    assert "request_id" in _EXTENSION_SYNC
    assert "requestPlatformSync" in _ACCOUNTS


def test_accounts_extension_install_instructions_present():
    assert "onNavigateHelp" in _ACCOUNTS
    assert "chrome://extensions" in _HELP
    assert "edge://extensions" in _HELP
    assert "开发者模式" in _HELP
    assert "browser_extension" in _HELP


# ── Round 18: 未登录快速提示 + 打开程序即自动同步 ───────────────────────

def test_login_required_reason_is_surfaced():
    """login_required 也必须显示 error_summary（可操作原因），否则用户只看到
    "需要登录" 却不知道要去账号设置同步。"""
    assert 'info.error_summary || "需要登录"' in _STATUS_DISPLAY


def test_platform_status_exposes_full_status_text():
    """状态文案被截断时用 title 提供完整内容；0 结果也要能带上安全原因。

    `detail` = 常规状态文案，或（empty 且有 error_summary 时）那条安全原因 ——
    两者都进 title，保证截断后仍能看全。
    """
    assert "statusText" in _PLATFORM_STATUS
    assert "title={[detail, freshness]" in _PLATFORM_STATUS
    assert 'info.error_summary' in _PLATFORM_STATUS


# ── 结果页「返回首页」+ 0 结果算失败（2026-09-19）────────────────────────

def test_back_home_button_sits_left_of_search_box_with_brand_fill():
    """返回首页按钮在搜索框左侧（.search-zone 内、SearchBar 之前）且用主题色底。"""
    assert '"btn primary home-back"' in _SEARCH_PAGE
    zone = _SEARCH_PAGE.index('className="search-zone"')
    assert zone < _SEARCH_PAGE.index("<SearchBar", zone), "按钮必须在搜索框之前（左侧）"
    css = (_ROOT / "index.css").read_text(encoding="utf-8")
    assert ".search-zone .home-back" in css
    assert ".search-zone > .search-area" in css


def test_back_home_does_not_clear_results():
    """「返回首页」只切视图：不能顺手清结果/任务。"""
    go_home = _SEARCH_PAGE.split("const goHome = useCallback")[1].split("), [")[0]
    assert "handleResetLocal" not in go_home, "回首页不该清结果（用户明确要求结果不删）"


def test_home_view_hides_per_platform_fetch_button():
    """首页那排平台只用于勾选范围：不显示 ⟳ 单独重搜按钮。"""
    assert "onPlatformFetch={!isHome && displayJobResponse ? handleFetchPlatform : undefined}" in _SEARCH_PAGE


def test_empty_result_counts_as_failure_and_surfaces_reason():
    """没搜到东西也算搜索失败：进顶部提示那条链路，并优先用平台给的安全原因。"""
    assert '"empty"' in _SEARCH_PAGE.split("FAILED_PLATFORM_STATUSES = [")[1].split("]")[0]
    assert 'empty: "search.reasonEmpty"' in _SEARCH_PAGE
    assert "info.error_summary" in _SEARCH_PAGE
    assert _zh("search.reasonEmpty")


def test_webui_i18n_back_home_keys_exist():
    """返回首页 / 查看上次结果 两个键在两种语言里都要有。"""
    for locale in ("zh-CN", "en-US"):
        data = json.loads((_LOCALES / locale / "common.json").read_text(encoding="utf-8"))
        assert data["search"]["backToHome"]
        assert "{{count}}" in data["search"]["viewLastResults"]


def test_app_root_mounts_autosync():
    """自动同步必须挂在应用根部 —— 打开程序就同步，而不是进了账号页才同步。"""
    assert "AccountAutoSync" in _APP
    assert "<AccountAutoSync />" in _APP


def test_autosync_runs_on_open_and_on_resume():
    """触发时机：挂载（打开程序）以及页面回到前台（去浏览器登录完切回来）。"""
    assert "detectExtension" in _AUTOSYNC_HOOK
    assert "decideAutoSync" in _AUTOSYNC_HOOK
    assert "visibilitychange" in _AUTOSYNC_HOOK
    assert '"focus"' in _AUTOSYNC_HOOK


def test_extension_probe_retries_until_pong():
    """扩展探测必须重复 ping，不能只发一次。

    content script 以 document_idle 注入，可能晚于 React 挂载和第一次 ping；
    单次 ping 会石沉大海，把已安装的扩展误判成"未安装"，自动同步就永远不启动
    （用户实测：重启程序后没有任何同步动作）。
    """
    assert "EXTENSION_PROBE_INTERVAL_MS" in _EXTENSION_SYNC
    assert "setInterval" in _EXTENSION_SYNC
    assert "EXTENSION_PROBE_TOTAL_MS" in _EXTENSION_SYNC


def test_autosync_reprobes_extension_on_resume():
    """回到前台时若扩展尚未连接，必须重新探测（可能刚启用/重新加载扩展）。"""
    assert "detectExtension({ totalMs: EXTENSION_PROBE_TOTAL_MS })" in _AUTOSYNC_HOOK


def test_autosync_result_is_visible():
    """自动同步的结果必须留在页面上（不能只在运行中显示），否则用户会以为
    什么都没发生。"""
    assert "NOTE_VISIBLE_MS" in _AUTOSYNC_HOOK
    assert "!running && !note" in _AUTOSYNC_COMPONENT


def test_autosync_is_cooldown_bounded():
    """冷却与 guard 必须存在：不能因为账号轮询反复重启浏览器。"""
    assert "AUTO_SYNC_COOLDOWN_MS" in _AUTOSYNC_HOOK
    assert "createBulkSyncGuard" in _AUTOSYNC_HOOK
    # 首次尝试之后不再由账号轮询驱动
    assert "lastAttemptRef.current !== null" in _AUTOSYNC_HOOK


def test_autosync_stays_quiet_when_nothing_was_synced():
    """浏览器里没有可同步的会话时不打扰（否则每次打开程序都弹失败提示）。"""
    assert "shouldAnnounceAutoSync" in _AUTOSYNC_HOOK
    assert "shouldAnnounceAutoSync" in (
        _ROOT / "lib" / "accountBulkSync.ts").read_text(encoding="utf-8")


def test_accounts_page_no_longer_autosyncs():
    """账号页只保留手动一键同步；自动同步由根部负责，避免两处各跑一套队列。"""
    assert "decideAutoSync" not in _ACCOUNTS
    assert "runSyncQueue" in _ACCOUNTS


def test_autosync_reverifies_without_extension():
    """方案 A：没装扩展时，重启后也要把已有登录状态复核回来。

    后端"已验证"只存在内存里，重启后 profile 还在但状态退化成
    "已导入，未确认登录"（accounts.py 的 _state_of）。装了扩展时自动同步会
    顺带验回来；没装扩展（扫码登录主路径）时这里是唯一补救路径：
    对"本地有 profile 但未确认"的平台直接调 verify 端点。
    """
    assert "decideStartupVerify" in _AUTOSYNC_HOOK
    assert "requestPlatformVerify" in _AUTOSYNC_HOOK
    assert "shouldAnnounceStartupVerify" in _AUTOSYNC_HOOK
    assert "decideStartupVerify" in _ACCOUNT_BULK
    assert "platformsNeedingVerify" in _ACCOUNT_BULK
    assert "profile_exists" in _ACCOUNT_BULK, "只有本地已有 profile 才值得验证"


def test_autosync_verify_path_is_quiet_on_expected_success():
    """全部确认成功是预期结果（重开程序的正常路径），不许每次开程序都弹提示。"""
    assert "shouldAnnounceStartupVerify" in _ACCOUNT_BULK
    assert "counts.verified < counts.total" in _ACCOUNT_BULK


def test_accounts_page_uses_scan_login_as_primary_path():
    """方案 A：卡片主按钮是扫码登录；扩展同步是可选加速（不删）。"""
    assert "startScanLogin" in _ACCOUNTS
    assert "扫码登录" in _ACCOUNTS
    assert "requestPlatformSync" in _ACCOUNTS, "扩展同步必须保留"
    # 登录任务与搜索互斥：扫码登录也要让搜索闸门看见（见 lib/accountGate.ts）
    assert "markScanLoginActive" in _ACCOUNTS
    assert "extraBusy" in _ACCOUNT_GATE
    assert "isAnyScanLoginActive" in _HOOK
    # 后端登录成功前不得声称成功；任务状态机在 lib/scanLogin.ts
    assert "isScanLoginTerminal" in _SCAN_LOGIN


def test_search_waits_for_account_ops():
    """搜索提交前必须等账号操作结束，否则自动同步会把搜索顶成 409。"""
    assert "waitForAccountOpsIdle" in _HOOK
    assert "waitForAccountOpsIdle" in _ACCOUNT_GATE
    assert '"syncing"' in _ACCOUNT_GATE
    assert '"verifying"' in _ACCOUNT_GATE


def test_account_gate_fails_open():
    """探测不到账号状态时必须放行，绝不把用户的搜索卡住。"""
    assert "catch {" in _ACCOUNT_GATE
    assert "return true; // 读不到状态 → 放行" in _ACCOUNT_GATE


def test_accounts_uses_single_status_label_source():
    """账号状态文案只能有一处枚举：账号页必须复用 lib/accounts.ts，
    不能自带第二套 STATUS_TEXT 映射（历史上同状态出现过两种文案）。"""
    assert "accountCardStatusLabel" in _ACCOUNTS
    assert "STATUS_TEXT" not in _ACCOUNTS
    assert "accountCardStatusLabel" in _ACCOUNTS_LIB
    assert "ACCOUNT_STATUS_LABELS" in _ACCOUNTS_LIB


def test_accounts_bulk_progress_total_is_dynamic():
    """手动同步也可能只同步部分平台，进度分母不能用写死的 4。"""
    assert "/4`" not in _ACCOUNTS
    assert "${bulkCompleted}/${bulkTotal}" in _ACCOUNTS


# ── i18n 键守卫 ─────────────────────────────────────────────────────────

def _flatten(table, prefix=""):
    """把嵌套的 locale 表压成 {"ns.key": "文案"}。"""
    out = {}
    for key, value in table.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, full + "."))
        else:
            out[full] = value
    return out


def _locale_keys(name: str) -> set:
    """某个语言认识的键 = common.json + license.json 的并集。"""
    keys = set()
    for fname in ("common.json", "license.json"):
        path = _LOCALES / name / fname
        if path.is_file():
            keys |= set(_flatten(json.loads(path.read_text(encoding="utf-8"))))
    return keys


# 只认有命名空间的写法（ns.key），避免把 t("普通词") 当成 i18n 键
_T_USED_RE = re.compile(r'\bt\(\s*"([A-Za-z][\w]*(?:\.[\w]+)+)"')


def test_i18n_keys_used_in_source_exist_in_every_locale():
    """组件里 t("ns.key") 用到的键必须在每个语言的 locale 里都存在。

    i18next 拼错键时**不报错**，而是把键名当文案渲染出去，用户会看到
    「search.某个键」这种字符串。所以这条守卫只做一件事：扫源码里的
    t("ns.key") 调用，逐个回查 locale（common 与 license 都算命中）。
    """
    locales = sorted(p.name for p in _LOCALES.iterdir() if p.is_dir())
    assert locales, "缺少 i18n/locales/*"
    known = {name: _locale_keys(name) for name in locales}

    used = set()
    for path in list(_ROOT.rglob("*.ts")) + list(_ROOT.rglob("*.tsx")):
        used |= set(_T_USED_RE.findall(path.read_text(encoding="utf-8")))

    assert used, '源码里没扫到任何 t("ns.key") 调用，守卫失效了？'
    problems = {name: sorted(k for k in used if k not in known[name])
                for name in locales}
    problems = {k: v for k, v in problems.items() if v}
    assert problems == {}, f"这些 t() 键在 locale 里找不到: {problems}"


def test_locales_define_the_same_keys():
    """两种语言的键集合必须一致 —— 少一条就等于该语言会渲染出键名。"""
    names = sorted(p.name for p in _LOCALES.iterdir() if p.is_dir())
    assert len(names) >= 2, "至少要有两种语言才谈得上对齐"
    base = names[0]
    for other in names[1:]:
        only_base = sorted(_locale_keys(base) - _locale_keys(other))
        only_other = sorted(_locale_keys(other) - _locale_keys(base))
        assert not only_base and not only_other, (
            f"{base} 与 {other} 的键不一致: 缺 {only_base} / 多 {only_other}")
