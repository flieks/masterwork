import { useState } from 'react';
import { useAtom } from 'jotai';
import type { SessionLaunchListItem } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { Input } from '~/components/ui/input';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { sessionLaunchesQueryAtom, submitInterviewAnswersMutationAtom } from '../queries';

/**
 * Any run currently waiting on the plan's questions, rendered as a form —
 * mounted outside the Radix tabs (SessionsListPage) so a run waiting on the
 * user is never hidden behind whichever tab happens to be open.
 */
export function InterviewQuestions() {
  const [{ data, isPending, isError }] = useAtom(sessionLaunchesQueryAtom);
  if (isPending || isError || !data) return null;

  const waiting = data.filter((launch) => launch.interview?.state === 'waiting');
  if (waiting.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      {waiting.map((launch) => (
        <WaitingLaunchCard key={launch.id} launch={launch} />
      ))}
    </div>
  );
}

function WaitingLaunchCard({ launch }: { launch: SessionLaunchListItem }) {
  const questions = launch.interview?.questions ?? [];
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [{ mutateAsync: submitAnswers, isPending: isSubmitting }] = useAtom(
    submitInterviewAnswersMutationAtom,
  );

  const allFilled = questions.every((q) => (answers[q.id] ?? '').trim().length > 0);
  const canSubmit = allFilled && !isSubmitting;

  async function submit() {
    if (!canSubmit) return;
    try {
      await submitAnswers({
        launchId: launch.id,
        body: {
          answers: questions.map((q) => ({ id: q.id, answer: (answers[q.id] ?? '').trim() })),
        },
      });
      toast.success('Run resumed', { description: launch.project_path });
    } catch (err) {
      toast.error('Could not submit answers', { description: apiErrorMessage(err) });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Waiting for answers</CardTitle>
        <CardDescription>{launch.request_text}</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          {questions.map((q) => (
            <div key={q.id} className="space-y-1.5">
              <label htmlFor={`interview-${launch.id}-${q.id}`} className="text-sm font-medium">
                {q.question}
              </label>
              <Input
                id={`interview-${launch.id}-${q.id}`}
                value={answers[q.id] ?? ''}
                onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
                required
              />
            </div>
          ))}
          <Button type="submit" disabled={!canSubmit}>
            {isSubmitting ? 'Submitting…' : 'Answer and continue'}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
