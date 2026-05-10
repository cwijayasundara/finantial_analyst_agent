import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { renderInline } from "@/lib/wikilinks";

const _PATTERN = /\[\[([a-zA-Z0-9_]+)\]\]/g;

export function MarkdownView({ body }: { body: string }) {
  const segments = body.split(/(?=\[\[)|(?<=\]\])/g);
  return (
    <article className="prose-pfh">
      {segments.map((seg, i) =>
        _PATTERN.test(seg)
          ? <InlineSegment key={i} text={seg} />
          : <ReactMarkdown
              key={i}
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ children, ...rest }) => (
                  <a {...rest} className="text-info hover:underline">{children}</a>
                ),
              }}
            >{seg}</ReactMarkdown>
      )}
    </article>
  );
}

function InlineSegment({ text }: { text: string }) {
  _PATTERN.lastIndex = 0;
  return <span>{renderInline(text)}</span>;
}
