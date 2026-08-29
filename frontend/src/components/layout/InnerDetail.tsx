import { useState } from "react";
import InnerStatePanel from "../inner/InnerStatePanel";
import ActivityPanel from "../panels/ActivityPanel";
import DesiresPanel from "../panels/DesiresPanel";
import Modal from "./Modal";

// 内在详情弹层：三个 tab（内在状态/欲望/活动）。
const TABS = [
  ["state", "内在状态"],
  ["desire", "欲望"],
  ["activity", "活动"],
] as const;

type TabKey = (typeof TABS)[number][0];

type InnerDetailProps = {
  onClose: () => void;
};

export default function InnerDetail({ onClose }: InnerDetailProps) {
  const [tab, setTab] = useState<TabKey>("state");

  return (
    <Modal title="内在详情" onClose={onClose}>
      <nav className="modal__tabs">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`modal__tab${tab === key ? " modal__tab--active" : ""}`}
            aria-pressed={tab === key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "state" && <InnerStatePanel />}
      {tab === "desire" && <DesiresPanel />}
      {tab === "activity" && <ActivityPanel />}
    </Modal>
  );
}
