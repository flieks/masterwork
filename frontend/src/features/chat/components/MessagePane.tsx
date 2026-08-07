import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { MessageSquare, AlertTriangle } from 'lucide-react';
import type { ChatMessage } from '~/api/generated';
import { EmptyState } from '~/components/EmptyState';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { chatMessagesQueryAtom, sendMessageMutationAtom } from '../queries';
import { dayLabelsFor } from '../separators';
import { MessageBubble } from './MessageBubble';
import { DateSeparator } from './DateSeparator';
import { ThinkingIndicator } from './ThinkingIndicator';
import { AffectedAssets } from './AffectedAssets';
import { Composer } from './Composer';

export function MessagePane({ sessionId }: { sessionId: string }) {
  const [{ data: messages, isPending, isError, error }] = useAtom(chatMessagesQueryAtom(sessionId));
  const [{ mutateAsync: send }] = useAtom(sendMessageMutationAtom);
  const queryClient = useQueryClient();

  const [input, setInput] = useState('');
  const [optimistic, setOptimistic] = useState<ChatMessage | null>(null);
  const [thinking, setThinking] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const items = useMemo(() => {
    const base = messages ?? [];
    return optimistic ? [...base, optimistic] : base;
  }, [messages, optimistic]);

  const labels = useMemo(() => dayLabelsFor(items.map((m) => m.created_at)), [items]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [items.length, thinking]);

  async function handleSend() {
    const content = input.trim();
    if (!content || thinking) return;

    const optimisticMsg: ChatMessage = {
      id: `optimistic-${Date.now()}`,
      session_id: sessionId,
      role: 'user',
      content,
      proposal: null,
      created_at: new Date().toISOString(),
    };
    setOptimistic(optimisticMsg);
    setInput('');
    setThinking(true);

    try {
      const exchange = await send({ sessionId, content });
      // Reconcile: drop the optimistic entry, append the persisted pair.
      queryClient.setQueryData<ChatMessage[]>(['chatMessages', sessionId], (old) => [
        ...(old ?? []),
        exchange.user_message,
        exchange.assistant_message,
      ]);
      queryClient.invalidateQueries({ queryKey: ['chatSessions'] });
      setOptimistic(null);
    } catch (err) {
      setOptimistic(null);
      setInput(content); // restore the draft so it can be retried
      toast.error('Message failed', { description: apiErrorMessage(err) });
    } finally {
      setThinking(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-6">
          {isPending ? (
            <div className="space-y-4">
              <Skeleton className="h-16 w-2/3" />
              <Skeleton className="ml-auto h-12 w-1/2" />
              <Skeleton className="h-24 w-3/4" />
            </div>
          ) : isError ? (
            <EmptyState
              icon={<AlertTriangle className="size-8" />}
              title="Couldn't load messages"
              description={apiErrorMessage(error)}
            />
          ) : items.length === 0 && !thinking ? (
            <EmptyState
              icon={<MessageSquare className="size-8" />}
              title="Start the conversation"
              description="Ask Claude about your installed skills and agents, or request a change."
            />
          ) : (
            <div className="space-y-3">
              {items.map((message, i) => (
                <Fragment key={message.id}>
                  {labels[i] ? <DateSeparator label={labels[i]!} /> : null}
                  <MessageBubble message={message} sessionId={sessionId} />
                </Fragment>
              ))}
              {thinking ? <ThinkingIndicator /> : null}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <AffectedAssets messages={messages ?? []} />
      <Composer value={input} onChange={setInput} onSend={handleSend} disabled={thinking} />
    </div>
  );
}
