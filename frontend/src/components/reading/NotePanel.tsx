import { useEffect, useState } from "react";
import { useReaderStore } from "../../stores/readerStore";
import Modal from "../layout/Modal";

// 笔记面板（07 §3）：覆盖层打开，NoteComposer（新建）+ NoteList（列表）。
// 只展示用户笔记 + 「给尼克斯看」批注；Nyx 章末整合笔记落 memory 不上屏。
export default function NotePanel({ onClose }: { onClose: () => void }) {
  const bookId = useReaderStore((s) => s.bookId);
  const notes = useReaderStore((s) => s.notes);
  const notesError = useReaderStore((s) => s.notesError);
  const loadNotes = useReaderStore((s) => s.loadNotes);
  const addNote = useReaderStore((s) => s.addNote);
  const updateNote = useReaderStore((s) => s.updateNote);
  const deleteNote = useReaderStore((s) => s.deleteNote);
  const showToNyx = useReaderStore((s) => s.showToNyx);

  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");

  useEffect(() => {
    void loadNotes();
  }, [loadNotes]);

  const submit = () => {
    const content = draft.trim();
    if (content === "" || bookId === null) return;
    void addNote({ book_id: bookId, content });
    setDraft("");
  };

  const startEdit = (id: string, content: string) => {
    setEditingId(id);
    setEditDraft(content);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft("");
  };

  const saveEdit = () => {
    if (editingId === null) return;
    const content = editDraft.trim();
    if (content === "") return;
    void updateNote(editingId, content);
    setEditingId(null);
    setEditDraft("");
  };

  return (
    <Modal title="笔记" onClose={onClose}>
      <div className="note-composer">
        <textarea
          className="note-composer__input"
          placeholder="记点什么…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          type="button"
          className="reading-btn"
          onClick={submit}
          disabled={draft.trim() === ""}
        >
          记笔记
        </button>
      </div>
      {notesError !== null && <p className="error-text">{notesError}</p>}
      {notes.length === 0 ? (
        <p className="panel-item">还没有笔记，随手记一条吧。</p>
      ) : (
        <ul className="note-list">
          {notes.map((n) => (
            <li key={n.id} className="note-item">
              {n.selected_text !== null && (
                <blockquote className="note-item__quote">{n.selected_text}</blockquote>
              )}
              {editingId === n.id ? (
                <textarea
                  className="note-composer__input"
                  aria-label="编辑笔记"
                  value={editDraft}
                  onChange={(e) => setEditDraft(e.target.value)}
                />
              ) : (
                <p className="note-item__content">{n.content}</p>
              )}
              {n.annotations.length > 0 && (
                <ul className="note-item__anns">
                  {n.annotations.map((a) => (
                    <li key={a.id} className="note-item__ann">
                      {a.content}
                    </li>
                  ))}
                </ul>
              )}
              <div className="note-item__actions">
                {editingId === n.id ? (
                  <>
                    <button
                      type="button"
                      className="reading-btn"
                      onClick={saveEdit}
                      disabled={editDraft.trim() === ""}
                    >
                      保存
                    </button>
                    <button
                      type="button"
                      className="reading-btn"
                      onClick={cancelEdit}
                    >
                      取消
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      className="reading-btn"
                      onClick={() => void showToNyx(n.id)}
                    >
                      给尼克斯看
                    </button>
                    <button
                      type="button"
                      className="reading-btn"
                      onClick={() => void deleteNote(n.id)}
                    >
                      删除
                    </button>
                    <button
                      type="button"
                      className="reading-btn"
                      onClick={() => startEdit(n.id, n.content)}
                    >
                      编辑
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
