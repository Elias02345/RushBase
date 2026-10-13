import { useTranslation } from "react-i18next";

import { HealthCard } from "./HealthCard";

export function HomePage() {
  const { t } = useTranslation();
  return (
    <>
      <section className="hero">
        <h1>{t("app.tagline")}</h1>
        <p>{t("app.intro")}</p>
      </section>
      <HealthCard />
    </>
  );
}
