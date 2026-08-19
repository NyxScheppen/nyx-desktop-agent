import { useState } from "react";
import InnerStatePanel from "../inner/InnerStatePanel";
import Panel from "./Panel";

// 侧栏抽屉（视觉改造布局 §1）：收纳非对话面板（内在状态/欲望/活动/记忆/Eval/溯源），
// 主界面只留半身像立绘 + 底部对话框。按钮切换 + 背景遮罩点击关闭。
export default function SideDrawer() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        className="drawer-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        {open ? "✕" : "面板"}
      </button>
      <aside className={`drawer ${open ? "drawer--open" : ""}`} aria-hidden={!open}>
        <InnerStatePanel />
        <Panel title="欲望" placeholder />
        <Panel title="活动" placeholder />
        <Panel title="记忆" placeholder />
        <Panel title="Eval" placeholder />
        <Panel title="溯源" placeholder />
      </aside>
      {open && <div className="drawer-backdrop" onClick={() => setOpen(false)} />}
    </>
  );
}
