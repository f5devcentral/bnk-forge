/**
 * Best-effort parser turning a request/response body pair into a chat
 * transcript. Handles OpenAI-style chat payloads (`messages[]`,
 * `choices[].message`) and degrades gracefully to a single raw turn when the
 * body isn't JSON or has an unknown shape.
 */

export interface TranscriptTurn {
  role: string;
  content: string;
}

function safeParse(raw: string): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function contentToText(content: unknown): string {
  if (typeof content === 'string') return content;
  // OpenAI multimodal content: array of { type, text } parts.
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === 'string') return part;
        if (part && typeof part === 'object' && 'text' in part) {
          return String((part as { text: unknown }).text ?? '');
        }
        return '';
      })
      .filter(Boolean)
      .join('\n');
  }
  if (content == null) return '';
  return JSON.stringify(content);
}

export function parseTranscript(reqBody: string, respBody: string): TranscriptTurn[] {
  const turns: TranscriptTurn[] = [];

  const req = safeParse(reqBody) as { messages?: Array<{ role?: string; content?: unknown }> } | null;
  if (req?.messages && Array.isArray(req.messages)) {
    for (const m of req.messages) {
      turns.push({ role: m.role ?? 'user', content: contentToText(m.content) });
    }
  } else if (reqBody) {
    turns.push({ role: 'request', content: reqBody });
  }

  const resp = safeParse(respBody) as {
    choices?: Array<{ message?: { role?: string; content?: unknown } }>;
  } | null;
  if (resp?.choices && Array.isArray(resp.choices)) {
    for (const c of resp.choices) {
      if (c.message) {
        turns.push({ role: c.message.role ?? 'assistant', content: contentToText(c.message.content) });
      }
    }
  } else if (respBody) {
    turns.push({ role: 'response', content: respBody });
  }

  return turns;
}
