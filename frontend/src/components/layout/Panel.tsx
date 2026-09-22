import type { ReactNode } from "react";

type PanelProps = {
  title: string;
  children?: ReactNode;
  className?: string;
};

// 通用面板容器：统一标题头 + 内容体。
export default function Panel({ title, children, className }: PanelProps) {
  return (
    <section className={className === undefined ? "panel" : `panel ${className}`}>
      <header className="panel-header">
        <h2>{title}</h2>
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}
