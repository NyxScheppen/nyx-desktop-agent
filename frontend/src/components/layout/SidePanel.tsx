import { useState, type ComponentType } from "react";
import InnerStatePanel from "../inner/InnerStatePanel";
import ActivityPanel from "../panels/ActivityPanel";
import BackgroundPanel from "../panels/BackgroundPanel";
import DesiresPanel from "../panels/DesiresPanel";
import EvalPanel from "../panels/EvalPanel";
import MaterialsPanel from "../panels/MaterialsPanel";
import MemoryPanel from "../panels/MemoryPanel";
import NarrativePanel from "../panels/NarrativePanel";
import OutputsPanel from "../panels/OutputsPanel";

// 设置标签页面板（视觉改造布局 §1）：非对话面板收成一列，标签切换一次显示一个。
// 首个「背景」标签承载背景色调/背景图设置，其余为观测面板。
// 仅挂载当前 tab（切换即重新 refresh；store 已缓存数据，无「等待」闪烁），
// 未激活面板不占 DOM，规避旧抽屉「flex 子项被压缩裁掉、无法滚动」的 bug。
type TabDef = { label: string; Panel: ComponentType };

const TABS: TabDef[] = [
  { label: "背景", Panel: BackgroundPanel },
  { label: "内在", Panel: InnerStatePanel },
  { label: "欲望", Panel: DesiresPanel },
  { label: "活动", Panel: ActivityPanel },
  { label: "产出", Panel: OutputsPanel },
  { label: "叙事", Panel: NarrativePanel },
  { label: "资料", Panel: MaterialsPanel },
  { label: "记忆", Panel: MemoryPanel },
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
