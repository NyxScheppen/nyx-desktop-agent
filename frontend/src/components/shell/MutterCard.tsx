import { useMutterStore } from "../../stores/mutterStore";

// 左栏下方常驻「碎碎念」小卡片：显示 Nyx 最近 3 条碎碎念（最新在前），
// 无碎碎念时显示占位。数据源是 mutterStore（mutter 不再进聊天历史）。
export default function MutterCard() {
  const mutters = useMutterStore((s) => s.mutters);
  const recent = mutters.slice(-3).reverse();

  return (
    <div className="mutter-card">
      <span className="mutter-card__title">碎碎念</span>
      {recent.length === 0 ? (
        <p className="mutter-card__empty">尼克斯安静地陪着你……</p>
      ) : (
        <ul className="mutter-card__list">
          {recent.map((m) => (
            <li key={m.id} className="mutter-card__item">
              {m.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
