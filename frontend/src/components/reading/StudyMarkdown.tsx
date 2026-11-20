import { Fragment, type ReactNode } from "react";

// React text nodes escape HTML. Links have an explicit protocol allowlist.
function inline(text: string, onSource?: (index: number) => void): ReactNode[] {
  const pattern =
    /(\[[^\]\n]+\]\([^\s)]+\)|`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
  return text.split(pattern).map((part, index) => {
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
    if (link) {
      const source = /^nyx-book:(\d+)$/.exec(link[2]);
      if (source && onSource)
        return (
          <button
            key={index}
            className="study-source"
            onClick={() => onSource(Number(source[1]))}
          >
            {link[1]}
          </button>
        );
      if (/^(https?:\/\/|mailto:)/i.test(link[2]))
        return (
          <a
            key={index}
            href={link[2]}
            target="_blank"
            rel="noopener noreferrer"
          >
            {link[1]}
          </a>
        );
      return link[1];
    }
    if (part.startsWith("**") && part.endsWith("**"))
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*"))
      return <em key={index}>{part.slice(1, -1)}</em>;
    if (part.startsWith("`") && part.endsWith("`"))
      return <code key={index}>{part.slice(1, -1)}</code>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

export function markdownBlocks(text: string): string[] {
  const blocks: string[] = [];
  let lines: string[] = [];
  let fenced = false;
  for (const line of text.split("\n")) {
    if (/^\s*```/.test(line)) fenced = !fenced;
    if (!line.trim() && !fenced) {
      if (lines.length) {
        blocks.push(lines.join("\n"));
        lines = [];
      }
    } else lines.push(line);
  }
  if (lines.length) blocks.push(lines.join("\n"));
  return blocks.length ? blocks : [""];
}

export default function StudyMarkdown({
  text,
  onSource,
}: {
  text: string;
  onSource?: (index: number) => void;
}) {
  return (
    <div className="study-markdown">
      {markdownBlocks(text).map((block, index) => {
        if (block.startsWith("```"))
          return (
            <pre key={index}>
              <code>
                {block.replace(/^```[^\n]*\n?/, "").replace(/\n?```$/, "")}
              </code>
            </pre>
          );
        const heading = /^(#{1,6})\s+([\s\S]*)$/.exec(block);
        if (heading)
          return (
            <div
              key={index}
              role="heading"
              aria-level={heading[1].length}
              className={`study-h study-h--${heading[1].length}`}
            >
              {inline(heading[2], onSource)}
            </div>
          );
        if (/^>\s?/.test(block))
          return (
            <blockquote key={index}>
              {inline(block.replace(/^>\s?/gm, ""), onSource)}
            </blockquote>
          );
        const lines = block.split("\n");
        if (lines.every((line) => /^[-*+]\s+/.test(line)))
          return (
            <ul key={index}>
              {lines.map((line, i) => (
                <li key={i}>
                  {inline(line.replace(/^[-*+]\s+/, ""), onSource)}
                </li>
              ))}
            </ul>
          );
        if (lines.every((line) => /^\d+[.)]\s+/.test(line)))
          return (
            <ol key={index}>
              {lines.map((line, i) => (
                <li key={i}>
                  {inline(line.replace(/^\d+[.)]\s+/, ""), onSource)}
                </li>
              ))}
            </ol>
          );
        return <p key={index}>{inline(block, onSource)}</p>;
      })}
    </div>
  );
}
