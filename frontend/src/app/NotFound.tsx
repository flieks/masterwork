import { Link } from 'react-router-dom';
import { Button } from '~/components/ui/button';

export function NotFound() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-10 text-center">
      <div>
        <p className="text-4xl font-semibold">404</p>
        <p className="mt-1 text-sm text-muted-foreground">This page doesn’t exist.</p>
      </div>
      <Button asChild variant="outline">
        <Link to="/skills">Go to Skills</Link>
      </Button>
    </div>
  );
}
