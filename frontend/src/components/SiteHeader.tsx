"use client";

import { useEffect, useState } from "react";
import { APP_BASE_PATH } from "@/utils/backend";

/**
 * The PolicyEngine site shell.
 *
 * /uk/chat is served on policyengine.org through a multizone rewrite, and the
 * rewrite does not inject the parent app's header into this response — every
 * zone child renders the shell itself. The app-zone-shell-audit in
 * policyengine-app-v2 asserts that the top 140px of the page carries the
 * PolicyEngine brand plus the Research, Model, API and Donate nav labels.
 *
 * Links are absolute so they resolve both here and on the standalone
 * deployment, where /uk/* belongs to the chat rather than the website.
 */
const SITE = "https://policyengine.org";

const NAV_LINKS = [
  { label: "Research", href: `${SITE}/uk/research` },
  { label: "Model", href: `${SITE}/uk/model` },
  { label: "API", href: `${SITE}/uk/api` },
  { label: "About", href: `${SITE}/uk/team` },
];

export default function SiteHeader() {
  // Embedded copies (?embed) are framed inside another page that already has
  // its own chrome, so the shell would be a duplicate header.
  const [isEmbed, setIsEmbed] = useState(false);

  useEffect(() => {
    const embedded = new URLSearchParams(window.location.search).has("embed");
    setIsEmbed(embedded);
    document.documentElement.style.setProperty(
      "--pe-shell-h",
      embedded ? "0px" : "",
    );
  }, []);

  if (isEmbed) return null;

  return (
    <header
      style={{
        height: "var(--pe-shell-h)",
        boxSizing: "border-box",
        position: "sticky",
        top: 0,
        zIndex: 100,
        background: "var(--bg)",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "16px",
        padding: "0 24px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          whiteSpace: "nowrap",
        }}
      >
        <a
          href={`${SITE}/uk`}
          aria-label="PolicyEngine"
          style={{ display: "block", textDecoration: "none" }}
        >
          <img
            src={`${APP_BASE_PATH}/policyengine-logo-square.png`}
            alt=""
            width={36}
            height={36}
            style={{ display: "block", borderRadius: "8px" }}
          />
        </a>
        <span
          aria-label="Beta"
          style={{
            display: "inline-flex",
            alignItems: "center",
            border: "1px solid transparent",
            borderRadius: "999px",
            padding: "4px 12px",
            background: "var(--primary)",
            color: "var(--primary-foreground)",
            fontSize: "12px",
            lineHeight: "16px",
            fontWeight: 500,
          }}
        >
          BETA
        </span>
      </div>

      <nav
        aria-label="PolicyEngine navigation"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "20px",
          fontSize: "14px",
          whiteSpace: "nowrap",
        }}
      >
        {NAV_LINKS.map((link) => (
          <a
            key={link.label}
            href={link.href}
            style={{ color: "var(--text-2)", textDecoration: "none" }}
          >
            {link.label}
          </a>
        ))}
        <a
          href={`${SITE}/uk/donate`}
          style={{
            color: "var(--text)",
            textDecoration: "none",
            border: "1px solid var(--border)",
            borderRadius: "6px",
            padding: "6px 14px",
          }}
        >
          Donate
        </a>
      </nav>
    </header>
  );
}
