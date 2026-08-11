import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ThemeSelector from "./ThemeSelector";

describe("ThemeSelector", () => {
  it("offers light, dark, and automatic preferences", () => {
    const onChange = vi.fn();
    render(
      <ThemeSelector compact preference="auto" onChange={onChange} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Appearance: Auto" }));
    expect(screen.getByRole("menuitemradio", { name: "Light" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "Dark" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "Auto" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    fireEvent.click(screen.getByRole("menuitemradio", { name: "Dark" }));
    expect(onChange).toHaveBeenCalledWith("dark");
  });
});
