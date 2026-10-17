import {
  Button,
  Card,
  DataTable,
  EmptyState,
  Field,
  Input,
  useToast,
  type DataColumn,
} from "@rushbase/ui";
import { Undo2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { useLibrarySettings, useSaveLibrarySettings } from "../../api/queries";
import { useRestoreTrashed, useTrash, type TrashedItem } from "../../api/safetyQueries";
import { formatBytes, formatDateTime } from "../../lib/format";

function RetentionForm({ days }: { days: number }) {
  const { t } = useTranslation();
  const toast = useToast();
  const settings = useLibrarySettings().data;
  const save = useSaveLibrarySettings();
  const [value, setValue] = useState(String(days));
  return (
    <form
      className="inline-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!settings) return;
        save.mutate(
          { ...settings, trash_retention_days: Number(value) },
          { onSuccess: () => toast.push({ title: t("common.saved"), tone: "success" }) },
        );
      }}
    >
      <Field label={t("trash.retention")} hint={t("trash.retentionHint")}>
        {(c) => (
          <Input
            {...c}
            type="number"
            min={1}
            max={3650}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        )}
      </Field>
      <Button type="submit" loading={save.isPending} disabled={!settings}>
        {t("common.save")}
      </Button>
    </form>
  );
}

/** The trash: restore or wait for the retention period to pass. */
export function TrashAdmin() {
  const { t } = useTranslation();
  const toast = useToast();
  const trash = useTrash();
  const restore = useRestoreTrashed();
  const columns: DataColumn<TrashedItem>[] = [
    {
      key: "name",
      header: t("trash.col.name"),
      width: "minmax(0, 2fr)",
      cell: (i) => (
        <span>
          <strong>{i.filename}</strong>{" "}
          <span className="muted">
            {t(`assetStatus.${i.prior_status}`, { defaultValue: i.prior_status })}
          </span>
        </span>
      ),
    },
    {
      key: "size",
      header: t("library.col.size"),
      width: "100px",
      align: "end",
      cell: (i) => formatBytes(i.size_total),
    },
    {
      key: "trashed",
      header: t("trash.col.trashed"),
      width: "170px",
      cell: (i) => formatDateTime(i.trashed_at),
    },
    {
      key: "expires",
      header: t("trash.col.expires"),
      width: "170px",
      cell: (i) => formatDateTime(i.expires_at),
    },
    {
      key: "actions",
      header: "",
      width: "140px",
      align: "end",
      cell: (i) => (
        <Button
          size="sm"
          variant="secondary"
          icon={<Undo2 size={14} />}
          loading={restore.isPending && restore.variables === i.asset_id}
          onClick={() =>
            restore.mutate(i.asset_id, {
              onSuccess: () => toast.push({ title: t("trash.restored"), tone: "success" }),
              onError: (error) =>
                toast.push({
                  title: t("trash.restoreFailed"),
                  description: error instanceof ApiError ? error.message : undefined,
                  tone: "error",
                }),
            })
          }
        >
          {t("trash.restore")}
        </Button>
      ),
    },
  ];
  return (
    <>
      <p className="muted">{t("trash.lead")}</p>
      {trash.data ? (
        <RetentionForm key={trash.data.retention_days} days={trash.data.retention_days} />
      ) : null}
      <Card flush>
        <DataTable
          label={t("trash.title")}
          rows={trash.data?.items ?? []}
          columns={columns}
          getRowId={(i) => i.asset_id}
          rowHeight={56}
          empty={<EmptyState title={t("trash.emptyTitle")} description={t("trash.emptyText")} />}
        />
      </Card>
    </>
  );
}
