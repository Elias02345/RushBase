import { EmptyState, Skeleton } from "@rushbase/ui";
import { ScanSearch } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useSimilar } from "../../api/aiQueries";
import { ApiError } from "../../api/client";
import { HitCard } from "../library/HitCard";

/** "Find similar": nearest neighbours of an asset or shot by the stored image vectors. */
export function SimilarPanel({ kind, id }: { kind: "asset" | "shot"; id: string }) {
  const { t } = useTranslation();
  const query = useSimilar(kind, id);
  if (query.isLoading) return <Skeleton height={180} radius={16} />;
  if (query.isError) {
    const missing = query.error instanceof ApiError && query.error.status === 404;
    return (
      <EmptyState
        compact
        icon={<ScanSearch size={24} />}
        title={missing ? t("similar.noVectorsTitle") : t("library.errorTitle")}
        description={missing ? t("similar.noVectorsText") : t("library.errorText")}
      />
    );
  }
  const items = query.data?.items ?? [];
  if (items.length === 0) {
    return (
      <EmptyState
        compact
        icon={<ScanSearch size={24} />}
        title={t("similar.emptyTitle")}
        description={t("similar.emptyText")}
      />
    );
  }
  return (
    <ul className="hit-grid" aria-label={t("similar.title")}>
      {items.map((hit) => (
        <HitCard key={`${hit.asset.id}-${hit.shot?.id ?? ""}`} hit={hit} />
      ))}
    </ul>
  );
}
