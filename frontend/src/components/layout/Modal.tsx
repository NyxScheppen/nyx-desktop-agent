import frontendPolicy from "../../../../nyx/settings/frontend.json";
import type { ReactNode } from "react";

type ModalProps = {
  title: string;
  onClose: () => void;
  children?: ReactNode;
  page?: boolean;
};

// 通用模态框：半透明遮罩 + 居中对话框。点遮罩关闭；点对话框内容不冒泡关闭。
export default function Modal({ title, onClose, children, page }: ModalProps) {
  if (page) {
    return <section className="nyx-mobile__tool-page" aria-label={title}>
      <header>
        <button type="button" aria-label={frontendPolicy.copy.Mobile.back} onClick={onClose}>←</button>
        <h2>{title}</h2>
      </header>
      {children}
    </section>;
  }
  return (
    <div className="modal-overlay" onClick={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal__header">
          <span className="modal__title">{title}</span>
          <button
            type="button"
            className="modal__close"
            aria-label={frontendPolicy.copy.Modal.aria_label}
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        <div className="modal__body">{children}</div>
      </section>
    </div>
  );
}
