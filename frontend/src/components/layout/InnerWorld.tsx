import { useState, type ComponentType } from "react";
import InnerStatePanel from "../inner/InnerStatePanel";
import ActivityPanel from "../panels/ActivityPanel";
import DesiresPanel from "../panels/DesiresPanel";
import MaterialsPanel from "../panels/MaterialsPanel";
import NarrativePanel from "../panels/NarrativePanel";
import OutputsPanel from "../panels/OutputsPanel";

// 内心世界（右侧滑出抽屉）：6 个观测面板（内在/欲望/活动/产出/叙事/资料）从「设置」移出，
// 收进对话框右侧的「内心世界」抽屉，默认收起、点对话框头部「内心」滑出。复用 side-panel 的
// 标签/内容样式（同款暖色卡片），仅容器位置不同（固定右侧 + transform 滑入滑出）。
// 仅挂载当前 tab（切换即重新 refresh；未激活面板不占 DOM），同 SidePanel。
type TabDef = { label: string; Panel: ComponentType };

const TABS: TabDef[] = [
  { label: "内在", Panel: InnerStatePanel },
  { label: "欲望", Panel: DesiresPanel },
  { label: "活动", Panel: ActivityPanel },
  { label: "产出", Panel: OutputsPanel },
  { label: "叙事", Panel: NarrativePanel },
  { label: "资料", Panel: MaterialsPanel },
];

type InnerWorldProps = {
  open: boolean;
  onClose: () => void;
};

export default function InnerWorld({ open, onClose }: InnerWorldProps) {
  const [active, setActive] = useState(0);
  const ActivePanel = TABS[active].Panel;

  return (
    <aside className={`inner-world${open ? " inner-world--open" : ""}`} aria-hidden={!open}>
      <header className="side-panel__header">
        <span className="side-panel__title">内心世界</span>
        <button type="button" className="side-panel__back" onClick={onClose}>
          收起
        </button>
      </header>
      <nav className="side-panel__tabs">
        {TABS.map((t, i) => (
          <button
            key={t.label}
            type="button"
            className={`side-panel__tab${active === i ? " side-panel__tab--active" : ""}`}
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
