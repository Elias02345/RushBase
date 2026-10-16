import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button, Checkbox, Dialog, Drawer, Field, Input, useToast } from "@rushbase/ui";

import { cliData } from "../bridge";
import { useBridge } from "../bridge/context";
import { useBasket, useResolve } from "../state/library";

interface ResolveResult {
  project: string;
  imported: number;
  existing: number;
  markers_added: number;
  errors: string[];
}

const stamp = () => new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");

/** The selection basket: exports without scripting and "send to Resolve" for Studio. */
export function BasketPanel({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const bridge = useBridge();
  const toast = useToast();
  const { basket, remove, clear } = useBasket();
  const resolve = useResolve();
  const [confirm, setConfirm] = useState(false);
  const [binName, setBinName] = useState("Rushbase");
  const [markers, setMarkers] = useState(true);
  const [metadata, setMetadata] = useState(true);
  const [busy, setBusy] = useState(false);

  const exportAs = async (kind: "fcpxml" | "edl" | "csv") => {
    const dir = await bridge.pickDirectory();
    if (!dir) return;
    const sep = dir.includes("\\") && !dir.includes("/") ? "\\" : "/";
    const out = `${dir.replace(/[\\/]+$/, "")}${sep}rushbase-${stamp()}.${kind}`;
    try {
      const result = await cliData<{ file: string; warnings: string[] }>(bridge, [
        "export",
        kind,
        "--out",
        out,
      ]);
      toast.push({
        title: t("basket.exported", { file: result.file }),
        description: result.warnings.join("\n") || undefined,
      });
    } catch (cause) {
      toast.push({ title: t("basket.exportFailed"), description: String(cause), tone: "error" });
    }
  };

  const send = async () => {
    setBusy(true);
    try {
      const args = ["resolve", "send", "--yes", "--bin", binName.trim() || "Rushbase"];
      if (markers) args.push("--markers");
      if (metadata) args.push("--metadata");
      const result = await cliData<ResolveResult>(bridge, args);
      toast.push({
        title: t("basket.sent", {
          imported: result.imported,
          existing: result.existing,
          project: result.project,
        }),
      });
      setConfirm(false);
    } catch (cause) {
      toast.push({ title: t("basket.sendFailed"), description: String(cause), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const studio = resolve.data?.installed && resolve.data.reachable && resolve.data.studio;
  return (
    <>
      <Drawer
        open={open}
        onOpenChange={onOpenChange}
        title={t("basket.title", { count: basket.count })}
        closeLabel={t("common.close")}
      >
        {basket.count === 0 ? (
          <p className="dk-muted">{t("basket.empty")}</p>
        ) : (
          <ul className="dk-basket">
            {basket.items.map((item) => (
              <li key={item.asset_id}>
                <span>
                  <strong>{item.filename}</strong>
                  <span className="dk-muted">
                    {item.selected_shots
                      ? t("basket.shots", { count: item.selected_shots.length })
                      : t("basket.whole")}
                  </span>
                </span>
                <Button size="sm" variant="ghost" onClick={() => void remove(item.asset_id)}>
                  {t("common.remove")}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="dk-actions">
          <Button
            variant="secondary"
            disabled={basket.count === 0}
            onClick={() => void exportAs("fcpxml")}
          >
            FCPXML
          </Button>
          <Button
            variant="secondary"
            disabled={basket.count === 0}
            onClick={() => void exportAs("edl")}
          >
            EDL
          </Button>
          <Button
            variant="secondary"
            disabled={basket.count === 0}
            onClick={() => void exportAs("csv")}
          >
            {t("basket.csv")}
          </Button>
          {studio ? (
            <Button disabled={basket.count === 0} onClick={() => setConfirm(true)}>
              {t("basket.sendToResolve")}
            </Button>
          ) : null}
          <Button variant="ghost" disabled={basket.count === 0} onClick={() => void clear()}>
            {t("basket.clear")}
          </Button>
        </div>
        {!studio ? <p className="dk-muted">{t("basket.resolveHint")}</p> : null}
      </Drawer>
      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("basket.confirmTitle")}
        description={t("basket.confirmText", { project: resolve.data?.project ?? "?" })}
        closeLabel={t("common.close")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(false)}>
              {t("common.cancel")}
            </Button>
            <Button loading={busy} onClick={() => void send()}>
              {t("basket.confirmSend")}
            </Button>
          </>
        }
      >
        <Field label={t("basket.bin")}>
          {(c) => <Input {...c} value={binName} onChange={(e) => setBinName(e.target.value)} />}
        </Field>
        <Checkbox checked={markers} onCheckedChange={setMarkers} label={t("basket.markers")} />
        <Checkbox checked={metadata} onCheckedChange={setMetadata} label={t("basket.metadata")} />
      </Dialog>
    </>
  );
}
