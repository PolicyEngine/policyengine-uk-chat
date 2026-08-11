export interface PromiseTail {
  current: Promise<void>;
}

export function enqueueSerial<T>(
  tail: PromiseTail,
  task: () => Promise<T>,
): Promise<T> {
  const queued = tail.current.then(task, task);
  tail.current = queued.then(() => undefined, () => undefined);
  return queued;
}
