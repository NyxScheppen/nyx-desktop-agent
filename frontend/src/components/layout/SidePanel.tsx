import { useState, type ComponentType } from "react";
import InnerStatePanel from "../inner/InnerStatePanel";
import ActivityPanel from "../panels/ActivityPanel";
import DesiresPanel from "../panels/DesiresPanel";
import EvalPanel from "../panels/EvalPanel";
import MemoryPanel from "../panels/MemoryPanel";
import TracePanel from "../panels/TracePanel";

// 右侧标签页面板（视觉改造布局 §1）：非对话面板收成一列，标签切换一次显示一个。
// 仅挂载当前 tab（切换即重新 refresh；store 已缓存数据，无「等待」闪烁），
// 未激活面板不占 DOM，规避旧抽屉「flex 子项被压缩裁掉、无法滚动」的 bug。
type TabDef = { label: string; Panel: ComponentType };

const TABS: TabDef[] = [
  { label: "内在", Panel: InnerStatePanel },
  { label: "欲望", Panel: DesiresPanel },
  { label: "活动", Panel: ActivityPanel },
  { label: "记忆", Panel: MemoryPanel },
  { label: "Eval", Panel: EvalPanel },
  { label: "溯源", Panel: TracePanel },
];

export default function SidePanel() {
  const [active, setActive] = useState(0);
  const ActivePanel = TABS[active].Panel;

  return (
    <aside className="side-panel">
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
