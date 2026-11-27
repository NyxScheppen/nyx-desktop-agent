import { useRef, useState, type FormEvent } from "react";
import frontendPolicy from "../../../../nyx/settings/frontend.json";
import readingPolicy from "../../../../nyx/settings/reading.json";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { formatPolicyText } from "../../lib/policy";
import { useAnnounceStore } from "../../stores/announceStore";
import { useCharacterStore } from "../../stores/characterStore";
import { useChatStore } from "../../stores/chatStore";
import EmotionSprite from "../inner/EmotionSprite";

export default function ReadingCompanion({ bookId, open, onToggle }: {
  bookId: string; open: boolean; onToggle: () => void;
}) {
  const role = useCharacterStore((state) => state.registry?.characters.find((item) => item.id === state.registry?.current_id));
  const messages = useChatStore((state) => state.messages);
  const notices = useAnnounceStore((state) => state.items);
  const busy = useChatStore((state) => state.isSending);
  const sendError = useChatStore((state) => state.sendError);
  const sendMessage = useChatStore((state) => state.sendMessage);
  const [draft, setDraft] = useState("");
  const [selected, setSelected] = useState("");
  const [ownError, setOwnError] = useState(false);
  const seen = useRef<string>();
  const name = role?.name ?? frontendPolicy.copy.Mobile.character;
  const turns = messages.filter((message) => message.bookId === bookId &&
    (message.kind === "reading_question" || message.kind === "initiate_chat"));
  const waiting = turns.filter((message) => message.question_status === "waiting" && message.attempt_id && message.paused_at == null);
  const target = waiting.find((message) => message.attempt_id === selected) ?? waiting.at(-1);
  const question = target ?? turns.at(-1);
  const aside = notices.filter((notice) => notice.bookId === bookId).at(-1);
  const latest = aside?.id ?? question?.id;
  const unread = !open && latest !== undefined && latest !== seen.current && (aside !== undefined || question?.preloaded !== true);
  const placeholder = formatPolicyText(readingPolicy.templates.Mobile.companion_reply, { p0: name });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !draft.trim()) return;
    const scope = captureScope();
    const text = draft;
    setOwnError(false);
    const sent = await sendMessage(text.trim(), target?.attempt_id ?? null);
    if (!isScopeCurrent(scope)) return;
    if (sent) setDraft((current) => current === text ? "" : current);
    setOwnError(!sent);
  }

  return <section className="nyx-mobile__companion">
    <button className="nyx-mobile__companion-toggle" type="button" aria-expanded={open} onClick={() => {
      seen.current = latest;
      onToggle();
    }}>
      <EmotionSprite size="circle" />
      <span className="nyx-mobile__companion-copy">
        <strong>{name}{unread && <span className="nyx-mobile__unread" role="img" aria-label={readingPolicy.copy.ReaderView.mobile_new_message} />}</strong>
        <small>{question?.content ?? aside?.text ?? formatPolicyText(readingPolicy.templates.Mobile.companion_empty, { p0: name })}</small>
      </span>
      <span aria-hidden="true">{open ? "⌄" : "⌃"}</span>
    </button>
    {open && <>
      <div className="nyx-mobile__companion-log" aria-live="polite">
        {aside && <p>{aside.text}</p>}
        {question && <p>{question.content}</p>}
      </div>
      <form className="nyx-mobile__companion-form" onSubmit={(event) => void submit(event)}>
        {waiting.length > 1 && <label>
          {frontendPolicy.copy.Mobile.question}
          <select value={target?.attempt_id ?? ""} onChange={(event) => setSelected(event.target.value)}>
            {waiting.map((message) => <option key={message.id} value={message.attempt_id}>{message.content}</option>)}
          </select>
        </label>}
        <input aria-label={placeholder} placeholder={placeholder} value={draft} onChange={(event) => setDraft(event.target.value)} />
        <button className="chat-input__send" type="submit" disabled={busy || !draft.trim()}>{frontendPolicy.copy.ChatInput.text}</button>
        {ownError && sendError && <p role="alert">{sendError}</p>}
      </form>
    </>}
  </section>;
}
