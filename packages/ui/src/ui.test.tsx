import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  AssetTile,
  Badge,
  Button,
  Card,
  Checkbox,
  CommandPalette,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  Input,
  ProgressRing,
  SearchField,
  StatusChip,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  ThemeProvider,
  ToastProvider,
  useTheme,
  useToast,
} from "./index";

describe("Button", () => {
  it("defaults to type=button and fires onClick", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    const button = screen.getByRole("button", { name: "Go" });
    expect(button).toHaveAttribute("type", "button");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("applies the variant class", () => {
    render(<Button variant="ghost">Quiet</Button>);
    expect(screen.getByRole("button")).toHaveClass("rb-button--ghost");
  });

  it("is disabled and busy while loading", () => {
    render(<Button loading>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });
});

describe("Card, Badge, StatusChip, EmptyState", () => {
  it("labels the card by its title", () => {
    render(
      <Card title="Server">
        <Badge tone="success">ok</Badge>
      </Card>,
    );
    expect(screen.getByRole("region", { name: "Server" })).toBeInTheDocument();
    expect(screen.getByText("ok")).toHaveClass("rb-badge--success");
  });

  it("names the state in text and tone class", () => {
    render(<StatusChip tone="processing">Processing</StatusChip>);
    expect(screen.getByText("Processing")).toHaveClass("rb-chip--processing");
  });

  it("renders title, description and action", () => {
    render(
      <EmptyState title="Nothing yet" description="Add material" action={<button>Go</button>} />,
    );
    expect(screen.getByRole("heading", { name: "Nothing yet" })).toBeInTheDocument();
    expect(screen.getByText("Add material")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument();
  });
});

describe("ProgressRing", () => {
  it("exposes progress to assistive technology", () => {
    render(<ProgressRing value={42.4} label="Import" />);
    const ring = screen.getByRole("progressbar", { name: "Import" });
    expect(ring).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByText("42%")).toBeInTheDocument();
  });

  it("clamps out-of-range values", () => {
    render(<ProgressRing value={250} label="Over" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("form controls", () => {
  it("wires label, hint and error to the input", () => {
    render(
      <Field label="Name" hint="Shown to others" error="Required">
        {({ invalid, ...control }) => <Input invalid={invalid} {...control} />}
      </Field>,
    );
    const input = screen.getByLabelText("Name");
    expect(input).toHaveAccessibleDescription("Shown to others Required");
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("clears a search field", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [value, setValue] = useState("rain");
      return (
        <SearchField
          value={value}
          onValueChange={setValue}
          clearLabel="Clear"
          aria-label="Search"
        />
      );
    }
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByRole("searchbox", { name: "Search" })).toHaveValue("");
  });

  it("toggles a checkbox", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Checkbox checked={false} onCheckedChange={onChange} label="Video" />);
    await user.click(screen.getByRole("checkbox", { name: "Video" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe("Dialog and Tabs", () => {
  it("opens with a name and closes with Escape", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <Dialog open onOpenChange={onOpenChange} title="Revoke token" closeLabel="Close">
        Sure?
      </Dialog>,
    );
    expect(screen.getByRole("dialog", { name: "Revoke token" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("switches panels with the arrow keys", async () => {
    const user = userEvent.setup();
    render(
      <Tabs defaultValue="a">
        <TabsList aria-label="Sections">
          <TabsTrigger value="a">Alpha</TabsTrigger>
          <TabsTrigger value="b">Beta</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Panel A</TabsContent>
        <TabsContent value="b">Panel B</TabsContent>
      </Tabs>,
    );
    await user.click(screen.getByRole("tab", { name: "Alpha" }));
    await user.keyboard("{ArrowRight}");
    expect(await screen.findByText("Panel B")).toBeInTheDocument();
  });
});

describe("Toast", () => {
  it("shows pushed messages", async () => {
    const user = userEvent.setup();
    function Trigger() {
      const { push } = useToast();
      return <button onClick={() => push({ title: "Copied", tone: "success" })}>go</button>;
    }
    render(
      <ToastProvider closeLabel="Close" regionLabel="Notifications">
        <Trigger />
      </ToastProvider>,
    );
    await user.click(screen.getByText("go"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });
});

describe("DataTable", () => {
  const rows = Array.from({ length: 2000 }, (_, index) => ({
    id: String(index),
    name: `Row ${index}`,
  }));

  it("renders only a window of rows and reports the full row count", () => {
    render(
      <DataTable
        label="Rows"
        rows={rows}
        getRowId={(row) => row.id}
        columns={[{ key: "name", header: "Name", cell: (row) => row.name }]}
      />,
    );
    expect(screen.getByRole("table", { name: "Rows" })).toHaveAttribute("aria-rowcount", "2001");
    const rendered = screen.getAllByRole("row").length;
    expect(rendered).toBeGreaterThan(5);
    expect(rendered).toBeLessThan(60);
  });

  it("opens a row with Enter", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    render(
      <DataTable
        label="Rows"
        rows={rows.slice(0, 3)}
        getRowId={(row) => row.id}
        onRowClick={onRowClick}
        columns={[{ key: "name", header: "Name", cell: (row) => row.name }]}
      />,
    );
    const first = screen.getByRole("row", { name: /Row 0/ });
    first.focus();
    await user.keyboard("{Enter}");
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });
});

describe("AssetTile", () => {
  const sprite = { url: "/s.jpg", cols: 5, rows: 2, count: 10 };

  it("scrubs through the sprite as the pointer moves", () => {
    render(
      <AssetTile
        title="Beach"
        media="video"
        poster="/p.jpg"
        sprite={sprite}
        selectLabel="Select"
        onOpen={() => undefined}
      />,
    );
    const media = screen.getByRole("button", { name: "Beach" });
    media.getBoundingClientRect = () => ({ left: 0, width: 100, top: 0, height: 56 }) as DOMRect;
    fireEvent.pointerMove(media, { clientX: 95, pointerType: "mouse" });
    const layer = media.querySelector<HTMLElement>(".rb-tile__sprite");
    expect(layer).not.toBeNull();
    // Frame 9 of 10 sits in the last column of the second row.
    expect(layer?.style.backgroundPosition).toBe("100% 100%");
    fireEvent.pointerLeave(media);
    expect(media.querySelector(".rb-tile__sprite")).toBeNull();
  });

  it("opens on click and selects on modifier click and Space", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onToggle = vi.fn();
    render(
      <AssetTile
        title="Beach"
        media="video"
        poster={null}
        selectLabel="Select"
        onOpen={onOpen}
        onToggleSelect={onToggle}
      />,
    );
    const media = screen.getByRole("button", { name: "Beach" });
    await user.click(media);
    expect(onOpen).toHaveBeenCalledOnce();
    await user.keyboard("{Control>}");
    await user.click(media);
    await user.keyboard("{/Control}");
    expect(onToggle).toHaveBeenLastCalledWith({ shift: false, additive: true });
    media.focus();
    await user.keyboard(" ");
    expect(onToggle).toHaveBeenCalledTimes(2);
    expect(onOpen).toHaveBeenCalledOnce();
  });
});

describe("CommandPalette", () => {
  it("filters and runs a command", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <CommandPalette
        open
        onOpenChange={onOpenChange}
        label="Commands"
        placeholder="Type a command"
        emptyLabel="No match"
        groups={[
          {
            heading: "Go to",
            items: [
              { id: "lib", label: "Library", onSelect },
              { id: "adm", label: "Admin", onSelect: () => undefined },
            ],
          },
        ]}
      />,
    );
    await user.type(screen.getByPlaceholderText("Type a command"), "libr");
    const list = screen.getByRole("listbox");
    expect(within(list).queryByText("Admin")).not.toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("ThemeProvider", () => {
  function Toggle() {
    const { theme, preference, toggleTheme, setPreference } = useTheme();
    return (
      <>
        <button onClick={toggleTheme}>{theme}</button>
        <button onClick={() => setPreference("system")}>system:{preference}</button>
      </>
    );
  }

  it("mirrors an explicit choice to <html> and persists it", () => {
    window.localStorage.setItem("rushbase.theme", "light");
    render(
      <ThemeProvider>
        <Toggle />
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(screen.getByRole("button", { name: "light" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("rushbase.theme")).toBe("dark");
  });

  it("returns to following the system", () => {
    window.localStorage.setItem("rushbase.theme", "dark");
    render(
      <ThemeProvider>
        <Toggle />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /system:/ }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(window.localStorage.getItem("rushbase.theme")).toBeNull();
  });

  it("throws outside a provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const swallow = (event: ErrorEvent) => event.preventDefault(); // jsdom re-reports render errors
    window.addEventListener("error", swallow);
    expect(() => render(<Toggle />)).toThrow(/ThemeProvider/);
    window.removeEventListener("error", swallow);
    spy.mockRestore();
  });
});
