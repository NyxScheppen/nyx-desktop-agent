import { ANNOUNCE_DURATION, useAnnounceStore } from "../stores/announceStore";

// 头像旁临时气泡层（frontend-design §8「头像旁冒出，几秒后淡出」）：碎碎念 + 活动完成后冒一句。
// 读 announceStore.items 渲染，CSS `announce-pop` 在动画末尾淡出，届时 store 已 dismiss 摘除节点。
// pointer-events:none 不挡交互；aria-live 供屏幕阅读器朗读临时冒话。
export default function AnnounceLayer() {
  const items = useAnnounceStore((s) => s.items);
  return (
    <div className="announce-layer" aria-live="polite">
      {items.map((it) => (
        <div
          key={it.id}
          className={`announce announce--${it.kind}`}
          style={{ animationDuration: `${ANNOUNCE_DURATION[it.kind]}ms` }}
        >
          {it.text}
        </div>
      ))}
    </div>
  );
}
