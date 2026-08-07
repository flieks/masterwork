import { useEffect, useState } from 'react';
import { Bot } from 'lucide-react';

/** Assistant-side "thinking" placeholder: pulsing dots + an elapsed counter. */
export function ThinkingIndicator() {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-start gap-3" aria-live="polite">
      <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Bot className="size-4" />
      </div>
      <div className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
        <span className="inline-flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="size-1.5 animate-bounce rounded-full bg-current"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </span>
        <span>Claude is thinking{seconds > 0 ? ` · ${seconds}s` : '…'}</span>
      </div>
    </div>
  );
}
