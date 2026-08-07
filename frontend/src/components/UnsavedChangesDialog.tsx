import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '~/components/ui/alert-dialog';

interface UnsavedChangesDialogProps {
  open: boolean;
  onDiscard: () => void;
  onKeepEditing: () => void;
}

/** Confirm before a react-router blocker discards in-progress edits. */
export function UnsavedChangesDialog({
  open,
  onDiscard,
  onKeepEditing,
}: UnsavedChangesDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={(next) => (!next ? onKeepEditing() : undefined)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
          <AlertDialogDescription>
            You have unsaved edits. Leaving this page will discard them.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onKeepEditing}>Keep editing</AlertDialogCancel>
          <AlertDialogAction
            onClick={onDiscard}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            Discard
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
