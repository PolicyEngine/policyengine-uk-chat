import type { Metadata } from "next";
import "@mantine/core/styles.css";
import Providers from "./Providers";

export const metadata: Metadata = {
  title: "UK Policy Assistant",
  description: "UK tax and benefit microsimulation assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head />
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", background: "#fafaf9" }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
