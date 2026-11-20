import innerLifePolicy from "../../../../nyx/settings/inner_life.json";
import characterPolicy from "../../../../nyx/settings/character.json";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useCharacterStore } from "../../stores/characterStore";
import { INNER_SECTIONS } from "../../lib/innerDocument";
import type { InnerTopic } from "../../types/api";
import Panel from "./Panel";

const IMAGE_LABELS: Record<string, string> = characterPolicy.frontend.CharacterCreator.IMAGE_LABELS as Record<string, string>;

export default function CharacterCreator({ onBack }: { onBack: () => void }) {
  const create = useCharacterStore((s) => s.create);
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [topics, setTopics] = useState<InnerTopic[]>([
    { section: "identity", id: "self-understanding", title: characterPolicy.copy.CharacterCreator.title, content: "" },
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
      setError(characterPolicy.copy.CharacterCreator.text); return;
    }
    if (new Set(normalized.map((topic) => topic.id)).size !== normalized.length) {
      setError(characterPolicy.copy.CharacterCreator.text_2); return;
    }
    if (normalized.reduce((sum, topic) => sum + Array.from(topic.id + topic.title + topic.content).length, 0) > 24000) {
      setError(characterPolicy.copy.CharacterCreator.text_3); return;
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

  return <Panel title={characterPolicy.copy.CharacterCreator.title_2} className="character-creator">
    <div className="character-creator__heading">
      <button type="button" className="reader-tool" disabled={submitting} onClick={onBack}>{characterPolicy.copy.CharacterCreator.button}</button>
      <p>{characterPolicy.copy.CharacterCreator.p}</p>
    </div>
    <form className="character-manager__form" onSubmit={(event) => void submit(event)}>
      <fieldset disabled={submitting}>
        <legend>{characterPolicy.copy.CharacterCreator.legend}</legend>
        <div className="character-creator__identity">
          <label>{characterPolicy.copy.CharacterCreator.label}<input required pattern="[a-z][a-z0-9_-]{0,63}" placeholder={characterPolicy.copy.CharacterCreator.label} value={id} onChange={(event) => setId(event.target.value)} /></label>
          <label>{characterPolicy.copy.CharacterCreator.label_2}<input required maxLength={characterPolicy.limits.CardManifest_name_max_length} placeholder={characterPolicy.copy.CharacterCreator.label_2} value={name} onChange={(event) => setName(event.target.value)} /></label>
        </div>
      </fieldset>
      <fieldset disabled={submitting}>
        <legend>{characterPolicy.copy.CharacterCreator.legend_2}</legend>
        {topics.map((topic, index) => <fieldset key={index} className="character-creator__topic">
          <legend>{characterPolicy.copy.CharacterCreator.legend_3}{index + 1}</legend>
          <div className="character-creator__identity">
            <label>{characterPolicy.copy.CharacterCreator.label_3}<select value={topic.section} onChange={(event) => updateTopic(index, { section: event.target.value })}>
              {Object.entries(INNER_SECTIONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select></label>
            <label>{characterPolicy.copy.CharacterCreator.label_4}<input required pattern="[a-z][a-z0-9-]{0,79}" value={topic.id} onChange={(event) => updateTopic(index, { id: event.target.value })} /></label>
          </div>
          <label>{characterPolicy.copy.CharacterCreator.label_5}<input required maxLength={characterPolicy.limits.CardManifest_name_max_length} value={topic.title} onChange={(event) => updateTopic(index, { title: event.target.value })} /></label>
          <div className="character-creator__content">
            <label htmlFor={`character-topic-content-${index}`}>{characterPolicy.copy.CharacterCreator.label_6}</label>
            <textarea id={`character-topic-content-${index}`} required maxLength={innerLifePolicy.document.MAX_TOPIC_CHARS} placeholder={characterPolicy.copy.CharacterCreator.placeholder} value={topic.content} onChange={(event) => updateTopic(index, { content: event.target.value })} />
          </div>
          {topics.length > 1 && <button type="button" className="reader-tool" onClick={() => setTopics((current) => current.filter((_, i) => i !== index))}>{characterPolicy.copy.CharacterCreator.button_2}{index + 1}</button>}
        </fieldset>)}
        <button type="button" className="bg-panel__btn" disabled={topics.length >= 64} onClick={() => setTopics((current) => {
          let number = current.length + 1;
          while (current.some((topic) => topic.id === `topic-${number}`)) number += 1;
          return [...current, { section: "personality", id: `topic-${number}`, title: "", content: "" }];
        })}>{characterPolicy.copy.CharacterCreator.button_3}</button>
      </fieldset>
      <fieldset disabled={submitting}>
        <legend>{characterPolicy.copy.CharacterCreator.legend_4}</legend>
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
      <button type="submit" className="bg-panel__btn" disabled={submitting}>{submitting ? characterPolicy.copy.CharacterCreator.text_4 : characterPolicy.copy.CharacterCreator.text_5}</button>
    </form>
  </Panel>;
}
