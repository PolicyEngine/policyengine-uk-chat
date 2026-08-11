import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AuthDialog from "./AuthDialog";

describe("AuthDialog", () => {
  it("shows email-verification instructions after confirmation signup", async () => {
    const onClose = vi.fn();
    render(
      <AuthDialog
        open
        onClose={onClose}
        signIn={vi.fn()}
        signUp={vi.fn().mockResolvedValue({
          error: null,
          requiresEmailConfirmation: true,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create one" }));
    fireEvent.change(screen.getByPlaceholderText("Email"), {
      target: { value: "person@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(screen.getByText(/person@example\.com/)).toBeInTheDocument();
    expect(screen.getByText(/verification link/)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes after an immediately authenticated signup", async () => {
    const onClose = vi.fn();
    render(
      <AuthDialog
        open
        onClose={onClose}
        signIn={vi.fn()}
        signUp={vi.fn().mockResolvedValue({
          error: null,
          requiresEmailConfirmation: false,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create one" }));
    fireEvent.change(screen.getByPlaceholderText("Email"), {
      target: { value: "person@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });
});
