import { useEffect, useState } from "react";
import policy from "../../../../nyx/settings/reading.json";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { getStudy, retryStudy } from "../../api/client";
import type { StudySession, StudySnapshot } from "../../types/readingStudy";
import Modal from "../layout/Modal";
import StudyMarkdown from "./StudyMarkdown";

const copy = policy.study.copy;

export function StudyResults({
  sessions,
}: {
  sessions: Omit<StudySession, "turns">[];
}) {
  return (
    <>
      {sessions.map((session) => (
        <article key={session.id}>
          <h3>{new Date(session.created_at * 1000).toLocaleString()}</h3>
          {Object.entries(session.results).map(([kind, result]) => (
            <div key={kind}>
              <strong>
                {kind === "summary"
                  ? copy.snapshot
                  : kind === "memory"
                    ? copy.memory
                    : copy.reflection}
              </strong>
              {" · "}
              {result.state === "completed"
                ? copy.completed
                : result.state === "failed"
                  ? copy.failure
                  : copy.queued}
              {result.error && <p role="alert">{result.error}</p>}
              {result.output?.summary && (
                <StudyMarkdown text={result.output.summary} />
              )}
            </div>
          ))}
        </article>
      ))}
    </>
  );
}

export default function BookStudyResults({ bookId }: { bookId: string }) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<StudySnapshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const scope = captureScope();
    let active = true;
    const refresh = () =>
      void getStudy(bookId)
        .then((value) => {
          if (active && isScopeCurrent(scope)) {
            setSnapshot(value);
            setError("");
          }
        })
        .catch((reason: unknown) => {
          if (active && isScopeCurrent(scope)) setError(String(reason));
        });
    refresh();
    const timer = setInterval(refresh, policy.study.parameters.poll_ms);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [bookId, open]);
  return (
    <>
      <button
        type="button"
        className="reading-btn"
        onClick={() => setOpen(true)}
      >
        {copy.results}
      </button>
      {open && (
        <Modal title={copy.results} onClose={() => setOpen(false)}>
          {error && <p role="alert">{error}</p>}
          {snapshot && (
            <>
              <p>{snapshot.pending_evidence ? copy.pending : copy.completed}</p>
              <StudyResults sessions={snapshot.sessions} />
              {[
                ...snapshot.materials,
                snapshot.graph_work,
                ...snapshot.feedback_jobs,
              ]
                .filter((job) => job.error)
                .map((job, i) => (
                  <p role="alert" key={i}>
                    {job.error}
                  </p>
                ))}
              <button
                className="reading-btn"
                onClick={() => {
                  const scope = captureScope();
                  void retryStudy(bookId).catch((reason: unknown) => {
                    if (isScopeCurrent(scope)) setError(String(reason));
                  });
                }}
              >
                {copy.retry}
              </button>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
