import { useEffect, useRef, useState, type FormEvent } from "react";
import { useCharacterStore } from "../../stores/characterStore";
import { INNER_SECTIONS } from "../../lib/innerDocument";
import type { InnerTopic } from "../../types/api";
import Panel from "./Panel";

const IMAGE_LABELS: Record<string, string> = {
  avatar: "头像", neutral: "平静", happy: "开心", sad: "悲伤", angry: "生气",
  worried: "担忧", shy: "害羞", sleepy: "困倦", thinking: "思考",
};

export default function CharacterCreator({ onBack }: { onBack: () => void }) {
  const create = useCharacterStore((s) => s.create);
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [topics, setTopics] = useState<InnerTopic[]>([
    { section: "identity", id: "self-understanding", title: "自我理解", content: "" },
  ]);
  const [images, setImages] = useState<Record<string, File>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const updateTopic = (index: number, patch: Partial<InnerTopic>) =>
    setTopics((current) => current.map((topic, i) => i === index ? { ...topic, ...patch } : topic));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    const normalized = topics.map((topic) => ({ ...topic, title: topic.title.trim(), content: topic.content.trim() }));
    if (!name.trim() || normalized.some((topic) => !topic.title || !topic.content)) {
      setError("名称、主题标题和正文不能为空"); return;
    }
    if (new Set(normalized.map((topic) => topic.id)).size !== normalized.length) {
      setError("主题 ID 不能重复"); return;
    }
    if (normalized.reduce((sum, topic) => sum + Array.from(topic.id + topic.title + topic.content).length, 0) > 24000) {
      setError("内在文档总长度不能超过 24000 字"); return;
    }
    pending.current = true;
    setSubmitting(true);
    setError("");
    try {
      await create({ id, name: name.trim(), inner_document: normalized }, Object.entries(images).map(([key, file]) =>
        new File([file], `${key}.${file.name.split(".").pop() || "png"}`, { type: file.type })));
      if (mounted.current) onBack();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  }

  return <Panel title="新建角色卡" className="character-creator">
    <div className="character-creator__heading">
      <button type="button" className="reader-tool" disabled={submitting} onClick={onBack}>← 返回角色管理</button>
      <p>按主题填写初始内在文档；角色会在相处中继续成长。</p>
    </div>
    <form className="character-manager__form" onSubmit={(event) => void submit(event)}>
      <fieldset disabled={submitting}>
        <legend>基本信息</legend>
        <div className="character-creator__identity">
          <label>角色 ID<input required pattern="[a-z][a-z0-9_-]{0,63}" placeholder="角色 ID" value={id} onChange={(event) => setId(event.target.value)} /></label>
          <label>角色名称<input required maxLength={100} placeholder="角色名称" value={name} onChange={(event) => setName(event.target.value)} /></label>
        </div>
      </fieldset>
      <fieldset disabled={submitting}>
        <legend>初始内在文档</legend>
        {topics.map((topic, index) => <fieldset key={index} className="character-creator__topic">
          <legend>主题 {index + 1}</legend>
          <div className="character-creator__identity">
            <label>所属部分<select value={topic.section} onChange={(event) => updateTopic(index, { section: event.target.value })}>
              {Object.entries(INNER_SECTIONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select></label>
            <label>主题 ID<input required pattern="[a-z][a-z0-9-]{0,79}" value={topic.id} onChange={(event) => updateTopic(index, { id: event.target.value })} /></label>
          </div>
          <label>主题标题<input required maxLength={100} value={topic.title} onChange={(event) => updateTopic(index, { title: event.target.value })} /></label>
          <div className="character-creator__content">
            <label htmlFor={`character-topic-content-${index}`}>主题正文</label>
            <textarea id={`character-topic-content-${index}`} required maxLength={6000} placeholder="这个角色如何理解自己、世界或他人……" value={topic.content} onChange={(event) => updateTopic(index, { content: event.target.value })} />
          </div>
          {topics.length > 1 && <button type="button" className="reader-tool" onClick={() => setTopics((current) => current.filter((_, i) => i !== index))}>删除主题 {index + 1}</button>}
        </fieldset>)}
        <button type="button" className="bg-panel__btn" disabled={topics.length >= 64} onClick={() => setTopics((current) => {
          let number = current.length + 1;
          while (current.some((topic) => topic.id === `topic-${number}`)) number += 1;
          return [...current, { section: "personality", id: `topic-${number}`, title: "", content: "" }];
        })}>添加主题</button>
      </fieldset>
      <fieldset disabled={submitting}>
        <legend>头像与表情（可选）</legend>
        <div className="character-creator__images">{Object.entries(IMAGE_LABELS).map(([key, label]) => <label key={key}>{label}
          <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => {
            const file = event.target.files?.[0];
            setImages((current) => {
              const next = { ...current };
              if (file) next[key] = file; else delete next[key];
              return next;
            });
          }} />
        </label>)}</div>
      </fieldset>
      {error && <p className="error-text" role="alert">{error}</p>}
      <button type="submit" className="bg-panel__btn" disabled={submitting}>{submitting ? "正在创建…" : "创建角色"}</button>
    </form>
  </Panel>;
}
