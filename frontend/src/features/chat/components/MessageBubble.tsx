import { Bot } from 'lucide-react';
import type { ChatMessage } from '~/api/generated';
import { MarkdownView } from '~/components/MarkdownView';
import { clockTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import { ProposalCard } from './ProposalCard';

export function MessageBubble({ message, sessionId }: { message: ChatMessage; sessionId: string }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] space-y-1">
          <div className="whitespace-pre-wrap break-words rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
            {message.content}
          </div>
          <div className="text-right text-[11px] text-muted-foreground">
            {clockTime(message.created_at)}
          </div>
        </div>
      </div>
    );
  }

  const isError = message.role === 'error';

  return (
    <div className="flex items-start gap-3">
      <div
        className={cn(
          'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full',
          isError ? 'bg-destructive/15 text-destructive' : 'bg-muted text-muted-foreground',
        )}
      >
        <Bot className="size-4" />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div
          className={cn(
            'rounded-lg px-3 py-2',
            isError
              ? 'border border-destructive/30 bg-destructive/10 text-destructive'
              : 'bg-muted',
          )}
        >
          <MarkdownView content={message.content} />
        </div>
        {message.proposal ? (
          <ProposalCard proposal={message.proposal} sessionId={sessionId} />
        ) : null}
        <div className="text-[11px] text-muted-foreground">{clockTime(message.created_at)}</div>
      </div>
    </div>
  );
}
