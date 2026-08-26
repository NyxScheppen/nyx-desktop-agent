import { useRef, useState } from "react";
import EmotionSprite from "./EmotionSprite";
import { useAnnounceStore } from "../../stores/announceStore";
import { useChatStore } from "../../stores/chatStore";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import type { EmotionCategory } from "../../types/api";

// 昼夜节律：夜间（22:00–06:00）默认困倦，白天回落当前情绪。
// 抽成纯函数便于测试（Avatar 内用 new Date().getHours() 传参）。
export function isNight(hour: number): boolean {
  return hour >= 22 || hour < 6;
}

// 戳立绘短语（借鉴 nyx_desktop_agent DockAvatar）：连续戳害羞，戳多了生气。
const SHY_PHRASES = ["呀！", "别戳啦……", "呜，好痒……"];
const ANGRY_PHRASES = ["不要再戳了啦！", "小狐狸我呀，要生气了！"];
const ANGRY_THRESHOLD = 5; // 连续戳 ≥5 次生气
const POKE_RESET_MS = 1500; // 停手 1.5s 后戳计数 + 临时情绪复位

// 头像立绘（常驻左栏）：半身像立绘 + 三项交互增强——
// 1) 戳立绘：点击临时害羞/生气 + announce 冒一句；
// 2) 红点通知：搭话（initiate_chat）时挂「有话对你说」徽标，点击清除；
// 3) 昼夜节律：夜间默认 sleepy。
export default function Avatar() {
  const emotion = useInnerLifeStore((s) => s.current?.emotion);
  const unreadProactive = useChatStore((s) => s.unreadProactive);
  const clearUnreadProactive = useChatStore((s) => s.clearUnreadProactive);
  const announce = useAnnounceStore((s) => s.announce);

  const [pokeEmotion, setPokeEmotion] = useState<EmotionCategory | null>(null);
  const pokeCount = useRef(0);
  const pokeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handlePoke = () => {
    pokeCount.current += 1;
    if (pokeTimer.current !== null) clearTimeout(pokeTimer.current);
    pokeTimer.current = setTimeout(() => {
      pokeCount.current = 0;
      setPokeEmotion(null);
      pokeTimer.current = null;
    }, POKE_RESET_MS);

    if (pokeCount.current >= ANGRY_THRESHOLD) {
      setPokeEmotion("angry");
      announce("mutter", ANGRY_PHRASES[(pokeCount.current - ANGRY_THRESHOLD) % ANGRY_PHRASES.length]);
    } else {
      setPokeEmotion("shy");
      announce("mutter", SHY_PHRASES[(pokeCount.current - 1) % SHY_PHRASES.length]);
    }
  };

  const displayed = pokeEmotion ?? (isNight(new Date().getHours()) ? "sleepy" : emotion);

  return (
    <div className="avatar" onClick={handlePoke} title="戳一戳">
      <EmotionSprite size="portrait" emotion={displayed} />
      {unreadProactive && (
        <button
          type="button"
          className="avatar-notice"
          onClick={(e) => {
            e.stopPropagation();
            clearUnreadProactive();
          }}
        >
          小狐狸我有话对你说
        </button>
      )}
    </div>
  );
}
