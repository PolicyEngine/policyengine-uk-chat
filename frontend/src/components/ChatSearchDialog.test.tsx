import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ChatSearchDialog, { type ChatSearchResult } from "./ChatSearchDialog";

const RECENT: ChatSearchResult[] = [
  {
    id: 1,
    session_id: "session-1",
    title: "Recent tax question",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    snippet: null,
  },
];

describe("ChatSearchDialog", () => {
  it("searches message content and selects a result", async () => {
    const result: ChatSearchResult = {
      ...RECENT[0],
      title: "Child benefit",
      snippet: "The household receives child benefit each week.",
    };
    const search = vi.fn().mockResolvedValue([result]);
    const onSelect = vi.fn();

    render(
      <ChatSearchDialog
        open
        recent={RECENT}
        search={search}
        onClose={vi.fn()}
        onSelect={onSelect}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Search chats" })).toBeInTheDocument();
    const input = screen.getByRole("searchbox", { name: "Search chats" });
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "benefit" } });

    expect(await screen.findByText("Child benefit")).toBeInTheDocument();
    expect(screen.getByText(/household receives child benefit/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Child benefit/ }));

    expect(search).toHaveBeenCalledWith("benefit");
    expect(onSelect).toHaveBeenCalledWith(result);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <ChatSearchDialog
        open
        recent={RECENT}
        search={vi.fn()}
        onClose={onClose}
        onSelect={vi.fn()}
      />,
    );

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });
});
