import { useEffect, useState } from "react";

// 打字机（03-chat-panel §5）：text 逐字显示，返回可见文本 + 是否打完。
// 纯渲染层——消息仍完整 append 到 store，这里只控制「显示到第几个字」，
// 不碰数据流、不碰 isReplying/超时 timer 生命周期。
export function useTypewriter(
  text: string,
  speed = 35,
): { displayed: string; done: boolean } {
  const [count, setCount] = useState(0);

  useEffect(() => {
    setCount(0);
    if (text === "") return;
    let alive = true;
    let i = 0;
    let timer: ReturnType<typeof setTimeout>;
    const step = () => {
      if (!alive) return;
      i += 1;
      setCount(i);
      if (i < text.length) timer = setTimeout(step, speed);
    };
    timer = setTimeout(step, speed);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [text, speed]);

  return { displayed: text.slice(0, count), done: count >= text.length };
}
