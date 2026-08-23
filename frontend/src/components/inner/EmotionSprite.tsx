import { useInnerLifeStore } from "../../stores/innerLifeStore";
import type { EmotionCategory } from "../../types/api";

// 8 情绪 sprite：文件名 = EmotionCategory 值，1:1 映射（04-inner-state-panel §2），无 switch 分支。
// import.meta.glob 免 8 行静态 import；键 = 相对本文件的路径。
const SPRITES = import.meta.glob("../../assets/sprites/*.png", {
  eager: true,
  import: "default",
}) as Record<string, string>;

type EmotionSpriteProps = {
  size?: "small" | "large" | "portrait"; // 气泡小图（03 §3）/ 内在面板大图（04 §1）/ 半身像立绘（视觉改造 §2）
  emotion?: EmotionCategory; // 可选覆盖：Avatar 戳立绘/昼夜节律传临时情绪；缺省读 current.emotion
};

// 情绪的唯一视觉载体：默认读 current.emotion → assets/sprites/{emotion}.png。
// emotion 传入则覆盖（临时情绪）；current === null 且未传 emotion 时返回 null，不崩（03 §3）。
export default function EmotionSprite({ size = "large", emotion }: EmotionSpriteProps) {
  const current = useInnerLifeStore((s) => s.current?.emotion);
  const resolved = emotion ?? current;
  if (resolved === undefined) return null;
  const src = SPRITES[`../../assets/sprites/${resolved}.png`];
  if (src === undefined) return null;
  return (
    <img className={`emotion-sprite emotion-sprite--${size}`} src={src} alt={resolved} />
  );
}
