import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Badge, Button, Card, ProgressRing, ThemeProvider, useTheme } from "./index";

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
});

describe("Card and Badge", () => {
  it("renders the title and content", () => {
    render(
      <Card title="Server">
        <Badge tone="success">ok</Badge>
      </Card>,
    );
    expect(screen.getByRole("heading", { name: "Server" })).toBeInTheDocument();
    expect(screen.getByText("ok")).toHaveClass("rb-badge--success");
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

describe("ThemeProvider", () => {
  function Toggle() {
    const { theme, toggleTheme } = useTheme();
    return <button onClick={toggleTheme}>{theme}</button>;
  }

  it("mirrors the theme to <html> and persists it", () => {
    window.localStorage.setItem("rushbase.theme", "light");
    render(
      <ThemeProvider>
        <Toggle />
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(screen.getByRole("button"));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("rushbase.theme")).toBe("dark");
  });

  it("throws outside a provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<Toggle />)).toThrow(/ThemeProvider/);
    spy.mockRestore();
  });
});
