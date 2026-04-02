"use client";

import { MantineProvider } from "@mantine/core";
import { AuthProvider } from "@/utils/AuthContext";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MantineProvider defaultColorScheme="light">
      <AuthProvider>{children}</AuthProvider>
    </MantineProvider>
  );
}
