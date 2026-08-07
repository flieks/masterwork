import { test, expect } from '@playwright/experimental-ct-react';
// Pure-logic test (no mount): import relatively so it resolves in the Node worker.
import { dayLabelsFor } from '../../src/features/chat/separators';

test('labels the first message and every day boundary, nothing in between', () => {
  const labels = dayLabelsFor([
    '2026-07-14T12:00:00Z',
    '2026-07-14T13:00:00Z', // same day as previous
    '2026-07-15T12:00:00Z', // new day
  ]);

  expect(labels[0]).not.toBeNull(); // first is always labelled
  expect(labels[1]).toBeNull(); // same day → no separator
  expect(labels[2]).not.toBeNull(); // day changed → separator
  expect(labels[0]).not.toEqual(labels[2]);
});

test('empty history yields no labels', () => {
  expect(dayLabelsFor([])).toEqual([]);
});
