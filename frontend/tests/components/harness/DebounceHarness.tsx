import { useState } from 'react';
import { useDebouncedValue } from '~/lib/hooks';

/** Renders the raw input and its debounced echo so a test can observe timing. */
export function DebounceHarness({ delay = 300 }: { delay?: number }) {
  const [raw, setRaw] = useState('');
  const debounced = useDebouncedValue(raw, delay);
  return (
    <div>
      <input aria-label="raw" value={raw} onChange={(e) => setRaw(e.target.value)} />
      <div data-testid="debounced">{debounced}</div>
    </div>
  );
}
