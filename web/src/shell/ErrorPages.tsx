import { Button, EmptyState } from "@rushbase/ui";
import { Link } from "@tanstack/react-router";
import { Compass, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

/** Shown for unknown URLs. */
export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <div className="center-page">
      <EmptyState
        icon={<Compass size={28} />}
        title={t("errors.notFoundTitle")}
        description={t("errors.notFoundText")}
        action={
          <Link to="/" className="rb-button rb-button--primary">
            {t("errors.backHome")}
          </Link>
        }
      />
    </div>
  );
}

/** Last-resort boundary for route errors (network down, unexpected server answer). */
export function ErrorPage({ error, reset }: { error: Error; reset: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="center-page">
      <EmptyState
        icon={<TriangleAlert size={28} />}
        title={t("errors.genericTitle")}
        description={error.message || t("errors.genericText")}
        action={<Button onClick={reset}>{t("common.retry")}</Button>}
      />
    </div>
  );
}
