interface KnowledgeMetricsCardsProps {
  loaded: number;
  active: number;
  disabled: number;
  sourceTotal: number;
  websiteCount: number;
  documentCount: number;
}

export function KnowledgeMetricsCards({
  loaded,
  active,
  disabled,
  sourceTotal,
  websiteCount,
  documentCount,
}: KnowledgeMetricsCardsProps) {
  const cards = [
    { label: 'Loaded entries', value: loaded, helper: 'Current page count' },
    { label: 'Active for AWA', value: active, helper: 'Enabled entries loaded' },
    { label: 'Disabled', value: disabled, helper: 'Not used in replies' },
    {
      label: 'Imported sources',
      value: websiteCount + documentCount,
      helper: sourceTotal ? `${sourceTotal} total by source` : 'No source totals yet',
    },
  ];

  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Knowledge overview">
      {cards.map((card) => (
        <div key={card.label} className="rounded-md border bg-card p-4">
          <p className="text-[12px] font-medium text-muted-foreground">{card.label}</p>
          <p className="mt-2 text-[24px] font-semibold leading-none text-foreground">{card.value}</p>
          <p className="mt-2 text-[12px] text-muted-foreground">{card.helper}</p>
        </div>
      ))}
    </section>
  );
}
