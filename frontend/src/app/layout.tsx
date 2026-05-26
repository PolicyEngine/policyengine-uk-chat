import type { Metadata } from "next";
import "@mantine/core/styles.css";
import Providers from "./Providers";

export const metadata: Metadata = {
  title: "PolicyEngine UK",
  description: "UK tax and benefit microsimulation assistant",
  icons: { icon: "/favicon.svg" },
};

const themeVars = `
  :root {
    --bg: #ffffff;
    --surface: #ffffff;
    --surface-2: #f4f4f4;
    --surface-hover: #ececec;
    --border: #e5e5e5;
    --border-light: #f0f0f0;
    --text: #0d0d0d;
    --text-2: #2f2f2f;
    --text-3: #5d5d5d;
    --muted: #8e8e8e;
    --faint: #b4b4b4;
    --accent: #0d0d0d;
    --accent-fg: #ffffff;
    --accent-hover: #000000;
    --accent-15: rgba(13,13,13,0.08);
    --user-bubble: #f4f4f4;
    --code-bg: #1a1917;
    --code-text: #c9c5bc;
    --sidebar-bg: #f9f9f9;
    color-scheme: light;
  }
  :root[data-theme="dark"] {
    --bg: #212121;
    --surface: #2f2f2f;
    --surface-2: #3a3a3a;
    --surface-hover: #3a3a3a;
    --border: #4a4a4a;
    --border-light: #3a3a3a;
    --text: #ececec;
    --text-2: #d1d5db;
    --text-3: #b0b0b0;
    --muted: #9ca3af;
    --faint: #6b7280;
    --accent: #ececec;
    --accent-fg: #1f1f1f;
    --accent-hover: #ffffff;
    --accent-15: rgba(236,236,236,0.10);
    --user-bubble: #2f2f2f;
    --code-bg: #1a1917;
    --code-text: #c9c5bc;
    --sidebar-bg: #171717;
    color-scheme: dark;
  }
  body { background: var(--bg); color: var(--text); }
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,300;0,8..60,400;0,8..60,600;1,8..60,400&family=Newsreader:ital,opsz,wght@0,6..72,300;0,6..72,400;0,6..72,500;1,6..72,400&display=swap" rel="stylesheet" />
        <style dangerouslySetInnerHTML={{ __html: themeVars }} />
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('theme');if(t==='dark'){document.documentElement.setAttribute('data-theme','dark');}}catch(e){}})();`,
          }}
        />
      </head>
      <body style={{ margin: 0, fontFamily: "system-ui, -apple-system, sans-serif", background: "var(--bg)", color: "var(--text)" }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
