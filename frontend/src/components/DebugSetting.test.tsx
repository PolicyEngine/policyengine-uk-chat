import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DebugSetting from "./DebugSetting";


describe("DebugSetting", () => {
  it("shows the stored state and requests its inverse", () => {
    const onChange = vi.fn();
    render(
      <DebugSetting
        compact={false}
        enabled
        onChange={onChange}
      />,
    );

    const setting = screen.getByRole("button", { name: "Debug activity" });
    expect(setting).toHaveAttribute("aria-pressed", "true");
    expect(setting).toHaveTextContent("Debug activity: On");
    fireEvent.click(setting);
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("provides compact state text without allowing a disabled change", () => {
    const onChange = vi.fn();
    render(
      <DebugSetting
        compact
        enabled={false}
        disabled
        onChange={onChange}
      />,
    );

    const setting = screen.getByRole("button", { name: "Debug activity" });
    expect(setting).toHaveAttribute("data-tip-right", "Debug activity: Off");
    fireEvent.click(setting);
    expect(onChange).not.toHaveBeenCalled();
  });
});
