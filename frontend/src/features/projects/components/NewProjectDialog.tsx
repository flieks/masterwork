import { useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Button } from '~/components/ui/button';
import { Input } from '~/components/ui/input';
import { Textarea } from '~/components/ui/textarea';
import { toast } from '~/components/ui/sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '~/components/ui/dialog';
import { apiErrorMessage } from '~/api/client';
import { createProjectMutationAtom, projectDetailPath } from '../queries';

interface NewProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function NewProjectDialog({ open, onOpenChange }: NewProjectDialogProps) {
  const [{ mutateAsync: create, isPending }] = useAtom(createProjectMutationAtom);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');

  const canCreate = name.trim().length > 0 && !isPending;

  function reset() {
    setName('');
    setGoal('');
  }

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      const project = await create({ name: trimmed, goal: goal.trim() || undefined });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      onOpenChange(false);
      reset();
      navigate(projectDetailPath(project.id));
    } catch (err) {
      toast.error('Could not create project', { description: apiErrorMessage(err) });
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>
            A workspace for a scenario your skills and agents should support.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <div className="space-y-1.5">
            <label htmlFor="project-name" className="text-sm font-medium">
              Name
            </label>
            <Input
              id="project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. New repo → Azure + Clerk + Vercel"
              autoFocus
              required
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="project-goal" className="text-sm font-medium">
              Goal <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <Textarea
              id="project-goal"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Describe the scenario in markdown…"
              rows={4}
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canCreate}>
              {isPending ? 'Creating…' : 'Create project'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
