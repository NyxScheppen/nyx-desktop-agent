import { useState, type ComponentType } from "react";
import InnerStatePanel from "../inner/InnerStatePanel";
import ActivityPanel from "../panels/ActivityPanel";
import DesiresPanel from "../panels/DesiresPanel";
import MaterialsPanel from "../panels/MaterialsPanel";
import MemoryPanel from "../panels/MemoryPanel";
import NarrativePanel from "../panels/NarrativePanel";
import OutputsPanel from "../panels/OutputsPanel";
import ReadingNotesPanel from "../panels/ReadingNotesPanel";
import DraggablePanel from "./DraggablePanel";

// 内心世界（可拖拽弹窗）：8 个观测面板按「内在/空间/记录」三大类收进可拖拽弹窗。
// 左面板摘要按钮（LeftPanel 调 onOpenInner 传分类 index）触发，点哪个开哪个
// 分类的卡片；卡片内只保留该分类的子标签 + 内容，顶部大类导航移除（大类由左面板摘要按钮承担）。
// 仅挂载当前子 tab（切换即重新 refresh；未激活面板不占 DOM），同 SidePanel。
export type TabDef = { label: string; Panel: ComponentType };
export type CategoryDef = { label: string; tabs: TabDef[] };

export const CATEGORIES: CategoryDef[] = [
  {
    label: "内在",
    tabs: [
      { label: "内在状态", Panel: InnerStatePanel },
      { label: "欲望", Panel: DesiresPanel },
      { label: "叙事", Panel: NarrativePanel },
    ],
  },
  {
    label: "空间",
    tabs: [
      { label: "读书笔记", Panel: ReadingNotesPanel },
      { label: "产出", Panel: OutputsPanel },
      { label: "资料", Panel: MaterialsPanel },
    ],
  },
  {
    label: "记录",
    tabs: [
      { label: "活动", Panel: ActivityPanel },
      { label: "记忆", Panel: MemoryPanel },
    ],
  },
];

type InnerWorldProps = {
  categoryIndex: number;
  onClose: () => void;
};

export default function InnerWorld({ categoryIndex, onClose }: InnerWorldProps) {
  const [activeTab, setActiveTab] = useState(0);
  const category = CATEGORIES[categoryIndex];
  const ActivePanel = category.tabs[activeTab].Panel;

  return (
    <DraggablePanel title={category.label} onClose={onClose}>
      <nav className="side-panel__tabs">
        {category.tabs.map((t, i) => (
          <button
            key={t.label}
            type="button"
            className={`side-panel__tab${
              activeTab === i ? " side-panel__tab--active" : ""
            }`}
            aria-pressed={activeTab === i}
            onClick={() => setActiveTab(i)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="side-panel__body">
        <ActivePanel />
      </div>
    </DraggablePanel>
  );
}
