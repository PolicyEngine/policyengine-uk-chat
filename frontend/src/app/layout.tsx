import type { Metadata } from "next";
import Script from "next/script";
import "@mantine/core/styles.css";
import Providers from "./Providers";

const GA_MEASUREMENT_ID = "G-2YHG89FY0N";

export const metadata: Metadata = {
  title: "PolicyEngine UK",
  description: "UK tax and benefit microsimulation assistant",
  icons: { icon: "/favicon.svg" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <Script
          src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`}
          strategy="afterInteractive"
        />
        <Script id="google-analytics" strategy="afterInteractive">
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            window.gtag = gtag;
            gtag('js', new Date());
            gtag('config', '${GA_MEASUREMENT_ID}');
          `}
        </Script>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,300;0,8..60,400;0,8..60,600;1,8..60,400&family=Newsreader:ital,opsz,wght@0,6..72,300;0,6..72,400;0,6..72,500;1,6..72,400&display=swap" rel="stylesheet" />
      </head>
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", background: "#fafaf9" }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
