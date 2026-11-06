import { useEffect, useState } from "react";
import { BASE_URL, getCharacterAsset } from "../../api/client";
import { useConnectionStore } from "../../stores/connectionStore";
import { useCharacterStore } from "../../stores/characterStore";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import type { EmotionCategory } from "../../types/api";

// 8 情绪 sprite：文件名 = EmotionCategory 值，1:1 映射，无 switch 分支。
// import.meta.glob 免 8 行静态 import；键 = 相对本文件的路径。
const SPRITES = import.meta.glob("../../assets/expressions/*.png", {
  eager: true,
  import: "default",
}) as Record<string, string>;

type EmotionSpriteProps = {
  size?: "large" | "circle"; // 内在面板大图（04 §1）/ 可拖拽头像圆圈（视觉改造 §4）
  emotion?: EmotionCategory; // 可选覆盖：Avatar 戳立绘/昼夜节律传临时情绪；缺省读 current.emotion
};

// 情绪的唯一视觉载体：默认读 current.emotion → assets/expressions/{emotion}.png。
// emotion 传入则覆盖（临时情绪）；current === null 且未传 emotion 时返回 null，不崩（03 §3）。
export default function EmotionSprite({ size = "large", emotion }: EmotionSpriteProps) {
  const current = useInnerLifeStore((s) => s.current?.emotion);
  const role = useCharacterStore((s) => s.registry?.characters.find((role) => role.id === s.registry?.current_id));
  const token = useConnectionStore((s) => s.token);
  const resolved = emotion ?? current;
  const asset = role?.assets[resolved ?? "neutral"] ?? role?.assets.avatar;
  const [image, setImage] = useState<{ asset: string; token: string; url: string } | null>(null);
  useEffect(() => {
    if (!asset || !token) return;
    const controller = new AbortController();
    let url: string | undefined;
    void getCharacterAsset(asset, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setImage({ asset, token, url });
    }).catch(() => { /* Missing/failed images retain the role name. */ });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [asset, token]);
  if (resolved === undefined && !role) return null;
  const src = role ? (asset ? (token
    ? (image?.asset === asset && image.token === token ? image.url : undefined)
    : BASE_URL + asset) : undefined) : SPRITES[`../../assets/expressions/${resolved}.png`];
  if (src === undefined) return <span className={`emotion-sprite emotion-sprite--${size}`}>{role?.name}</span>;
  return (
    <img
      className={`emotion-sprite emotion-sprite--${size}`}
      src={src}
      crossOrigin="anonymous"
      alt={resolved ?? role?.name}
      draggable={false}
    />
  );
}
