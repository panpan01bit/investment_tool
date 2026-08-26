/** Cell helpers shared across tables/lists. Kept separate to avoid import cycles. */

export function DirectionCell({
  direction,
  title,
}: {
  direction: number;
  title?: string;
}): JSX.Element {
  if (direction > 0)
    return (
      <span className="dir dir-up" title={title ?? '看多'}>
        ▲
      </span>
    );
  if (direction < 0)
    return (
      <span className="dir dir-down" title={title ?? '看空'}>
        ▼
      </span>
    );
  return (
    <span className="dir dir-flat" title={title ?? '中性'}>
      ●
    </span>
  );
}
