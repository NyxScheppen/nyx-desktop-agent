import { useEffect, useState } from "react";
import { getCharacterCard, getCharacterAsset, type CharacterCardDetail } from "../../api/client";
import { useConnectionStore } from "../../stores/connectionStore";
import { INNER_SECTIONS } from "../../lib/innerDocument";
import { EMOTION_LABELS } from "../../lib/labels";
import { isEmotionCategory } from "../../types/api";

function CardImage({ path, label }: { path: string; label: string }) {
  const token = useConnectionStore((s) => s.token);
  const [image, setImage] = useState<{ path: string; token: string; url: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let url: string | undefined;
    void getCharacterAsset(path, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setImage({ path, token, url });
    }).catch(() => { /* The text label remains available when an asset is unavailable. */ });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [path, token]);
  return <figure>{image?.path === path && image.token === token && <img src={image.url} alt={label} />}<figcaption>{label}</figcaption></figure>;
}

function CardText({ text }: { text: string }) {
  return <div className="character-card__prose">{text.split(/\n\s*\n/).map((paragraph, index) => {
    const heading = paragraph.match(/^#{1,6}\s+([^\n]+)$/);
    return heading ? <h4 key={index}>{heading[1]}</h4> : <p key={index}>{paragraph}</p>;
  })}</div>;
}

export default function CharacterCardView({ id }: { id: string }) {
  const token = useConnectionStore((s) => s.token);
  const [card, setCard] = useState<CharacterCardDetail | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [page, setPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setCard(null);
    setError("");
    setPage(0);
    void getCharacterCard(id, controller.signal).then((value) => {
      if (!controller.signal.aborted) setCard(value);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, [id, token, retry]);
  if (error) return <div role="alert"><p>{error}</p><button type="button" className="bg-panel__btn" onClick={() => setRetry(retry + 1)}>重新读取角色卡</button></div>;
  if (!card) return <p role="status">正在读取角色卡…</p>;
  return <article className="character-card" aria-label={`${card.name} 的初始角色卡`}>
    <header className="character-card__heading"><h3>{card.name}</h3><span>{card.id} · v{card.schema_version}</span></header>
    <p className="panel-item__meta">初始角色设定；相处中形成的变化可在「内在状态」查看。</p>
    <nav className="character-card__pages" aria-label="角色卡分页">
      {["初始设定", "身份与表达约定", "角色参考资料"].map((label, index) => <button
        key={label} type="button" aria-current={page === index ? "page" : undefined}
        onClick={() => setPage(index)}>{label}</button>)}
    </nav>
    {page === 0 && <section aria-label="初始设定">
    <div className="character-card__topics">{Object.entries(INNER_SECTIONS).map(([key, label]) => {
      const topics = card.inner_document.filter((topic) => topic.section === key);
      return topics.length > 0 && <section key={key}><h3>{label}</h3>{topics.map((topic) => <div key={topic.id}><h4>{topic.title}</h4><p>{topic.content}</p></div>)}</section>;
    })}</div>
    {card.long_term_desires.length > 0 && <section><h3>初始长期欲望</h3>{card.long_term_desires.map((desire, index) => <div key={index}>
      <h4>{desire.name}</h4><p>{desire.description}</p>
      {desire.subtopics.length > 0 && <p className="panel-item__meta">{desire.subtopics.join(" · ")}</p>}
    </div>)}</section>}
    {Object.keys(card.assets).length > 0 && <section><h3>头像与表情</h3><div className="character-card__images">{Object.keys(card.assets).map((key) =>
      <CardImage key={key} path={`/api/characters/${encodeURIComponent(id)}/assets/${encodeURIComponent(key)}`} label={key === "avatar" ? "头像" : isEmotionCategory(key) ? EMOTION_LABELS[key] : key} />)}</div></section>}
    </section>}
    {page === 1 && <section aria-label="身份与表达约定"><CardText text={card.canon} /></section>}
    {page === 2 && <section aria-label="角色参考资料">{card.character_reference
      ? <CardText text={card.character_reference} /> : <p className="panel-item__meta">此角色卡未附参考资料。</p>}</section>}
  </article>;
}
