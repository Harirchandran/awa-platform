export function TypingDots() {
  return (
    <span className="inline-flex items-center gap-[5px] px-1 py-1" aria-label="AWA is typing">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="typing-dot h-[6px] w-[6px] rounded-full bg-emerald-400"
          style={{ animationDelay: `${i * 160}ms` }}
        />
      ))}
    </span>
  );
}