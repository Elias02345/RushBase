import { Button, Input } from "@rushbase/ui";
import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Facet } from "../../api/contracts";
import { formatNumber } from "../../lib/format";
import { isEnumKey, isTagKey, valueLabel } from "../../lib/facets";
import type { LibrarySearch } from "../../routes/search";

const VISIBLE = 6;

function EnumFacet({
  facet,
  label,
  active,
  onToggle,
}: {
  facet: Facet;
  label: string;
  active: readonly string[];
  onToggle: (value: string, selected: boolean) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const [all, setAll] = useState(false);
  const values = facet.values ?? [];
  if (values.length === 0) return null;
  const shown = all ? values : values.slice(0, VISIBLE);
  const panelId = `facet-${facet.key}`;
  return (
    <section className="facet">
      <h3 className="facet__title">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen(!open)}
        >
          {label}
          <ChevronDown size={16} aria-hidden="true" className={open ? "" : "facet__chev--closed"} />
        </button>
      </h3>
      {open ? (
        <ul id={panelId} className="facet__list">
          {shown.map((v) => {
            const selected = active.includes(v.value);
            return (
              <li key={v.value}>
                <button
                  type="button"
                  className="facet__value"
                  aria-pressed={selected}
                  onClick={() => onToggle(v.value, selected)}
                >
                  <span className="facet__name">{valueLabel(t, facet.key, v.value)}</span>
                  <span className="facet__count">{formatNumber(v.count)}</span>
                </button>
              </li>
            );
          })}
          {values.length > VISIBLE ? (
            <li>
              <Button variant="ghost" size="sm" onClick={() => setAll(!all)}>
                {all
                  ? t("library.showLess")
                  : t("library.showMore", { count: values.length - VISIBLE })}
              </Button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </section>
  );
}

export function FacetSidebar({
  facets,
  search,
  language,
  onChange,
}: {
  facets: Facet[];
  search: LibrarySearch;
  language: string;
  onChange: (patch: Partial<LibrarySearch>) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="facets">
      {facets.map((facet) => {
        const label = language === "en" ? facet.label_en : facet.label_de;
        if (facet.kind === "enum" && isEnumKey(facet.key)) {
          return (
            <EnumFacet
              key={facet.key}
              facet={facet}
              label={label}
              active={search[facet.key] ? [String(search[facet.key])] : []}
              onToggle={(value, selected) =>
                onChange({ [facet.key]: selected ? undefined : value } as Partial<LibrarySearch>)
              }
            />
          );
        }
        if (facet.kind === "enum" && isTagKey(facet.key)) {
          const current = search.tag ?? [];
          return (
            <EnumFacet
              key={facet.key}
              facet={facet}
              label={label}
              active={current}
              onToggle={(value, selected) => {
                const next = selected ? current.filter((v) => v !== value) : [...current, value];
                onChange({ tag: next.length > 0 ? next : undefined });
              }}
            />
          );
        }
        if (facet.kind === "date") {
          return (
            <section className="facet" key={facet.key}>
              <h3 className="facet__title facet__title--static">{label}</h3>
              <div className="facet__dates">
                <label>
                  <span>{t("library.from")}</span>
                  <Input
                    type="date"
                    value={search.date_from ?? ""}
                    min={typeof facet.min === "string" ? facet.min.slice(0, 10) : undefined}
                    max={
                      search.date_to ??
                      (typeof facet.max === "string" ? facet.max.slice(0, 10) : undefined)
                    }
                    onChange={(e) => onChange({ date_from: e.target.value || undefined })}
                  />
                </label>
                <label>
                  <span>{t("library.to")}</span>
                  <Input
                    type="date"
                    value={search.date_to ?? ""}
                    min={search.date_from}
                    onChange={(e) => onChange({ date_to: e.target.value || undefined })}
                  />
                </label>
              </div>
            </section>
          );
        }
        return null;
      })}
    </div>
  );
}
