/**
 * Marks a model OpenRouter no longer lists, wherever the catalogue names it.
 *
 * A retired model cannot be played, but its games are real and its page stays: hiding it hid
 * thirteen games of Qwen3.8 27B behind a URL nothing linked to. Shown mixed in with the playable
 * models rather than set apart, so a provider's group reads as its whole history here.
 */
export function RetiredBadge({ className = "" }: { className?: string }) {
  return (
    <span
      title="OpenRouter no longer lists this model, so it cannot be played. Its games stay."
      className={`inline-block flex-none border border-line px-1 py-px font-mono text-label uppercase tracking-wider text-ink-faint ${className}`}
    >
      retired
    </span>
  );
}
