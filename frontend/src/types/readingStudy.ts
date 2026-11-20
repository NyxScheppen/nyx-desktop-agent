import type { Book } from "./api";

export type StudyAnnotation = {
  reply_event_id: string;
  quote: string;
  comment: string;
};
export type StudyInput = {
  input_id: string;
  message: string;
  notes: string[];
  annotation: StudyAnnotation | null;
  presentation: string;
};
export type StudyJob = {
  state: "queued" | "completed" | "failed";
  attempts: number;
  error: string;
  output?: { summary?: string; content?: string };
};
export type StudyTurn = {
  id: string;
  input: Omit<StudyInput, "input_id">;
  created_at: number;
  status: "queued" | "running" | "committed" | "failed" | "incomplete";
  reply: string;
  error: string;
};
export type StudySource = {
  paragraph_id: string;
  index: number;
  quote: string;
};
export type StudyNode = {
  id: string;
  title: string;
  content: string;
  version: number;
  state: "unassessed" | "clarify" | "explaining" | "connecting";
  sources: StudySource[];
  evidence: string[];
  corrects: string[];
};
export type StudyGraph = {
  version: number;
  nodes: StudyNode[];
  edges: { from: string; to: string; label: string }[];
  deleted: string[];
};
export type StudySession = {
  id: string;
  created_at: number;
  ended_at: number | null;
  turns: StudyTurn[];
  results: Record<string, StudyJob>;
};
export type StudySnapshot = {
  revision: number;
  book: Book;
  session: StudySession;
  graph: StudyGraph;
  history: {
    id: string;
    version: number;
    created_at: number;
    kind: string;
    reason?: string;
    operations: {
      id: string;
      op?: string;
      title?: string;
      content?: string;
      sources?: StudySource[];
    }[];
    evidence?: string[];
  }[];
  evidence: {
    id: string;
    session_id: string;
    input: StudyTurn["input"];
    reply: string;
  }[];
  materials: (StudyJob & {
    id: string;
    start: number;
    end: number;
    summary: string;
  })[];
  graph_work: StudyJob;
  pending_evidence: boolean;
  feedback_jobs: StudyJob[];
  sessions: Omit<StudySession, "turns">[];
};
export type StudyNote = { id: string; content: string; revision: number };
