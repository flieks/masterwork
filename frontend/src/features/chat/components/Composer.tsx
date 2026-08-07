import type { KeyboardEvent } from 'react';
import { SendHorizontal } from 'lucide-react';
import { Textarea } from '~/components/ui/textarea';
import { Button } from '~/components/ui/button';

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled?: boolean;
}

export function Composer({ value, onChange, onSend, disabled }: ComposerProps) {
  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSend();
    }
  }

  return (
    <div className="border-t p-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={2}
          aria-label="Message"
          placeholder="Ask Claude to refine a skill or agent…  (Enter to send, Shift+Enter for newline)"
          className="max-h-40 resize-none"
        />
        <Button
          onClick={onSend}
          disabled={disabled || !value.trim()}
          size="icon"
          aria-label="Send message"
        >
          <SendHorizontal />
        </Button>
      </div>
    </div>
  );
}
