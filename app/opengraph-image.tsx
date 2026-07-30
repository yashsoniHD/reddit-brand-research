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
  "Reddit Content Ideas: turn what people actually say on Reddit into content you can publish";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const BRAND = "#2a78d6";
const INK = "#0b0b0b";
const SOFT = "#46443f";

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
          background: "#f7f7f5",
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
          <div
            style={{
              fontSize: 68,
              lineHeight: 1.1,
              fontWeight: 700,
              letterSpacing: -2,
              color: INK,
              display: "flex",
              flexWrap: "wrap",
            }}
          >
            Turn what people actually say on Reddit into&nbsp;
            <span style={{ color: BRAND }}>content</span>
            &nbsp;you can publish.
          </div>
          <div style={{ fontSize: 28, lineHeight: 1.45, color: SOFT }}>
            Communities verified against the archive. Numbers counted, not estimated.
            Every claim linked to its thread.
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
