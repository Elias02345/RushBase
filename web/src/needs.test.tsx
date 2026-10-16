import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MOCK_PASSWORD } from "./api/mocks/handlers";
import { createQueryClient } from "./api/queryClient";
import { AppProviders } from "./Providers";
import { createAppRouter } from "./router";
import { validateNeedsSearch } from "./routes/search";

async function signIn(path: string, username: string) {
  const client: QueryClient = createQueryClient();
  const router = createAppRouter(client, createMemoryHistory({ initialEntries: [path] }));
  await act(async () => {
    render(
      <AppProviders client={client}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
  });
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Benutzername"), username);
  await user.type(screen.getByLabelText("Passwort"), MOCK_PASSWORD);
  await user.click(screen.getByRole("button", { name: "Anmelden" }));
  return user;
}

describe("needs search validation", () => {
  it("keeps only known values", () => {
    expect(validateNeedsSearch({ list: "abc", state: "closed" })).toEqual({
      list: "abc",
      state: "closed",
    });
    expect(validateNeedsSearch({ list: "", state: "weird" })).toEqual({});
  });
});

describe("needs page", () => {
  it("shows a list with its automatic candidate and lets an editor confirm it", async () => {
    const user = await signIn("/needs", "elias");
    await user.click(await screen.findByRole("button", { name: /Imagefilm Hafen/ }));
    expect(await screen.findByText("Fischerboote bei Sonnenaufgang")).toBeInTheDocument();
    expect(screen.getByText(/automatisch/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Bestätigen" }));
    expect(await screen.findByText("1 von 1 bestätigt")).toBeInTheDocument();
  });

  it("offers the MCP administration with the connector URL", async () => {
    await signIn("/admin?tab=mcp", "elias");
    expect(await screen.findByText("https://media.example.org/mcp")).toBeInTheDocument();
    expect(screen.getByText("Claude")).toBeInTheDocument();
  });
});
