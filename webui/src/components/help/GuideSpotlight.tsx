import { useEffect, useState } from "react";

/** 高亮框比目标外扩几像素，免得描边压在控件自己的边框上。 */
const SPOTLIGHT_PADDING = 6;
/** 气泡和框之间的间距。 */
const HINT_GAP = 8;
const HINT_MAX_WIDTH = 340;
/** 往下放气泡所需的最小空间，不够就翻到框的上方。 */
const HINT_MIN_ROOM = 72;

interface Anchor {
  top: number;
  left: number;
  width: number;
  height: number;
  /** 视口尺寸一起存：窗口变化会影响气泡放下方还是上方。 */
  viewportWidth: number;
  viewportHeight: number;
}

function sameAnchor(a: Anchor, b: Anchor): boolean {
  return a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height
    && a.viewportWidth === b.viewportWidth && a.viewportHeight === b.viewportHeight;
}

/**
 * 跟着目标元素走的高亮框：中间挖空、四周压暗，纯视觉、不拦点击。
 *
 * - 定位用视口坐标 + `position: fixed`，滚动 / 窗口变化 / 元素尺寸变化都重算，
 *   所以滚轮一动，框就跟着目标一起走。
 * - 压暗靠一层超大 `box-shadow` 铺满视口，不需要额外的遮罩元素。
 * - 整层 `pointer-events: none`：它只是提示，不锁交互，高亮的东西照样能点。
 * - 教程跳到这一步的那一帧目标可能还没渲染（页面是懒加载的），所以先按帧重试。
 */
export function GuideSpotlight({ selector, hint }: { selector: string; hint: string }) {
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  useEffect(() => {
    let frame = 0;
    let attempts = 0;

    const measure = () => {
      frame = 0;
      const target = document.querySelector(selector);
      if (!target) {
        setAnchor(null);
        return false;
      }
      const box = target.getBoundingClientRect();
      const next: Anchor = {
        top: box.top,
        left: box.left,
        width: box.width,
        height: box.height,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
      setAnchor((previous) => (previous && sameAnchor(previous, next) ? previous : next));
      return true;
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    const bootstrap = () => {
      if (measure()) return;
      attempts += 1;
      if (attempts < 60) frame = requestAnimationFrame(bootstrap);
    };

    bootstrap();
    // 捕获阶段监听：页面内部的滚动容器也能收到。
    window.addEventListener("scroll", schedule, { capture: true, passive: true });
    window.addEventListener("resize", schedule, { passive: true });
    const observer = new ResizeObserver(schedule);
    observer.observe(document.body);
    return () => {
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [selector]);

  if (!anchor) return null;

  const top = anchor.top - SPOTLIGHT_PADDING;
  const left = anchor.left - SPOTLIGHT_PADDING;
  const width = anchor.width + SPOTLIGHT_PADDING * 2;
  const height = anchor.height + SPOTLIGHT_PADDING * 2;
  const maxWidth = Math.min(HINT_MAX_WIDTH, anchor.viewportWidth - 24);
  const below = top + height + HINT_GAP + HINT_MIN_ROOM <= anchor.viewportHeight;

  return (
    <>
      <div className="guide-spotlight" aria-hidden="true" style={{ top, left, width, height }} />
      {/* 文案与卡片正文重复，所以对读屏隐藏，避免同一句被念两遍。 */}
      <p
        className="guide-spotlight-hint"
        aria-hidden="true"
        style={{
          left: Math.max(12, Math.min(left, anchor.viewportWidth - maxWidth - 12)),
          maxWidth,
          ...(below
            ? { top: top + height + HINT_GAP }
            : { bottom: anchor.viewportHeight - top + HINT_GAP }),
        }}
      >
        {hint}
      </p>
    </>
  );
}
