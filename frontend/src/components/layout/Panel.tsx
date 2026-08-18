import type { ReactNode } from "react";

type PanelProps = {
  title: string;
  placeholder?: boolean;
  children?: ReactNode;
};

// 通用面板容器：占位面板复用（frontend/README §5）
export default function Panel({ title, placeholder = false, children }: PanelProps) {
  return (
    <section className="panel">
      <header className="panel-header">
        <h2>{title}</h2>
        {placeholder && <span className="panel-badge">后续</span>}
      </header>
      <div className="panel-body">
        {children ?? (placeholder ? "占位" : null)}
      </div>
    </section>
  );
}
