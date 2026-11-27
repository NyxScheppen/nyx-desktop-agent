import { formatPolicyText } from "../../lib/policy";
import readingPolicy from "../../../../nyx/settings/reading.json";
import { useEffect, useRef, useState } from "react";
import { importBook } from "../../api/client";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { useReaderStore } from "../../stores/readerStore";
import Panel from "../layout/Panel";

// 书架：列出已导入书 + 「导入 EPUB」按钮；点书 openBook 进阅读页（06 §1）。
export default function BookshelfView({ onOpenBook }: { onOpenBook?: (id: string) => void }) {
  const books = useReaderStore((s) => s.books);
  const booksError = useReaderStore((s) => s.booksError);
  const loadBooks = useReaderStore((s) => s.loadBooks);
  const openBook = useReaderStore((s) => s.openBook);
  const [importError, setImportError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const resume = books.find((book) => book.user_position > 0);

  useEffect(() => {
    void loadBooks();
  }, [loadBooks]);

  const onImport = async (file: File) => {
    const scope = captureScope();
    setImporting(true);
    setImportError(null);
    try {
      await importBook(file);
      if (!isScopeCurrent(scope)) return;
      await loadBooks(); // 刷新书架
    } catch (err) {
      // 409 重复 / 400 非 epub·超限 / 500 解析失败均走统一 throw
      if (isScopeCurrent(scope)) setImportError(err instanceof Error ? err.message : String(err));
    } finally {
      if (isScopeCurrent(scope)) setImporting(false);
    }
  };

  return (
    <Panel title={onOpenBook ? undefined : readingPolicy.copy.BookshelfView.title}>
      {onOpenBook && resume && <section className="nyx-mobile__resume">
        <div>
          <small>{readingPolicy.copy.BookshelfView.mobile_reading}</small>
          <h2>{resume.title}</h2>
          <p>{formatPolicyText(readingPolicy.templates.reader_progress, { p0: resume.user_position, p1: resume.total_paragraphs })}</p>
        </div>
        <button type="button" onClick={() => onOpenBook(resume.id)}>{readingPolicy.copy.BookshelfView.mobile_resume}</button>
      </section>}
      {!onOpenBook && <div className="bookshelf__toolbar">
        <input
          ref={fileRef}
          type="file"
          accept=".epub"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void onImport(f);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className="reading-btn"
          onClick={() => fileRef.current?.click()}
          disabled={importing}
        >
          {importing ? readingPolicy.copy.BookshelfView.text : readingPolicy.copy.BookshelfView.text_2}
        </button>
        {importError !== null && <span className="error-text">{importError}</span>}
      </div>}
      {booksError !== null && <p className="error-text">{booksError}</p>}
      {books.length === 0 ? (
        <p className="panel-item">{onOpenBook ? readingPolicy.copy.BookshelfView.mobile_empty : readingPolicy.copy.BookshelfView.p}</p>
      ) : (
        <ul className="panel-list">
          {books.map((b) => (
            <li key={b.id}>
              <button
                type="button"
                className="bookshelf__item"
                onClick={() => onOpenBook ? onOpenBook(b.id) : void openBook(b.id)}
              >
                <span className="panel-item__main">{b.title}</span>
                <span className="panel-item__meta">
                  {b.author} · {b.total_paragraphs} 段
                  {b.user_position > 0 ? formatPolicyText(readingPolicy.templates.BookshelfView.text, { p0: b.user_position }) : readingPolicy.copy.BookshelfView.text_3}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
