import { captureScope, isScopeCurrent } from "./characterScope";

// Each module has one active fetch and at most one queued refresh. Scope identity
// prevents an old role's completion from draining or replacing a new role's work.
export function coalescedRefresh(refresh: () => Promise<void>): () => Promise<void> {
  let active: {
    scope: ReturnType<typeof captureScope>;
    dirty: boolean;
    started: boolean;
    promise: Promise<void>;
  } | undefined;
  return () => {
    const scope = captureScope();
    if (!isScopeCurrent(scope)) return Promise.resolve();
    if (active?.scope === scope) {
      if (active.started) active.dirty = true;
      return active.promise;
    }
    const job = { scope, dirty: false, started: false, promise: Promise.resolve() };
    active = job;
    job.promise = Promise.resolve().then(async () => {
      do {
        job.dirty = false;
        if (!isScopeCurrent(scope)) return;
        job.started = true;
        await refresh();
        if (!job.dirty && active === job) active = undefined;
      } while (job.dirty && isScopeCurrent(scope));
    }).finally(() => {
      if (active === job) active = undefined;
    });
    return job.promise;
  };
}
