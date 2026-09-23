import { X } from "lucide-react";

/**
 * 教程进行中常驻的退出入口，固定在右下角。
 *
 * 教程不锁交互，用户随时可能滚到别处、点开别的页面，卡片会被顶出视口 ——
 * 这时得有个不随页面滚走的出口，免得卡在「教程还在，但找不到怎么关」。
 * 位置和「返回顶部」（`.scroll-top`）上下错开，两者可能同时出现。
 */
export function GuideExit({ label, onExit }: { label: string; onExit: () => void }) {
  return (
    <button type="button" className="btn guide-exit" onClick={onExit}>
      <X aria-hidden="true" />
      {label}
    </button>
  );
}
