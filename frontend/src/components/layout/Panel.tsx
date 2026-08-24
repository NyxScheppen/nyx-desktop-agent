import type { ReactNode } from "react";

type PanelProps = {
  title: string;
  children?: ReactNode;
};

// 通用面板容器：统一标题头 + 内容体。
export default function Panel({ title, children }: PanelProps) {
  return (
    <section className="panel">
      <header className="panel-header">
        <h2>{title}</h2>
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}
