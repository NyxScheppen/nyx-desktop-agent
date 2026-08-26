import { useState } from "react";
import type { ComponentType } from "react";
import InnerStatePanel from "../inner/InnerStatePanel";
import ActivityPanel from "../panels/ActivityPanel";
import DesiresPanel from "../panels/DesiresPanel";
import MaterialsPanel from "../panels/MaterialsPanel";
import MemoryPanel from "../panels/MemoryPanel";
import NarrativePanel from "../panels/NarrativePanel";
import OutputsPanel from "../panels/OutputsPanel";
import ReadingNotesPanel from "../panels/ReadingNotesPanel";

// 内心世界（页内面板，替换书卷区）：8 个观测面板按「内在/空间/记录」三大类，
// 顶部横向子标签条（网页 tab 感）点切换活动面板；传 categoryIndex（0 内在 / 1 空间 / 2 记录）。
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
};

export default function InnerWorld({ categoryIndex }: InnerWorldProps) {
  const category = CATEGORIES[categoryIndex];
  const [activeTab, setActiveTab] = useState(0);
  const ActivePanel = category.tabs[activeTab].Panel;

  return (
    <section className="side-panel">
      <header className="side-panel__header">
        <span className="side-panel__title">{category.label}</span>
      </header>
      <nav className="side-panel__tabs" aria-label="分类面板">
        {category.tabs.map((t, i) => (
          <button
            key={t.label}
            type="button"
            className={`side-panel__tab${
              i === activeTab ? " side-panel__tab--active" : ""
            }`}
            aria-pressed={i === activeTab}
            onClick={() => setActiveTab(i)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="side-panel__body">
        <ActivePanel />
      </div>
    </section>
  );
}
