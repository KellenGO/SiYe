import { useEffect, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { PLATFORM_LABELS, PLATFORM_COLORS } from "@/types/search";
import type { PlatformSlug } from "@/types/search";
import type { UsePlatformLimitsResult } from "@/hooks/usePlatformLimits";
import { MAX_PLATFORM_LIMIT, MIN_PLATFORM_LIMIT, PLATFORM_ORDER, parsePlatformLimitInput } from "@/lib/platformLimits";

/**
 * 单个平台的搜索数量设置行：
 * - 减号/加号不越过 1–40；
 * - 可直接编辑数字；输入框暂时为空时不立即变成 1；
 * - blur 或 Enter 校正：小于 1 → 1、大于 40 → 40、小数取整、非法/空 → 恢复上次有效值；
 * - 修改一个平台不影响其他平台。
 */
function LimitRow({
  platform,
  value,
  onChange,
}: {
  platform: PlatformSlug;
  value: number;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const lastValidRef = useRef(value);

  useEffect(() => {
    setDraft(String(value));
    lastValidRef.current = value;
  }, [value]);

  const commit = (raw: string) => {
    const parsed = parsePlatformLimitInput(raw);
    if (parsed === null) {
      // 非法或空值 → 恢复该平台上一次有效值
      setDraft(String(lastValidRef.current));
      return;
    }
    lastValidRef.current = parsed;
    setDraft(String(parsed));
    onChange(parsed);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    setDraft(raw);
    const parsed = parsePlatformLimitInput(raw);
    if (parsed !== null) {
      // 合法输入立即生效（自动保存）；空/非法等待 blur/Enter 校正
      lastValidRef.current = parsed;
      onChange(parsed);
    }
  };

  const color = PLATFORM_COLORS[platform] || "#4ca4dc";

  return (
    <div className="setting-row">
      <div>
        <div className="setting-label"><i className="pd" style={{ backgroundColor: color }} />{PLATFORM_LABELS[platform]}</div>
        <p className="setting-desc">每轮获取 {MIN_PLATFORM_LIMIT}–{MAX_PLATFORM_LIMIT} 条内容</p>
      </div>
      <div className="stepper">
        <button
          type="button"
          aria-label={`减少${PLATFORM_LABELS[platform]}数量`}
          onClick={() => { const next = value - 1; if (next >= MIN_PLATFORM_LIMIT) onChange(next); }}
          disabled={value <= MIN_PLATFORM_LIMIT}
          className="stepper-button"
        >
          <Minus className="w-4 h-4" />
        </button>
        <input
          type="text"
          inputMode="numeric"
          value={draft}
          onChange={handleChange}
          onBlur={() => commit(draft)}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
          aria-label={`${PLATFORM_LABELS[platform]}搜索数量`}
          className="stepper-input"
        />
        <button
          type="button"
          aria-label={`增加${PLATFORM_LABELS[platform]}数量`}
          onClick={() => { const next = value + 1; if (next <= MAX_PLATFORM_LIMIT) onChange(next); }}
          disabled={value >= MAX_PLATFORM_LIMIT}
          className="stepper-button"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

export function SearchSettings({ limits, setLimit, resetAll }: UsePlatformLimitsResult) {
  return (
<section className="settings-section-enter">
        <div className="settings-title"><h2>搜索设置</h2><p>为不同平台，留出合适的搜索数量。</p></div>
        <div>
          {PLATFORM_ORDER.map((p) => (
            <LimitRow
              key={p}
              platform={p}
              value={limits[p]}
              onChange={(v) => setLimit(p, v)}
            />
          ))}
        </div>
        <div className="setting-footer"><span>修改后从下一次搜索开始生效</span><button type="button" className="text-link" onClick={resetAll}>恢复默认数量</button></div>
        <div className="info-box"><h3>多一点内容，也需要多一点时间</h3><p>数量越大，搜索耗时可能越长，也更容易遇到平台请求限制。默认每个平台 20 条；需要更多时，可以在结果页继续搜索。</p></div>
      </section>
  );
}
