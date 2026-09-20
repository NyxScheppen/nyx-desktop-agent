import { useEffect } from "react";
import { useGameCompanionStore } from "../../stores/gameCompanionStore";
import Panel from "../layout/Panel";

const STATUS_LABELS = {
  observing: "观察中",
  paused: "已暂停",
  ended: "已结束",
} as const;

const PHASE_LABELS = {
  unknown: "未知",
  exploration: "探索",
  dialogue: "对白",
  choice: "选择",
  turn: "回合",
  transition: "转场",
} as const;

export default function GameCompanionView() {
  const sessionId = useGameCompanionStore((s) => s.sessionId);
  const status = useGameCompanionStore((s) => s.status);
  const gameId = useGameCompanionStore((s) => s.gameId);
  const phase = useGameCompanionStore((s) => s.phase);
  const revision = useGameCompanionStore((s) => s.revision);
  const observation = useGameCompanionStore((s) => s.observation);
  const error = useGameCompanionStore((s) => s.error);
  const pause = useGameCompanionStore((s) => s.pause);
  const resume = useGameCompanionStore((s) => s.resume);
  const stop = useGameCompanionStore((s) => s.stop);
  const confirmChoice = useGameCompanionStore((s) => s.confirmChoice);
  const loadCurrent = useGameCompanionStore((s) => s.loadCurrent);

  useEffect(() => {
    if (sessionId === null) void loadCurrent();
  }, [loadCurrent, sessionId]);

  return (
    <Panel title="游戏陪玩">
      {error !== null && <p className="error-text">{error}</p>}
      {sessionId === null ? (
        <p className="panel-item">还没有进行中的游戏陪玩会话</p>
      ) : (
        <div className="game-companion">
          <div className="game-companion__header">
            <span>{gameId ?? "游戏"}</span>
            <span className="panel-badge">
              {status === null ? "等待中" : STATUS_LABELS[status]}
            </span>
            <span className="panel-item__meta">#{revision}</span>
          </div>
          <div className="game-companion__actions">
            {status === "paused" ? (
              <button type="button" className="reading-btn" onClick={() => void resume()}>
                继续观察
              </button>
            ) : status === "observing" ? (
              <button type="button" className="reading-btn" onClick={() => void pause()}>
                暂停观察
              </button>
            ) : null}
            {status !== "ended" && (
              <button type="button" className="reading-btn" onClick={() => void stop()}>
                结束陪玩
              </button>
            )}
          </div>
          {observation === null ? (
            <p className="panel-item">还没看清这一幕，等下一帧画面…</p>
          ) : (
            <>
              <div className="game-companion__meta">
                <span>{phase === null ? "未知阶段" : PHASE_LABELS[phase]}</span>
                {observation.speaker !== null && <span>{observation.speaker}</span>}
              </div>
              {observation.scene_summary !== null && (
                <p className="game-companion__summary">{observation.scene_summary}</p>
              )}
              {observation.dialogue.length > 0 && (
                <div className="game-companion__dialogue">
                  {observation.dialogue.map((block) => (
                    <p key={block.id}>{block.text}</p>
                  ))}
                </div>
              )}
              {observation.choices.length > 0 && (
                <div className="game-companion__choices">
                  {observation.choices.map((choice) => (
                    <button
                      key={choice.id}
                      type="button"
                      className="reading-btn"
                      onClick={() => void confirmChoice(choice.id)}
                    >
                      {choice.text}
                    </button>
                  ))}
                </div>
              )}
              {observation.uncertainties.length > 0 && (
                <p className="panel-item__meta">还不确定：{observation.uncertainties.join("、")}</p>
              )}
            </>
          )}
        </div>
      )}
    </Panel>
  );
}
