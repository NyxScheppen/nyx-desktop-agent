import { useEffect, useState } from "react";

// 打字机（03-chat-panel §5）：text 逐字显示，返回可见文本 + 是否打完。
// 纯渲染层——消息仍完整 append 到 store，这里只控制「显示到第几个字」，
// 不碰数据流、不碰 isReplying/超时 timer 生命周期。
// ready=false 时不启动（串行逐字：speak/ask 等前置 think 打完才开打），
// 期间 displayed="" 且 done=false（无光标）；ready 转 true 才从 0 开始逐字。
export function useTypewriter(
  text: string,
  speed = 35,
  ready = true,
): { displayed: string; done: boolean } {
  const [count, setCount] = useState(0);

  useEffect(() => {
    setCount(0);
    if (text === "" || !ready) return;
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
  }, [text, speed, ready]);

  // ready 未就绪时 done 恒 false：done 是「是否已打完」，未开打不算打完。
  // count 未就绪时被 useEffect 复位为 0，displayed 天然为空串。
  const done = ready && count >= text.length;
  return { displayed: text.slice(0, count), done };
}
