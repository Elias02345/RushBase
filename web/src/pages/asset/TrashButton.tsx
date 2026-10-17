import { Button, Dialog, useToast } from "@rushbase/ui";
import { useNavigate } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AssetDetail } from "../../api/contracts";
import { ApiError } from "../../api/client";
import { useTrashAsset } from "../../api/safetyQueries";

/** Administrators: move the asset (or a duplicate) to the trash after a confirmation dialog. */
export function TrashButton({ detail }: { detail: AssetDetail }) {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const trash = useTrashAsset();
  const [open, setOpen] = useState(false);
  const duplicate = detail.status === "duplicate";
  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        icon={<Trash2 size={14} />}
        onClick={() => setOpen(true)}
      >
        {duplicate ? t("trash.moveDuplicate") : t("trash.move")}
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={t("trash.confirmTitle")}
        description={t("trash.confirmText", { name: detail.title ?? detail.filename })}
        closeLabel={t("common.close")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              loading={trash.isPending}
              onClick={() =>
                trash.mutate(detail.id, {
                  onSuccess: () => {
                    setOpen(false);
                    toast.push({ title: t("trash.done"), tone: "success" });
                    void navigate({ to: "/admin", search: { tab: "trash" } });
                  },
                  onError: (error) =>
                    toast.push({
                      title: error instanceof ApiError ? error.message : t("common.error"),
                      tone: "error",
                    }),
                })
              }
            >
              {t("trash.confirm")}
            </Button>
          </>
        }
      >
        <p className="muted">{t("trash.confirmHint")}</p>
      </Dialog>
    </>
  );
}
