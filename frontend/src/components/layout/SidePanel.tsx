import { useState, type ComponentType } from "react";
import BackgroundPanel from "../panels/BackgroundPanel";
import EvalPanel from "../panels/EvalPanel";

// 设置标签页面板（视觉改造布局 §1）：观测面板（含记忆）已移入右侧「内心世界」抽屉，此处只留
// 背景外观/Eval 两项，标签切换一次显示一个。首个「背景」承载背景色调/背景图设置。
// 仅挂载当前 tab（切换即重新 refresh；store 已缓存数据，无「等待」闪烁），
// 未激活面板不占 DOM，规避旧抽屉「flex 子项被压缩裁掉、无法滚动」的 bug。
type TabDef = { label: string; Panel: ComponentType };

const TABS: TabDef[] = [
  { label: "背景", Panel: BackgroundPanel },
  { label: "Eval", Panel: EvalPanel },
];

type SidePanelProps = {
  onBack: () => void;
};

export default function SidePanel({ onBack }: SidePanelProps) {
  const [active, setActive] = useState(0);
  const ActivePanel = TABS[active].Panel;

  return (
    <aside className="side-panel">
      <header className="side-panel__header">
        <span className="side-panel__title">设置</span>
        <button type="button" className="side-panel__back" onClick={onBack}>
          返回对话
        </button>
      </header>
      <nav className="side-panel__tabs">
        {TABS.map((t, i) => (
          <button
            key={t.label}
            type="button"
            className={`side-panel__tab ${active === i ? "side-panel__tab--active" : ""}`}
            aria-pressed={active === i}
            onClick={() => setActive(i)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="side-panel__body">
        <ActivePanel />
      </div>
    </aside>
  );
}
