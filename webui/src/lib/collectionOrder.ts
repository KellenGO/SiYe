/** Move one custom folder before or after another without changing the input order. */
export function moveCollection(ids: number[], source: number, target: number, after: boolean): number[] {
  if (source === target || !ids.includes(source) || !ids.includes(target)) return ids;
  const next = ids.filter((id) => id !== source);
  const targetIndex = next.indexOf(target);
  next.splice(targetIndex + (after ? 1 : 0), 0, source);
  return next;
}
