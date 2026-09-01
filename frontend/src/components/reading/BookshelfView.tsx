import { useEffect, useRef, useState } from "react";
import { importBook } from "../../api/client";
import { useReaderStore } from "../../stores/readerStore";
import Panel from "../layout/Panel";

// 书架：列出已导入书 + 「导入 EPUB」按钮；点书 openBook 进阅读页（06 §1）。
export default function BookshelfView() {
  const books = useReaderStore((s) => s.books);
  const booksError = useReaderStore((s) => s.booksError);
  const loadBooks = useReaderStore((s) => s.loadBooks);
  const openBook = useReaderStore((s) => s.openBook);
  const [importError, setImportError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void loadBooks();
  }, [loadBooks]);

  const onImport = async (file: File) => {
    setImporting(true);
    setImportError(null);
    try {
      await importBook(file);
      await loadBooks(); // 刷新书架
    } catch (err) {
      // 409 重复 / 400 非 epub·超限 / 500 解析失败均走统一 throw（05-client §2）
      setImportError(err instanceof Error ? err.message : String(err));
    } finally {
      setImporting(false);
    }
  };

  return (
    <Panel title="读书">
      <div className="bookshelf__toolbar">
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
          {importing ? "导入中…" : "导入 EPUB"}
        </button>
        {importError !== null && <span className="error-text">{importError}</span>}
      </div>
      {booksError !== null && <p className="error-text">{booksError}</p>}
      {books.length === 0 ? (
        <p className="panel-item">书架空空如也，导入一本 EPUB 开始陪读吧。</p>
      ) : (
        <ul className="panel-list">
          {books.map((b) => (
            <li key={b.id}>
              <button
                type="button"
                className="bookshelf__item"
                onClick={() => void openBook(b.id)}
              >
                <span className="panel-item__main">{b.title}</span>
                <span className="panel-item__meta">
                  {b.author} · {b.total_paragraphs} 段
                  {b.user_position > 0 ? ` · 读到第 ${b.user_position} 段` : " · 未读"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
