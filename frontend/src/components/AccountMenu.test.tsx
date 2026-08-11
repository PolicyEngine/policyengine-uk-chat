import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AccountMenu from "./AccountMenu";

describe("AccountMenu", () => {
  it("opens settings without logging out, then logs out explicitly", () => {
    const onSignOut = vi.fn();
    render(
      <AccountMenu
        compact
        email="person@example.com"
        onSignOut={onSignOut}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Open account settings" });
    expect(trigger).not.toHaveAttribute("data-tip");
    expect(trigger).not.toHaveAttribute("title");

    fireEvent.click(trigger);
    expect(onSignOut).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("person@example.com")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: "Log out" }));
    expect(onSignOut).toHaveBeenCalledOnce();
  });
});
