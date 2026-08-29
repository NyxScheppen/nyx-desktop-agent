import type { ReactNode } from "react";

type ModalProps = {
  title: string;
  onClose: () => void;
  children?: ReactNode;
};

// 通用模态框：半透明遮罩 + 居中对话框。点遮罩关闭；点对话框内容不冒泡关闭。
export default function Modal({ title, onClose, children }: ModalProps) {
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
            aria-label="关闭"
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
