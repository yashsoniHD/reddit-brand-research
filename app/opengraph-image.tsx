import { ImageResponse } from "next/og";

/**
 * The share card, drawn in code.
 *
 * No fonts are loaded and no assets are fetched: the renderer's built-in face
 * is used, so this route has no network dependency and cannot fail a build.
 * Colours are the light theme's, hard-coded because an OG image has no theme
 * to follow.
 */

export const alt =
  "Reddit Content Ideas: content ideas from real Reddit conversations";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const BRAND = "#2a78d6";
const INK = "#0b0b0b";
const SOFT = "#474b52";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#f7f8fa",
          padding: "72px 80px",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              background: BRAND,
              display: "flex",
            }}
          />
          <div
            style={{
              fontSize: 22,
              fontWeight: 600,
              letterSpacing: 2,
              color: SOFT,
              textTransform: "uppercase",
            }}
          >
            Reddit Content Ideas
          </div>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 28,
            maxWidth: 980,
          }}
        >
          {/*
            Weight and tracking mirror .t-display in globals.css (600,
            -0.032em, which is -2.18px at 68px). The site sets that tier in
            Inter and this card uses the renderer's built-in sans-serif,
            which is close enough in colour that matching the weight and
            tracking is what keeps the card from reading heavier than the
            page it advertises. No font is loaded here on purpose: it would
            add a network fetch to a route that must build with zero
            external dependencies.
          */}
          <div
            style={{
              fontSize: 68,
              lineHeight: 1.1,
              fontWeight: 600,
              letterSpacing: -2.18,
              color: INK,
              display: "flex",
              flexWrap: "wrap",
            }}
          >
            <span style={{ color: BRAND }}>Content ideas</span>
            &nbsp;from real Reddit conversations.
          </div>
          <div style={{ fontSize: 28, lineHeight: 1.45, color: SOFT }}>
            Every idea links to the thread it came from.
          </div>
        </div>

        <div style={{ display: "flex", gap: 12 }}>
          {["Cited evidence", "Public Reddit data", "Ready to publish"].map((label) => (
            <div
              key={label}
              style={{
                display: "flex",
                borderRadius: 999,
                border: "1px solid rgba(42,120,214,0.32)",
                background: "#e7effb",
                color: "#1f5fab",
                fontSize: 22,
                fontWeight: 600,
                padding: "10px 22px",
              }}
            >
              {label}
            </div>
          ))}
        </div>
      </div>
    ),
    size,
  );
}
